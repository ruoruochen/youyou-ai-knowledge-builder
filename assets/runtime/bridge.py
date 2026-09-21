#!/usr/bin/env python3
"""Local Markdown vault operations. JSON request on stdin, JSON receipt on stdout."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
from datetime import datetime, timezone
import yaml
import dailyflow

ROOT = Path(__file__).resolve().parent
CATEGORIES = ['灵感库', '项目', '资产', '资源', '归档', '技能与模板']
OWNED_CATEGORIES = ['项目', '资产', '技能与模板']
EXTERNAL_CATEGORIES = ['资源', '灵感库']
SCOPES = {'owned': OWNED_CATEGORIES, 'external': EXTERNAL_CATEGORIES, 'all': CATEGORIES}

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

class Vault:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, relative):
        if not isinstance(relative, str) or not relative:
            raise ValueError('笔记路径不能为空')
        parts = Path(relative).parts
        if Path(relative).is_absolute() or any(x.startswith('.') for x in parts):
            raise ValueError('路径必须是知识库内的相对路径，不能包含隐藏目录或上级路径')
        p = self.root / relative
        if any(x.is_symlink() for x in (p, *p.parents) if x != self.root and x.is_relative_to(self.root)):
            raise ValueError('不允许符号链接')
        if not p.resolve().is_relative_to(self.root):
            raise ValueError('路径超出知识库')
        if p.suffix != '.md' or len(parts) < 2 or parts[0] not in CATEGORIES:
            raise ValueError('只能操作知识目录内的 Markdown 笔记')
        return p

    @staticmethod
    def split(text):
        match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', text, re.S)
        if not match:
            if text.startswith('---\n') or text.startswith('---\r\n'):
                raise ValueError('笔记属性区未闭合，请先修复；不会覆盖原文件')
            return '', {}, text
        header = match.group(1)
        meta = yaml.safe_load(header) or {}
        if not isinstance(meta, dict):
            raise ValueError('笔记属性必须为键值映射')
        return header, meta, text[match.end():]

    def read(self, relative):
        p = self.path(relative)
        text = p.read_bytes().decode('utf-8')
        _, meta, body = self.split(text)
        category = Path(relative).parts[0]
        scope = 'owned' if category in OWNED_CATEGORIES else 'external' if category in EXTERNAL_CATEGORIES else 'archive_unclassified'
        return dict(path=relative, category=category, scope=scope, metadata=meta, body=body, revision=digest(text))

    def notes(self, categories=None):
        result = []
        for category in CATEGORIES if categories is None else categories:
            folder = self.root / category
            if folder.is_symlink():
                continue
            for base, dirs, files in os.walk(folder, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(base)/d).is_symlink() and not (category == '项目' and Path(base) == folder and d == '每日流'))
                for name in sorted(files):
                    p = Path(base)/name
                    if name.endswith('.md') and not name.startswith('.') and not p.is_symlink():
                        result.append(self.read(str(p.relative_to(self.root))))
        return result

    def render(self, meta, body):
        return '---\n' + ''.join(k + ': ' + json.dumps(v, ensure_ascii=False) + '\n' for k,v in meta.items()) + '---\n' + body.strip() + '\n'

    def patch(self, text, fields, body=None):
        # Keep external YAML properties, nested values and comments intact.
        header, meta, previous_body = self.split(text)
        lines = header.splitlines(keepends=True)
        for key, value in fields.items():
            pattern = re.compile(r'^(?:' + re.escape(key) + r'|"' + re.escape(key) + r'"|\x27' + re.escape(key) + r'\x27)\s*:')
            starts = [i for i, line in enumerate(lines) if pattern.match(line)]
            if len(starts) > 1 or (key in meta and not starts):
                raise ValueError('属性格式不支持安全局部修改，请先规范属性：' + key)
            replacement = key + ': ' + json.dumps(value, ensure_ascii=False) + '\n'
            if starts:
                start = starts[0]
                end = start + 1
                while end < len(lines) and (lines[end].startswith((' ', '\t', '- ')) or not lines[end].strip()):
                    end += 1
                lines[start:end] = [replacement]
            else:
                if lines and not lines[-1].endswith('\n'):
                    lines[-1] += '\n'
                lines.append(replacement)
        changed = ''.join(lines).rstrip('\r\n')
        return '---\n' + changed + '\n---\n' + (body.strip() + '\n' if body is not None else previous_body)

    def history_dir(self, path):
        self.path(path)
        base = self.root / '.history'
        folder = base / digest(path)
        if base.is_symlink() or folder.is_symlink():
            raise ValueError('历史目录不允许使用符号链接')
        return folder

    def write(self, path, text, old=None):
        p = self.path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if old is not None:
            before = p.read_bytes()
            if digest(before.decode('utf-8')) != old:
                raise ValueError('笔记已被修改，请重新读取后合并，禁止覆盖')
            backup = self.history_dir(path)
            backup.mkdir(parents=True, exist_ok=True)
            version = backup / (old + '.md')
            if version.is_symlink():
                raise ValueError('历史文件不允许使用符号链接')
            if version.exists() and version.read_bytes() != before:
                raise ValueError('历史版本校验失败，停止写入')
            if not version.exists():
                version.write_bytes(before)
        elif p.exists():
            raise ValueError('目标已存在，禁止覆盖')
        temp = p.with_name('.' + p.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(text, encoding='utf-8')
            if old is not None and digest(p.read_bytes().decode('utf-8')) != old:
                raise ValueError('写入前发现外部修改，请重新读取')
            if old is None:
                os.link(temp, p)
            else:
                os.replace(temp, p)
        finally:
            if temp.exists():
                temp.unlink()

    def run(self, q):
        op = q['operation']
        if op in dailyflow.READ_OPS | dailyflow.WRITE_OPS:
            return dailyflow.run(self, q)
        if op in ('read', 'list', 'search'):
            scope = q.get('scope', 'owned')
            if scope not in SCOPES:
                raise ValueError('检索范围必须是 owned（我的）、external（外部）或 all（全库）')
            categories = SCOPES[scope]
        if op == 'read':
            self.path(q['path'])
            if Path(q['path']).parts[0] not in categories:
                raise ValueError('这篇笔记不在本次检索范围内；找我的素材不能读取外部目录')
            return self.read(q['path'])
        if op in ('history', 'read_version'):
            folder = self.history_dir(q['path'])
            if op == 'history':
                return [dict(version=p.stem, saved_at=datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat())
                        for p in sorted(folder.glob('*.md'), key=lambda p:p.stat().st_mtime, reverse=True) if not p.is_symlink()]
            version = q['version']
            if not re.fullmatch('[a-f0-9]{64}', version):
                raise ValueError('历史版本无效')
            p = folder / (version + '.md')
            if p.is_symlink():
                raise ValueError('历史文件不允许使用符号链接')
            text = p.read_bytes().decode('utf-8')
            if digest(text) != version:
                raise ValueError('历史版本内容校验失败')
            _, meta, body = self.split(text)
            return dict(path=q['path'], metadata=meta, body=body, revision=version, text=text)
        if op in ('list', 'search'):
            terms = q.get('query', '').casefold().split()
            rows = []
            # Scope is applied before reading file bodies, not just before returning results.
            for n in self.notes(categories):
                if (n['metadata'].get('status') == 'archived' or n['category'] == '归档') and not q.get('include_archived'):
                    continue
                hay = (n['path'] + '\n' + json.dumps(n['metadata'], ensure_ascii=False, default=str) + '\n' + n['body']).casefold()
                if not terms or all(t in hay for t in terms):
                    if q.get('summary'):
                        rows.append(dict(path=n['path'], category=n['category'], scope=n['scope'], metadata=n['metadata'], revision=n['revision'], excerpt=n['body'][:240]))
                    else:
                        rows.append(n)
            offset, limit = int(q.get('offset', 0)), int(q.get('limit', 50))
            if offset < 0 or limit < 1 or limit > 500:
                raise ValueError('分页参数无效')
            return dict(scope=scope, categories=categories, total=len(rows), offset=offset, limit=limit, items=rows[offset:offset+limit])
        # User intent is checked by the conversational agent, not inferred from note contents.
        expected = '记下来' if op == 'create' else op
        if q.get('authorization') != expected or not q.get('user_instruction', '').strip():
            raise ValueError('缺少本次用户明确指令；普通对话禁止写入')
        if op == 'create':
            category = q['category']
            title = q['title'].strip()
            body = q['body'].strip()
            kind = q.get('kind', 'knowledge')
            flow = {'kind': kind}
            if kind in ('task', 'decision'):
                if category != '项目':
                    raise ValueError('个人待办与待定决策只能保存在项目目录')
                flow.update(task_state=q.get('task_state', 'pending_decision' if kind == 'decision' else 'todo'),
                            reminder_enabled=q.get('reminder_enabled', True),
                            due_at=q.get('due_at'), review_at=q.get('review_at'))
            dailyflow.validate_fields(kind, flow)
            if category not in CATEGORIES or category == '归档':
                raise ValueError('无效的新笔记分类')
            if not title or not body or re.search(r'[/\\\n\r\x00\[\]#^|]', title) or title.startswith('.') or len(title)>100:
                raise ValueError('标题或正文无效')
            # Saving a personal note must not search external inputs implicitly either.
            duplicate_categories = OWNED_CATEGORIES if category in OWNED_CATEGORIES else EXTERNAL_CATEGORIES
            for n in self.notes(duplicate_categories):
                if n['body'].strip() == body and n['metadata'].get('kind', 'knowledge') == kind:
                    return dict(status='duplicate', path=n['path'], revision=n['revision'])
            path = category + '/' + title + '.md'
            meta = dict(id=uuid.uuid4().hex, title=title, created=now(), updated=now(), status='active', source=q.get('source','本次对话'))
            meta.update(flow)
            self.write(path, self.render(meta, body))
        elif op in ('update','archive','restore','rollback'):
            path = q['path']
            n = self.read(path)
            if q.get('expected_revision') != n['revision']:
                raise ValueError('版本不一致，请重新读取笔记')
            meta, body = n['metadata'], n['body']
            text = self.path(path).read_bytes().decode('utf-8')
            if digest(text) != n['revision']:
                raise ValueError('读取后发生外部修改，请重新读取')
            fields = {'updated': now()}
            if op == 'update':
                body = q.get('body', body).strip()
                if not body:
                    raise ValueError('不能用空正文覆盖笔记')
                props = q.get('properties', {})
                if not isinstance(props, dict) or set(props) - {'title','tags','aliases','source','kind','task_state','due_at','review_at','reminder_enabled'}:
                    raise ValueError('只能编辑标题、标签、别名、来源及受支持的流程属性')
                flow_keys = {'kind','task_state','due_at','review_at','reminder_enabled'}
                if flow_keys.intersection(props):
                    kind = props.get('kind', meta.get('kind', 'knowledge'))
                    if kind in ('task', 'decision') and not path.startswith('项目/'):
                        raise ValueError('个人待办与待定决策只能保存在项目目录')
                    if kind in ('task', 'decision') and not props.get('task_state', meta.get('task_state')):
                        props['task_state'] = 'pending_decision' if kind == 'decision' else 'todo'
                    dailyflow.validate_fields(kind, props, meta)
                for key, value in props.items():
                    if key in flow_keys:
                        continue
                    if key in ('tags', 'aliases'):
                        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                            raise ValueError('标签和别名必须是文本列表')
                    elif not isinstance(value, str) or not value.strip():
                        raise ValueError('标题和来源必须是非空文本')
                fields.update(props)
            elif op == 'rollback':
                old = self.run(dict(operation='read_version', path=path, version=q['version']))
                self.write(path, old['text'], n['revision'])
                return self.with_views(dict(status=op, **self.read(path)))
            else:
                fields['status'] = 'archived' if op == 'archive' else 'active'
            self.write(path, self.patch(text, fields, body if op == 'update' else None), n['revision'])
        else:
            raise ValueError('不支持的操作')
        return self.with_views(dict(status=op, **self.read(path)))

    def with_views(self, receipt):
        try:
            receipt['views'] = dailyflow.refresh(self)
        except Exception as exc:
            receipt['views_pending'] = True
            receipt['views_error'] = str(exc)
        return receipt


if __name__ == "__main__":
    raise SystemExit("Library only. Use the instance runtime/cli.py with explicit --instance and --id.")
