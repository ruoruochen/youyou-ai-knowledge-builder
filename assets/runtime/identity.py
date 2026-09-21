"""Instance identity and containment checks. Not an OS security boundary."""
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

MANIFEST = 'instance.json'
SCHEMA = 1


def clock():
    return datetime.now(timezone.utc).isoformat()


def absolute(value):
    p = Path(value).expanduser()
    if not p.is_absolute() or '..' in p.parts:
        raise ValueError('An absolute path without parent traversal is required')
    for part in (p, *p.parents):
        if part.is_symlink():
            raise ValueError('Symlink paths are not allowed: ' + str(part))
    return p.resolve()


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def revision(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_instance(root, expected_id, ready=True):
    root = absolute(root)
    UUID(expected_id)
    p = root / MANIFEST
    if p.is_symlink() or not p.is_file():
        raise ValueError('No valid instance manifest at the explicitly selected root')
    value = json.loads(p.read_text(encoding='utf-8'))
    if value.get('schema_version') != SCHEMA or value.get('instance_id') != expected_id:
        raise ValueError('Instance ID or schema mismatch; no fallback is allowed')
    if value.get('root') != str(root):
        raise ValueError('Instance was copied or moved; root binding mismatch')
    if value.get('mode') not in ('test', 'production') or value.get('status') not in ('active', 'archived'):
        raise ValueError('Invalid instance mode/status')
    if ready and value.get('state') != 'ready':
        raise ValueError('Instance creation is incomplete; do not reuse as a finished vault')
    if value.get('vault_dir') != 'vault-' + expected_id:
        raise ValueError('Vault binding mismatch')
    for protected in value.get('protected_roots', []):
        if overlaps(root, absolute(protected)):
            raise ValueError('Selected instance overlaps a protected root')
    for name in ('runtime', value['vault_dir']):
        folder = absolute(root / name)
        if not folder.is_dir():
            raise ValueError('Missing instance directory: ' + name)
    ZoneInfo(value['timezone'])
    datetime.strptime(value['review_time'], '%H:%M')
    return value, revision(p)


def atomic_json(path, value):
    if path.is_symlink():
        raise ValueError('Refusing a symlink state file')
    temp = path.with_name('.' + path.name + '.' + uuid4().hex)
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def writer_lock(root):
    path = root / '.writer.lock'
    token = uuid4().hex
    try:
        with path.open('x', encoding='utf-8') as handle:
            json.dump({'pid': os.getpid(), 'token': token, 'created_at': clock()}, handle)
    except FileExistsError:
        raise ValueError('Instance is busy or has an interrupted write; do not auto-delete the lock')
    try:
        yield
    finally:
        if not path.is_symlink() and path.is_file():
            if json.loads(path.read_text(encoding='utf-8')).get('token') == token:
                path.unlink()


def inspect(root, instance_id):
    value, rev = read_instance(root, instance_id)
    return dict(value, revision=rev)


def change(root, instance_id, operation, expected_revision, user_instruction, properties=None):
    root = absolute(root)
    if not user_instruction.strip():
        raise ValueError('An explicit instance management instruction is required')
    # Validate before even creating the transient writer lock in the target.
    read_instance(root, instance_id)
    with writer_lock(root):
        value, rev = read_instance(root, instance_id)
        if rev != expected_revision:
            raise ValueError('Manifest changed; inspect again before editing')
        if operation in ('archive', 'restore'):
            if operation == 'archive' and value.get('scheduler', {}).get('status') == 'ACTIVE':
                raise ValueError('Pause the actual scheduler and update its binding before archiving')
            value['status'] = 'archived' if operation == 'archive' else 'active'
        elif operation == 'configure':
            properties = properties or {}
            if not properties or set(properties) - {'name', 'timezone', 'review_time', 'scheduler'}:
                raise ValueError('Only name/timezone/review_time/scheduler are configurable')
            if 'name' in properties and (not isinstance(properties['name'], str) or not properties['name'].strip() or len(properties['name']) > 100):
                raise ValueError('Invalid display name')
            if 'timezone' in properties:
                ZoneInfo(properties['timezone'])
            if 'review_time' in properties:
                datetime.strptime(properties['review_time'], '%H:%M')
            if value.get('scheduler', {}).get('status') == 'ACTIVE' and {'timezone', 'review_time'} & set(properties):
                raise ValueError('Pause and update the actual scheduler before changing time settings')
            if 'scheduler' in properties:
                binding = properties['scheduler']
                current_binding = value.get('scheduler', {})
                if current_binding.get('status') == 'ACTIVE':
                    if not binding or any(binding.get(k) != current_binding.get(k) for k in ('provider', 'job_id')):
                        raise ValueError('Pause and record the existing scheduler before clearing or replacing its binding')
                if binding:
                    if value['mode'] != 'production':
                        raise ValueError('Test instances cannot bind a live scheduler')
                    if value['status'] == 'archived' and binding.get('status') == 'ACTIVE':
                        raise ValueError('Archived instances cannot bind an active scheduler')
                    if set(binding) != {'provider', 'job_id', 'status', 'last_verified_at'} or binding['status'] not in ('ACTIVE', 'PAUSED'):
                        raise ValueError('Invalid scheduler binding; record only a verified real job')
                    if any(not isinstance(x, str) or not x.strip() for x in binding.values()):
                        raise ValueError('Scheduler values must be nonempty strings')
                elif binding != {}:
                    raise ValueError('Use an empty object for no scheduler')
            value.update(properties)
        else:
            raise ValueError('Unknown instance operation')
        value['updated_at'] = clock()
        history = root / '.instance-history'
        if history.is_symlink():
            raise ValueError('Manifest history cannot be a symlink')
        history.mkdir(exist_ok=True)
        backup = history / (rev + '.json')
        if backup.is_symlink():
            raise ValueError('Manifest history cannot be a symlink')
        raw = (root / MANIFEST).read_bytes()
        if not backup.exists():
            with backup.open('xb') as f:
                f.write(raw)
        elif backup.read_bytes() != raw:
            raise ValueError('Manifest backup failed verification')
        if revision(root / MANIFEST) != rev:
            raise ValueError('Manifest changed externally; refusing overwrite')
        atomic_json(root / MANIFEST, value)
    return inspect(root, instance_id)
