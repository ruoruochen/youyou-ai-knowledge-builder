"""Daily navigation and explicit task/decision lifecycle for the Markdown vault."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OWNED = ['项目', '资产', '技能与模板']
BASE = '项目/每日流'
MARK_START = '<!-- dailyflow:start -->'
MARK_END = '<!-- dailyflow:end -->'
STATES = {
    'pending_decision': '待决定', 'todo': '待执行', 'in_progress': '进行中',
    'done': '已完成', 'cancelled': '已取消',
}
KINDS = {'knowledge', 'decision', 'task'}
READ_OPS = {'today', 'daily', 'queue', 'reminder_scan', 'reminder_settings'}
WRITE_OPS = {'refresh_views', 'reminder_ack', 'reminder_configure'}
DEFAULTS = {'enabled': True, 'stale_hours': 48, 'timezone': 'UTC'}
TIMEZONE = 'UTC'
REVIEW_TIME = '20:30'

def configure(timezone='UTC', review_time='20:30'):
    global TIMEZONE, REVIEW_TIME
    ZoneInfo(timezone)
    datetime.strptime(review_time, '%H:%M')
    TIMEZONE, REVIEW_TIME = timezone, review_time
    DEFAULTS['timezone'] = timezone


def current():
    return datetime.now(ZoneInfo(TIMEZONE))


def stamp(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        result = value
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
        result = datetime.fromisoformat(str(value) + 'T' + REVIEW_TIME + ':00')
    else:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=ZoneInfo(TIMEZONE))
    return result.astimezone(ZoneInfo(TIMEZONE))


def date_value(value):
    result = datetime.strptime(str(value), '%Y-%m-%d').date()
    return result.isoformat()


def validate_fields(kind, fields, existing=None):
    if kind not in KINDS:
        raise ValueError('类型必须是knowledge、decision或task')
    merged = dict(existing or {})
    merged.update(fields)
    state = merged.get('task_state')
    if kind in ('decision', 'task'):
        if state not in STATES:
            raise ValueError('待处理事项状态无效')
    elif state is not None or merged.get('due_at') or merged.get('review_at'):
        raise ValueError('普通知识不能设置待办状态或期限，请显式设为task或decision')
    for key in ('due_at', 'review_at'):
        if merged.get(key):
            stamp(merged[key])
    if 'reminder_enabled' in merged and not isinstance(merged['reminder_enabled'], bool):
        raise ValueError('reminder_enabled必须是布尔值')


def settings(v):
    result = dict(DEFAULTS)
    p = state_path(v, 'settings.json')
    if p.exists():
        result.update(json.loads(p.read_text(encoding='utf-8')))
    result['timezone'] = TIMEZONE
    result['review_time'] = REVIEW_TIME
    return result


def state_path(v, name):
    root = v.root / '.dailyflow'
    p = root / name
    if root.is_symlink() or p.is_symlink():
        raise ValueError('流程状态目录不允许符号链接')
    return p


def write_state(v, name, data):
    import os
    import uuid
    p = state_path(v, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_name('.' + name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, p)
    finally:
        if temp.exists():
            temp.unlink()


def authored(v, archived=False):
    return [n for n in v.notes(OWNED)
            if archived or n['metadata'].get('status') != 'archived']


def items(v, archived=False, closed=False):
    result = []
    for n in authored(v, archived):
        m = n['metadata']
        if m.get('kind') not in ('task', 'decision'):
            continue
        if not closed and m.get('task_state') in ('done', 'cancelled'):
            continue
        result.append(n)
    return result


def brief(n):
    m = n['metadata']
    return {
        'path': n['path'], 'revision': n['revision'],
        'title': m.get('title', Path(n['path']).stem),
        'kind': m.get('kind', 'knowledge'), 'task_state': m.get('task_state'),
        'due_at': m.get('due_at'), 'review_at': m.get('review_at'),
        'status': m.get('status', 'active'),
    }


def daily_records(v, day):
    day = date_value(day)
    result = []
    for n in authored(v):
        m = n['metadata']
        created, updated = stamp(m.get('created')), stamp(m.get('updated'))
        if (created and created.date().isoformat() == day) or (updated and updated.date().isoformat() == day):
            r = brief(n)
            r['event'] = '新增' if created and created.date().isoformat() == day else '更新'
            result.append(r)
    return result


def link(n):
    title = str(n.get('title', Path(n['path']).stem)).replace('|', '｜').replace(']', '）').replace('[', '（')
    return '[[' + n['path'][:-3] + '|' + title + ']]'


def queue_lines(records):
    if not records:
        return ['暂无待处理事项。']
    lines = []
    for r in records:
        extras = []
        if r.get('review_at'):
            extras.append('复查：' + str(r['review_at']))
        if r.get('due_at'):
            extras.append('期限：' + str(r['due_at']))
        lines.append('- ' + link(r) + ' · ' + STATES.get(r.get('task_state'), '待核对')
                     + (' · ' + '；'.join(extras) if extras else ''))
    return lines


def managed(v, path, title, body):
    p = v.path(path)
    block = MARK_START + '\n' + body.strip() + '\n' + MARK_END
    if p.exists():
        original = p.read_bytes().decode('utf-8')
        if original.count(MARK_START) != 1 or original.count(MARK_END) != 1:
            raise ValueError('导航文件缺少唯一维护标记，不会覆盖手写内容：' + path)
        start, end = original.index(MARK_START), original.index(MARK_END) + len(MARK_END)
        if end < start:
            raise ValueError('导航文件维护标记顺序错误：' + path)
        text = original[:start] + block + original[end:]
        if text == original:
            return False
        from bridge import digest
        v.write(path, text, digest(original))
    else:
        text = v.render({'title': title, 'kind': 'navigation', 'status': 'active'},
                        '# ' + title + '\n\n> 这里只汇总已经保存的个人内容；修改原笔记即可更新索引。维护区外可以添加手写备注。\n\n' + block)
        v.write(path, text)
    return True


def refresh(v, today=None):
    day = date_value(today or current().date().isoformat())
    notes = authored(v)
    days = {day}
    for n in notes:
        for key in ('created', 'updated'):
            dt = stamp(n['metadata'].get(key))
            if dt:
                days.add(dt.date().isoformat())
    directory = v.root / BASE / '日报'
    if directory.is_symlink():
        raise ValueError('日报目录不允许符号链接')
    if directory.exists():
        for p in directory.glob('*.md'):
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.stem):
                days.add(p.stem)
    changed = []
    for d in sorted(days):
        rows = daily_records(v, d)
        body = ['本页是已保存内容的日期导航，不是对未保存聊天的自动总结。', '']
        body += ['- ' + link(r) + ' · ' + r['event'] for r in rows] or ['这一天没有已保存的个人内容。']
        path = BASE + '/日报/' + d + '.md'
        if managed(v, path, d + ' 日报', '\n'.join(body)):
            changed.append(path)
    queue = [brief(n) for n in items(v)]
    body = ['## 待定决策', ''] + queue_lines([x for x in queue if x['task_state'] == 'pending_decision'])
    body += ['', '## 待执行／进行中', ''] + queue_lines([x for x in queue if x['task_state'] != 'pending_decision'])
    path = BASE + '/待处理事项.md'
    if managed(v, path, '待处理事项', '\n'.join(body)):
        changed.append(path)
    todays = daily_records(v, day)
    body = ['日期：' + day, '', '[[项目/每日流/日报/' + day + '|' + day + ' 日报]] · [[项目/每日流/待处理事项|待处理事项]]',
            '', '## 今天保存或更新的内容', '']
    body += ['- ' + link(r) + ' · ' + r['event'] for r in todays] or ['今天还没有已保存的个人内容。']
    body += ['', '## 继续处理', ''] + queue_lines(queue)
    path = BASE + '/今日.md'
    if managed(v, path, '今日', '\n'.join(body)):
        changed.append(path)
    return {'date': day, 'changed': changed, 'open_items': len(queue), 'saved_today': len(todays)}


def scan(v, at=None):
    config = settings(v)
    moment = stamp(at) if at else current()
    if not config['enabled']:
        return {'enabled': False, 'alerts': []}
    ledger_path = state_path(v, 'reminders.json')
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
    alerts = []
    from bridge import digest
    for n in items(v):
        m = n['metadata']
        if m.get('reminder_enabled', True) is False:
            continue
        # Explicit future review dates suppress the generic 48-hour nudge.
        due, review = stamp(m.get('due_at')), stamp(m.get('review_at'))
        updated = stamp(m.get('updated')) or stamp(m.get('created'))
        reason = None
        if due and due <= moment:
            reason = '已到期限' if m.get('task_state') != 'pending_decision' else '已到决定日期'
        elif review and review <= moment:
            reason = '已到复查时间'
        elif not due and not review and updated and updated + timedelta(hours=config['stale_hours']) <= moment:
            reason = '保存或更新后已满' + str(config['stale_hours']) + '小时，尚未处理'
        if not reason:
            continue
        key = str(m.get('id') or n['path'])
        fingerprint = digest(json.dumps([key, n['revision'], reason], ensure_ascii=False))
        if ledger.get(key, {}).get('fingerprint') == fingerprint:
            continue
        r = brief(n)
        r.update(id=key, reason=reason, fingerprint=fingerprint)
        alerts.append(r)
    return {'enabled': True, 'checked_at': moment.isoformat(), 'alerts': alerts}


def run(v, q):
    op = q['operation']
    if op == 'today':
        day = current().date().isoformat()
        return {'date': day, 'saved': daily_records(v, day), 'queue': [brief(n) for n in items(v)]}
    if op == 'daily':
        day = date_value(q.get('date', current().date().isoformat()))
        return {'date': day, 'items': daily_records(v, day)}
    if op == 'queue':
        return {'items': [brief(n) for n in items(v, bool(q.get('include_archived')), bool(q.get('include_closed')))]}
    if op == 'reminder_scan':
        return scan(v)
    if op == 'reminder_settings':
        return settings(v)
    if not q.get('user_instruction', '').strip():
        raise ValueError('流程写入需要明确的用户指令或已授权维护说明')
    if op == 'refresh_views':
        if q.get('authorization') != 'maintenance':
            raise ValueError('缺少已授权导航维护标记')
        return refresh(v)
    if op == 'reminder_configure':
        if q.get('authorization') != 'update':
            raise ValueError('缺少修改提醒设置授权')
        delta = q.get('settings', {})
        if not isinstance(delta, dict) or set(delta) - {'enabled', 'stale_hours'}:
            raise ValueError('只允许修改enabled与stale_hours；实际检查时间通过当前Agent的调度工具调整')
        updated = settings(v)
        updated.update(delta)
        if not isinstance(updated['enabled'], bool) or type(updated['stale_hours']) is not int or not 1 <= updated['stale_hours'] <= 8760:
            raise ValueError('提醒参数无效')
        write_state(v, 'settings.json', updated)
        return updated
    if op == 'reminder_ack':
        if q.get('authorization') != 'maintenance':
            raise ValueError('缺少提醒处理标记')
        pending = {x['fingerprint']: x for x in scan(v)['alerts']}
        requested = q.get('fingerprints', [])
        if not isinstance(requested, list) or not requested or any(x not in pending for x in requested):
            raise ValueError('提醒已变化、已处理或指纹无效，请重新检查')
        p = state_path(v, 'reminders.json')
        ledger = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        for token in requested:
            row = pending[token]
            ledger[row['id']] = {'fingerprint': token, 'prepared_at': current().isoformat(), 'path': row['path']}
        write_state(v, 'reminders.json', ledger)
        return {'prepared': len(requested), 'meaning': '已准备本轮提醒，不代表操作系统通知已送达'}
    raise ValueError('未知流程操作')
