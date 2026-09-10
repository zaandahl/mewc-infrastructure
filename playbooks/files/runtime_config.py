#!/usr/bin/python3
"""Plan/apply minimal runtime storage edits; never relocate or remove data."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def docker_config(text, data_root):
    config = json.loads(text or '{}')
    if not isinstance(config, dict):
        raise ValueError('Docker configuration must be a JSON object')
    previous = config.get('data-root', '/var/lib/docker')
    config['data-root'] = data_root
    return json.dumps(config, indent=2, sort_keys=True) + '\n', previous


def containerd_config(text, data_root):
    config = tomllib.loads(text)
    previous = config.get('root', '/var/lib/containerd')
    lines = text.splitlines(keepends=True)
    root_indices = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith('['):
            break
        if re.match(r'^\s*root\s*=', line):
            root_indices.append(i)
    replacement = f'root = {json.dumps(data_root)}\n'
    if root_indices:
        lines[root_indices[0]] = replacement
    else:
        lines.insert(0, replacement)
    result = ''.join(lines)
    # imports may override the root in ways the main file cannot prove safe.
    if config.get('imports'):
        raise ValueError('containerd imports require manual review before managed storage setup')
    if tomllib.loads(result).get('root') != data_root:
        raise ValueError('Could not set top-level containerd root')
    return result, previous


def verify_containerd_root(text, expected):
    actual = tomllib.loads(text).get('root')
    if actual != expected:
        raise ValueError(f'Effective containerd root is {actual!r}, expected {expected!r}')


def migration_needed(previous, target):
    path = Path(previous)
    return previous != target and path.exists() and any(path.iterdir())


def write_atomic(path, content):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = handle.name
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mount-point', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    root = args.mount_point
    if not re.fullmatch(r'/mnt/[A-Za-z0-9_-]+', root):
        raise ValueError('Invalid mount point')
    if args.verify:
        effective = subprocess.check_output(['containerd', 'config', 'dump'], text=True, timeout=30)
        verify_containerd_root(effective, f'{root}/containerd')
        print(json.dumps({'verified': True, 'containerd_root': f'{root}/containerd'}))
        return
    for unit in ('docker.service', 'docker.socket', 'containerd.service'):
        local_unit = Path('/etc/systemd/system') / unit
        if local_unit.exists():
            raise ValueError(f'Custom {local_unit} requires manual reconciliation')
        dropins = Path(str(local_unit) + '.d')
        if dropins.exists():
            allowed = {'mewc-storage.conf'}
            if unit == 'docker.service':
                allowed.add('override.conf')
            unexpected = [path.name for path in dropins.glob('*.conf') if path.name not in allowed]
            if unexpected:
                raise ValueError(f'Custom runtime drop-ins require review: {unexpected}')
    edits = []
    for name, transform, path in [
        ('docker', docker_config, Path('/etc/docker/daemon.json')),
        ('containerd', containerd_config, Path('/etc/containerd/config.toml')),
    ]:
        target = Path(root) / name
        if target.is_symlink() or (target.exists() and target.resolve() != target):
            raise ValueError(f'{target} must be a real directory on verified storage')
        original = path.read_text() if path.exists() else ''
        updated, previous = transform(original, f'{root}/{name}')
        # Compare semantics for Docker: nvidia-ctk formatting is not a change.
        changed = (json.loads(original or '{}') != json.loads(updated)
                   if name == 'docker' else original != updated)
        if migration_needed(previous, f'{root}/{name}'):
            raise ValueError(f'{previous} contains runtime data: explicit stopped-service '
                             'migration and verification are required before configuration')
        if changed:
            edits.append((path, updated))
    # Legacy override injects a duplicate data-root flag; only remove this known file.
    legacy = Path('/etc/systemd/system/docker.service.d/override.conf')
    remove_legacy = False
    if legacy.exists():
        known = ('[Service]\nExecStart=\nExecStart=/usr/bin/dockerd '
                 '--data-root=/mnt/mewc-volume/docker -H fd:// '
                 '--containerd=/run/containerd/containerd.sock\n')
        if legacy.read_text().strip() != known.strip():
            raise ValueError('Custom Docker override.conf requires manual reconciliation')
        remove_legacy = True
    if args.apply:
        subprocess.run(['/usr/local/sbin/mewc-storage-check'], check=True)
        for path, updated in edits:
            write_atomic(path, updated)
        if remove_legacy:
            legacy.unlink()
    print(json.dumps({'changed': bool(edits or remove_legacy),
                      'files': [str(path) for path, _ in edits],
                      'remove_legacy_override': remove_legacy}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'MEWC runtime configuration refused: {error}', file=sys.stderr)
        sys.exit(1)
