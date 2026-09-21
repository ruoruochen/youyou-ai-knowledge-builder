#!/usr/bin/env python3
"""Keep all fixtures and reports in a new run under an explicitly allowed directory."""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from uuid import uuid4
import builder
import identity


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}


def integration(root, checks):
    def mark(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append({'name': name, 'passed': True})

    def deny(name, action):
        try:
            action()
        except (ValueError, RuntimeError, FileExistsError):
            mark(name, True)
        else:
            mark(name, False)

    protected = root / 'protected-existing-vault'
    (protected / '.obsidian').mkdir(parents=True)
    (protected / 'sentinel.md').write_text('Existing content must stay unchanged.', encoding='utf-8')
    before_protected = snapshot(protected)
    probe = subprocess.run([sys.executable, '-B', str(Path(__file__)), '--work', str(protected/'nested'),
                            '--protect', str(protected)], capture_output=True, text=True)
    mark('selftest runner rejects its own protected target before writing', probe.returncode != 0 and snapshot(protected) == before_protected and not (protected/'nested').exists())
    parent = root / 'instances'
    a = builder.create(parent, 'Same display name', 'test', 'Asia/Shanghai', protected=[protected])
    b = builder.create(parent, 'Same display name', 'test', 'UTC', protected=[protected])
    ar, br = Path(a['root']), Path(b['root'])
    b_before = snapshot(br)
    mark('same-name creations receive separate UUIDs, paths and vaults', a['instance_id'] != b['instance_id'] and ar != br)

    def call(config, q, succeeds=True, runtime=None, selected_root=None, selected_id=None):
        r = subprocess.run([sys.executable, '-B', str(runtime or Path(config['root']) / 'runtime' / 'cli.py'),
                            '--instance', str(selected_root or config['root']), '--id', selected_id or config['instance_id']],
                           input=json.dumps(q), text=True, capture_output=True, encoding='utf-8')
        value = json.loads(r.stdout)
        if bool(value.get('ok')) != succeeds or (r.returncode == 0) != succeeds:
            raise AssertionError({'request': q, 'return': value, 'stderr': r.stderr})
        if succeeds:
            assert value['instance_id'] == config['instance_id'] and value['root'] == config['root']
            return value['result']
        return value

    before = snapshot(ar)
    call(a, {'operation': 'list'}, succeeds=False, selected_id=b['instance_id'])
    call(b, {'operation': 'list'}, succeeds=False, runtime=ar/'runtime'/'cli.py')
    mark('wrong ID and wrong runtime are rejected without writes', snapshot(ar) == before and snapshot(br) == b_before)
    with patch.object(identity, 'writer_lock', side_effect=AssertionError('Lock touched before identity check')):
        deny('instance management verifies identity before touching a lock', lambda: identity.change(ar,b['instance_id'],'archive',a['revision'],'Synthetic wrong-target request'))
    r = subprocess.run([sys.executable, '-B', str(ar/'runtime'/'cli.py')], input='{"operation":"list"}', text=True, capture_output=True)
    mark('missing explicit binding cannot fall back', r.returncode != 0)
    deny('creation inside an existing instance is rejected', lambda: builder.create(ar/'nested', 'Nested', 'test'))
    deny('creation inside a protected existing vault is rejected', lambda: builder.create(protected/'nested', 'Unsafe', 'test', protected=[protected]))
    copy = root / 'copied-instance'
    shutil.copytree(ar, copy)
    call(a, {'operation': 'list'}, succeeds=False, runtime=copy/'runtime'/'cli.py', selected_root=copy)
    mark('raw directory copy cannot impersonate the original binding', True)
    call(a, {'operation': 'create', 'category': '资产', 'title': 'No consent', 'body': 'Do not save'}, succeeds=False)
    mark('no-save authorization leaves an empty vault', call(a, {'operation': 'list'})['total'] == 0)

    q = dict(operation='create', authorization='记下来', user_instruction='Save this fictional test record',
             category='资产', title='Test note', body='Personal test idea A', source='Synthetic test')
    note = call(a, q)
    mark('new note stores only in selected instance', call(a, {'operation':'list'})['total'] == 1 and call(b, {'operation':'list'})['total'] == 0)
    duplicate = call(a, q)
    mark('duplicate content does not create a second note', duplicate['status'] == 'duplicate')
    external = call(a, dict(q, category='资源', title='External test', body='External reference X'))
    mark('owned search excludes external content', call(a, {'operation':'search','query':'External'})['total'] == 0)
    for op in ('read', 'history', 'read_version'):
        call(a, {'operation':op,'path':external['path'],'version':'0'*64}, succeeds=False)
    mark('owned explicit and historical reads reject external paths', True)
    call(a, {'operation':'read','scope':'all','path':'../other.md'}, succeeds=False)
    mark('path traversal is rejected', True)
    symlink = ar/a['vault_dir']/'资产'/'cross-link.md'
    symlink.symlink_to(protected/'sentinel.md')
    call(a, {'operation':'read','path':'资产/cross-link.md'}, succeeds=False)
    mark('cross-root symlink is rejected', True)

    def update(n, **extra):
        return call(a, dict(operation='update',authorization='update',user_instruction='Edit fictional test',
                            path=n['path'],expected_revision=n['revision'],**extra))

    revised = update(note, body='Revised personal test idea A')
    call(a, dict(operation='update',authorization='update',user_instruction='Stale edit',path=note['path'],expected_revision=note['revision'],body='Wrong'), succeeds=False)
    mark('stale revision cannot overwrite a newer note', call(a, {'operation':'read','path':note['path']})['body'].strip() == 'Revised personal test idea A')
    def lifecycle(n, op, **extra):
        return call(a, dict(operation=op, authorization=op, user_instruction='Manage fictional test record',
                            path=n['path'],expected_revision=n['revision'],**extra))
    archived = lifecycle(revised, 'archive')
    mark('note archive excludes it from active search', call(a, {'operation':'list'})['total'] == 0)
    restored = lifecycle(archived, 'restore')
    rolled = lifecycle(restored, 'rollback', version=note['revision'])
    mark('note restore and rollback preserve history', rolled['body'] == note['body'] and len(call(a, {'operation':'history','path':note['path']})) >= 2)
    task = call(a, dict(q, category='项目', title='Pending decision', body='AI proposal\nMy objection remains undecided', kind='decision', due_at='2000-01-01T20:30:00+00:00'))
    mark('pending decision remains undecided in queue', call(a, {'operation':'queue'})['items'][0]['task_state'] == 'pending_decision')
    alerts = call(a, {'operation':'reminder_scan'})['alerts']
    call(a, dict(operation='reminder_ack',authorization='maintenance',user_instruction='Prepared synthetic reminder',fingerprints=[x['fingerprint'] for x in alerts]))
    mark('reminder acknowledgement deduplicates unchanged item', len(alerts) == 1 and call(a, {'operation':'reminder_scan'})['alerts'] == [])
    finished = update(task, properties={'task_state':'done'})
    mark('completed item leaves queue and reminder candidates', not call(a, {'operation':'queue'})['items'] and not call(a, {'operation':'reminder_scan'})['alerts'])
    today = call(a, {'operation':'today'})
    mark('daily navigation excludes external sources', all(x['path'] != external['path'] for x in today['saved']))
    before = snapshot(ar)
    for op in ('list','today','daily','queue','reminder_scan','reminder_settings'):
        call(a, {'operation':op})
    mark('routine reading and scans make no filesystem changes', before == snapshot(ar))
    (ar/'.writer.lock').write_text('{"token":"another-writer"}',encoding='utf-8')
    call(a, dict(q,title='Busy writer',body='Should not save'), succeeds=False)
    (ar/'.writer.lock').unlink()  # Only the disposable lock this test just created.
    mark('concurrent writer lock rejects another write', True)

    observed = identity.inspect(ar, a['instance_id'])
    deny('test mode refuses live scheduler binding', lambda: identity.change(ar,a['instance_id'],'configure',observed['revision'],'Test',{'scheduler':{'provider':'test','job_id':'fake','status':'ACTIVE','last_verified_at':identity.clock()}}))
    changed = identity.change(ar,a['instance_id'],'configure',observed['revision'],'Rename this test instance',{'name':'Renamed test'})
    mark('rename preserves root and stable ID', changed['root'] == a['root'] and changed['instance_id'] == a['instance_id'])
    deny('stale manifest revision is rejected', lambda: identity.change(ar,a['instance_id'],'configure',observed['revision'],'Stale change',{'name':'wrong'}))
    archived = identity.change(ar,a['instance_id'],'archive',changed['revision'],'Archive test')
    call(a, dict(q,title='Archived writer',body='Should not save'), succeeds=False)
    call(a, {'operation':'list'})
    restored = identity.change(ar,a['instance_id'],'restore',archived['revision'],'Restore test')
    mark('instance archive is read-only and reversible', restored['status'] == 'active')
    mark('explicit-parent listing includes both instances', len(builder.list_instances(parent)['items']) == 2)
    mark('all operations leave the other instance unchanged', b_before == snapshot(br))
    configured = identity.change(ar,a['instance_id'],'configure',restored['revision'],'Use UTC for this test',{'timezone':'UTC','review_time':'09:15'})
    mark('instance timezone and review time reach runtime settings', call(a, {'operation':'reminder_settings'})['timezone'] == 'UTC' and call(a, {'operation':'reminder_settings'})['review_time'] == '09:15')
    # Only an isolated manifest fixture: no actual host scheduler is created.
    prod = builder.create(root/'scheduler-fixtures', 'Scheduler metadata fixture', 'production')
    binding = {'provider':'simulated-host','job_id':'synthetic-job','status':'ACTIVE','last_verified_at':identity.clock()}
    active = identity.change(prod['root'],prod['instance_id'],'configure',prod['revision'],'Synthetic metadata test',{'scheduler':binding})
    deny('active scheduler binding cannot be silently cleared', lambda: identity.change(prod['root'],prod['instance_id'],'configure',active['revision'],'Synthetic test',{'scheduler':{}}))
    deny('instance archive requires pausing the actual scheduler first', lambda: identity.change(prod['root'],prod['instance_id'],'archive',active['revision'],'Synthetic test'))
    paused = identity.change(prod['root'],prod['instance_id'],'configure',active['revision'],'Synthetic pause metadata',{'scheduler':dict(binding,status='PAUSED')})
    disabled = identity.change(prod['root'],prod['instance_id'],'configure',paused['revision'],'Clear synthetic binding',{'scheduler':{}})
    mark('paused scheduler metadata can be cleared without a leftover binding', disabled['scheduler'] == {})
    mark('protected existing vault unchanged', before_protected == snapshot(protected))
    return {'a':a['root'],'b':b['root']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True)
    parser.add_argument('--protect', action='append', default=[])
    args = parser.parse_args()
    parent = identity.absolute(args.work)
    for protected in args.protect:
        if identity.overlaps(parent, identity.absolute(protected)):
            raise ValueError('Selftest directory overlaps a protected root')
    for p in (parent, *parent.parents):
        if (p / identity.MANIFEST).exists() or (p / '.obsidian').exists() or p == builder.PACKAGE:
            raise ValueError('Selftest work must be outside a skill package or existing vault')
    root = parent / ('run-' + str(uuid4()))
    (root / 'work').mkdir(parents=True,exist_ok=False)
    os.environ['KB_TEST_WORK'] = str(root)
    (root/'AGENTS.md').write_text('Synthetic test fixture only.',encoding='utf-8')
    # Tests can use disposable TemporaryDirectory objects only below this run.
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent/'tests'))
    stream = io.StringIO()
    outcome = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (root/'regression.txt').write_text(stream.getvalue(),encoding='utf-8')
    report = {'regression_tests': outcome.testsRun, 'regression_passed': outcome.wasSuccessful(),
              'checks': [], 'work': str(root), 'platform': sys.platform}
    try:
        report['instances'] = integration(root, report['checks'])
    except Exception as exc:
        report['integration_error'] = repr(exc)
    report['ok'] = outcome.wasSuccessful() and 'integration_error' not in report
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
