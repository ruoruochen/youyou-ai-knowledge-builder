#!/usr/bin/env python3
"""Create and manage isolated Markdown knowledge instances. No global registry."""
import argparse
import importlib.util
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

PACKAGE = Path(__file__).resolve().parents[1]
RUNTIME = PACKAGE / 'assets' / 'runtime'
sys.path.insert(0, str(RUNTIME))
import identity


def preflight(timezone='UTC'):
    errors = []
    if sys.version_info < (3, 9):
        errors.append('Python 3.9+ is required')
    if importlib.util.find_spec('yaml') is None:
        errors.append('PyYAML is missing; install it in an allowed instance/tool environment')
    try:
        ZoneInfo(timezone)
    except Exception:
        errors.append('Timezone unavailable: ' + timezone + '; verify IANA name or install tzdata')
    required = ['bridge.py', 'dailyflow.py', 'identity.py', 'cli.py']
    if any(not (RUNTIME / x).is_file() for x in required):
        errors.append('Incomplete skill package: runtime files are missing')
    if not (PACKAGE / 'references' / 'operating-rules.md').is_file():
        errors.append('Incomplete skill package: operating rules are missing')
    return {'ready': not errors, 'python': sys.version.split()[0], 'platform': sys.platform,
            'timezone': timezone, 'errors': errors, 'scheduler': 'not_configured',
            'obsidian_required_for_file_operations': False}


