#!/usr/bin/env python3
"""The only supported note entry point: explicit instance root and UUID."""
import argparse
import json
import sys
from pathlib import Path
import identity
import bridge
import dailyflow

READ_OPS = {'read', 'list', 'search', 'history', 'read_version'} | dailyflow.READ_OPS
WRITE_OPS = {'create', 'update', 'archive', 'restore', 'rollback'} | dailyflow.WRITE_OPS


def execute(root, instance_id, request):
    root = identity.absolute(root)
    if Path(__file__).resolve().parent.parent != root:
        raise ValueError('This runtime belongs to another instance; use the target instance runtime')
    config, _ = identity.read_instance(root, instance_id)
    op = request.get('operation')
    if op not in READ_OPS | WRITE_OPS | {'inspect'}:
        raise ValueError('Unsupported operation')
    if op == 'inspect':
        return identity.inspect(root, instance_id)
    scope = request.get('scope', 'owned')
    if scope not in bridge.SCOPES:
        raise ValueError('Scope must be owned, external or all')
    # Apply ownership limits to explicit paths, including historical reads.
    if request.get('path'):
        relative = Path(request['path'])
        if not relative.parts or relative.parts[0] not in bridge.SCOPES[scope]:
            raise ValueError('The requested path is outside the chosen ownership scope')
    dailyflow.configure(config['timezone'], config['review_time'])
    vault = bridge.Vault(root / config['vault_dir'])
    if op in READ_OPS:
        return vault.run(request)
    if config['status'] != 'active':
        raise ValueError('Archived instances are read-only; explicitly restore the instance first')
    with identity.writer_lock(root):
        fresh, _ = identity.read_instance(root, instance_id)
        if fresh['status'] != 'active' or fresh != config:
            raise ValueError('Instance configuration changed; reread before retrying')
        return vault.run(request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', required=True)
    parser.add_argument('--id', required=True)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    if not isinstance(request, dict):
        raise ValueError('stdin must contain one JSON object')
    result = execute(args.instance, args.id, request)
    print(json.dumps({'ok': True, 'instance_id': args.id, 'root': str(identity.absolute(args.instance)), 'result': result}, ensure_ascii=False, default=str, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        sys.exit(1)
