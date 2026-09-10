#!/usr/bin/python3
"""Fail closed before formatting, mounting, or using MEWC's Cinder volume."""
import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path


def run_json(argv):
    return json.loads(subprocess.check_output(argv, text=True))


def normalize_serial(value):
    # Full UUIDs only. A virtio prefix is not sufficient proof of identity.
    value = str(value or '').strip().lower()
    if value.startswith('volume-'):
        value = value[7:]
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def validate_mount_path(path):
    if not re.fullmatch(r'/mnt/[A-Za-z0-9_-]+', path):
        raise ValueError('mount_point must be a single directory under /mnt')


def descendants(node):
    yield node
    for child in node.get('children', []):
        yield from descendants(child)


def select_volume(blocks, volume_id, mount_point, expected_serial=None, attachment_verified=False):
    volume_id = str(uuid.UUID(volume_id))
    validate_mount_path(mount_point)
    if expected_serial is not None:
        if not attachment_verified or expected_serial not in (volume_id, volume_id[:20]):
            raise ValueError('Serial override requires verified attachment and exact UUID/virtio20 transform')
    disks = blocks.get('blockdevices', [])
    candidates = [node for disk in disks for node in descendants(disk)
                  if (normalize_serial(node.get('serial')) == volume_id
                      or (expected_serial and str(node.get('serial', '')).strip() == expected_serial))]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one disk with the full Cinder volume serial; '
                         f'found {len(candidates)}. Truncated serials require an explicit '
                         'control-plane attachment verification; device names are not identity.')
    disk = candidates[0]
    if disk.get('type') != 'disk' or disk.get('ro') or disk.get('children'):
        raise ValueError('Refusing read-only, partitioned, or non-whole-disk volume')
    mounts = [m for m in disk.get('mountpoints', []) if m]
    if any(m != mount_point for m in mounts):
        raise ValueError('Volume is mounted elsewhere (or is the root disk)')
    if disk.get('fstype') not in (None, '', 'ext4'):
        raise ValueError('Only blank or existing ext4 data volumes are supported')
    return disk


def check_mount(disk, mounts, mount_point, filesystem_uuid):
    entries = mounts.get('filesystems', [])
    if len(entries) != 1:
        raise ValueError('Expected one mounted filesystem')
    actual = entries[0]
    if (actual.get('target') != mount_point or actual.get('maj:min') != disk.get('maj:min')
            or actual.get('uuid') != filesystem_uuid or disk.get('uuid') != filesystem_uuid
            or actual.get('fstype') != 'ext4'
            or 'rw' not in actual.get('options', '').split(',')):
        raise ValueError('Data mount identity/UUID or writable filesystem does not match')


def inspect(volume_id, mount_point, verify_blank=False, expected_serial=None, attachment_verified=False):
    blocks = run_json(['lsblk', '--json', '--paths', '--output',
                       'NAME,TYPE,SERIAL,FSTYPE,UUID,MOUNTPOINTS,MAJ:MIN,RO'])
    disk = select_volume(blocks, volume_id, mount_point, expected_serial, attachment_verified)
    target = Path(mount_point)
    if target.is_symlink():
        raise ValueError("Mount point must not be a symlink")
    mounted = subprocess.run(["findmnt", "--json", "--mountpoint", mount_point,
                              "--output", "TARGET,MAJ:MIN,UUID,FSTYPE,OPTIONS"],
                             capture_output=True, text=True)
    if mounted.returncode == 0:
        check_mount(disk, json.loads(mounted.stdout), mount_point, disk.get("uuid"))
    elif mounted.returncode != 1:
        raise ValueError("Unable to inspect existing mount")
    elif target.exists() and any(target.iterdir()):
        raise ValueError("Unmounted mount point contains data; reconcile explicitly")
    if verify_blank and not disk.get('fstype'):
        signatures = run_json(['wipefs', '--no-act', '--json', disk['name']])
        if signatures.get('signatures'):
            raise ValueError('Unrecognised on-disk signatures: refusing format')
    return disk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--volume-id')
    parser.add_argument('--mount-point')
    parser.add_argument('--expected-serial')
    parser.add_argument('--attachment-verified', action='store_true')
    parser.add_argument('--config', default='/etc/mewc-storage.json')
    args = parser.parse_args()
    if args.inspect:
        disk = inspect(args.volume_id, args.mount_point, verify_blank=True,
                       expected_serial=args.expected_serial, attachment_verified=args.attachment_verified)
        print(json.dumps(disk))
        return
    config = json.loads(Path(args.config).read_text())
    disk = inspect(config['volume_id'], config['mount_point'],
                   expected_serial=config.get('expected_serial'),
                   attachment_verified=config.get('attachment_verified', False))
    mounts = run_json(['findmnt', '--json', '--mountpoint', config['mount_point'],
                       '--output', 'TARGET,MAJ:MIN,UUID,FSTYPE,OPTIONS'])
    check_mount(disk, mounts, config['mount_point'], config['filesystem_uuid'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f'MEWC storage check failed: {error}', file=sys.stderr)
        sys.exit(1)