def create(parent, name, mode, timezone='UTC', review_time='20:30', protected=()):
    report = preflight(timezone)
    if not report['ready']:
        raise ValueError(json.dumps(report, ensure_ascii=False))
    if mode not in ('test', 'production'):
        raise ValueError('Explicit test or production mode is required')
    if not name.strip() or len(name) > 100:
        raise ValueError('A display name of 1-100 characters is required')
    datetime.strptime(review_time, '%H:%M')
    parent = identity.absolute(parent)
    for ancestor in (parent, *parent.parents):
        if (ancestor / identity.MANIFEST).exists() or (ancestor / '.obsidian').exists():
            raise ValueError('Cannot create an instance inside an existing instance or Obsidian vault')
    protected = [identity.absolute(p) for p in protected]
    # Protect the distributable itself even if caller omits --protect.
    if PACKAGE not in protected:
        protected.append(PACKAGE)
    instance_id = str(uuid4())
    root = parent / ('kb-' + mode + '-' + instance_id)
    for p in protected:
        if identity.overlaps(root, p):
            raise ValueError('Target overlaps protected root: ' + str(p))
    # Ancestor detection handles old vaults that predate this instance format.
    parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=False)
    value = {'schema_version': identity.SCHEMA, 'instance_id': instance_id, 'name': name,
             'mode': mode, 'status': 'active', 'state': 'building', 'root': str(root),
             'vault_dir': 'vault-' + instance_id, 'timezone': timezone,
             'review_time': review_time, 'created_at': identity.clock(), 'updated_at': identity.clock(),
             'protected_roots': [str(p) for p in protected], 'scheduler': {},
             'template_version': '1.0.0'}
    identity.atomic_json(root / identity.MANIFEST, value)
    try:
        shutil.copytree(RUNTIME, root / 'runtime', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        import bridge
        import dailyflow
        vault_root = root / value['vault_dir']
        vault_root.mkdir()
        for category in bridge.CATEGORIES:
            (vault_root / category).mkdir()
        binding = ('# This instance only\n\n'
                   + '- Display name: ' + name + '\n- Mode: ' + mode
                   + '\n- Instance ID: `' + instance_id + '`\n- Root: `' + str(root)
                   + '`\n- Vault: `' + str(vault_root) + '`\n\n'
                   + 'Re-read instance.json and verify this root + ID before each operation. '
                   + 'Do not fall back to another default vault. Do not edit global agent rules.\n\n')
        rules = (PACKAGE / 'references' / 'operating-rules.md').read_text(encoding='utf-8')
        (root / 'AGENTS.md').write_text(binding + rules, encoding='utf-8')
        (root / 'API.md').write_text((PACKAGE / 'references' / 'api.md').read_text(encoding='utf-8'), encoding='utf-8')
        handoff = {'instance_id': instance_id, 'root': str(root), 'mode': mode,
                   'command': [sys.executable, '-B', str(root / 'runtime' / 'cli.py'), '--instance', str(root), '--id', instance_id]}
        identity.atomic_json(root / 'handoff.json', handoff)
        (vault_root / '开始使用.md').write_text(
            '# ' + name + '\n\n模式：' + mode + '\n\n身份：`' + instance_id
            + '`\n\n在AI中交流，明确说“记下来”才保存。找我的只查项目、资产、技能与模板；资源和灵感库是外部输入。\n\n'
            + '[[项目/每日流/今日|今日]] · [[项目/每日流/待处理事项|待处理事项]]\n\n'
            + '日报只索引已保存的个人内容。提醒检查逻辑已提供，真实定时任务尚未配置。\n\n'
            + '用AI时提供本库根目录与完整身份ID，读取库外同级AGENTS.md和API.md。\n', encoding='utf-8')
        dailyflow.configure(timezone, review_time)
        dailyflow.refresh(bridge.Vault(vault_root))
        value['state'] = 'ready'
        identity.atomic_json(root / identity.MANIFEST, value)
    except Exception as exc:
        # Preserve partial work and evidence; never replace or delete a previous instance.
        raise RuntimeError('Creation incomplete at ' + str(root) + ': ' + str(exc)) from exc
    return dict(identity.inspect(root, instance_id), handoff=handoff,
                note='Empty isolated instance created; no real scheduler or global app settings changed')


def list_instances(parent):
    parent = identity.absolute(parent)
    items = []
    if not parent.exists():
        return {'items': []}
    for p in sorted(parent.glob('kb-*')):
        if p.is_symlink() or not p.is_dir():
            continue
        manifest = p / identity.MANIFEST
        if manifest.is_symlink() or not manifest.is_file():
            continue
        try:
            raw = json.loads(manifest.read_text(encoding='utf-8'))
            value, rev = identity.read_instance(p, raw['instance_id'], ready=False)
            items.append({k: value[k] for k in ('instance_id', 'name', 'mode', 'root', 'status', 'state')})
        except Exception as exc:
            items.append({'root': str(p), 'error': str(exc)})
    return {'items': items}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='operation', required=True)
    doctor = sub.add_parser('doctor')
    doctor.add_argument('--timezone', default='UTC')
    make = sub.add_parser('create')
    make.add_argument('--parent', required=True)
    make.add_argument('--name', required=True)
    make.add_argument('--mode', required=True, choices=['test', 'production'])
    make.add_argument('--timezone', default='UTC')
    make.add_argument('--review-time', default='20:30')
    make.add_argument('--protect', action='append', default=[])
    ls = sub.add_parser('list')
    ls.add_argument('--parent', required=True)
    for op in ('inspect', 'configure', 'archive', 'restore'):
        q = sub.add_parser(op)
        q.add_argument('--instance', required=True)
        q.add_argument('--id', required=True)
        if op != 'inspect':
            q.add_argument('--expected-revision', required=True)
            q.add_argument('--user-instruction', required=True)
    args = p.parse_args()
    if args.operation == 'doctor':
        result = preflight(args.timezone)
    elif args.operation == 'create':
        result = create(args.parent, args.name, args.mode, args.timezone, args.review_time, args.protect)
    elif args.operation == 'list':
        result = list_instances(args.parent)
    elif args.operation == 'inspect':
        result = identity.inspect(args.instance, args.id)
    else:
        props = json.load(sys.stdin) if args.operation == 'configure' else None
        result = identity.change(args.instance, args.id, args.operation, args.expected_revision, args.user_instruction, props)
    print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, indent=2, default=str))
    if args.operation == 'doctor' and not result['ready']:
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
