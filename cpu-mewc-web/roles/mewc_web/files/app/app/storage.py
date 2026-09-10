"""Shared strict paths and atomic state. No cloud or Docker configuration in requests."""
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath

MOUNT = Path(os.environ.get('MEWC_MOUNT', '/mnt/mewc-volume'))
ROOT = MOUNT / 'mewc-web'
ALLOWED = {'.jpg', '.jpeg', '.png'}


def mounted():
    if not MOUNT.is_mount():
        raise ValueError('Research volume is not mounted')


def job_id(value):
    if not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid job ID')
    return value


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Invalid relative path')
    if any(ord(c) < 32 for c in value) or len(value.encode()) > 1024:
        raise ValueError('Invalid relative path')
    parts = value.split('/')
    if any(p in ('', '.', '..') for p in parts) or PurePosixPath(value).is_absolute():
        raise ValueError('Invalid relative path')
    return Path(*parts)


def safe_file(base, name):
    path = relative(name)
    current = base
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlinks are forbidden')
    if not current.is_file() or not current.resolve().is_relative_to(base.resolve()):
        raise ValueError('File not found')
    return current


def open_input(base, name):
    """Open through directory descriptors: a compromised web account cannot swap a symlink."""
    parts = relative(name).parts
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        result = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(result).st_mode):
            os.close(result)
            raise ValueError('Regular files required')
        return os.fdopen(result, 'rb')
    finally:
        os.close(fd)


def atomic_json(path, value):
    fd, tmp = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def state(jid):
    path = ROOT / 'jobs' / job_id(jid) / 'state.json'
    if path.exists():
        return json.loads(path.read_text())
    if (ROOT / 'inbox' / jid / 'manifest.json').is_file():
        manifest = json.loads((ROOT / 'inbox' / jid / 'manifest.json').read_text())
        return {'status': 'uploaded', 'processed': 0, 'total': len(manifest['files'])}
    raise ValueError('Job not found')
