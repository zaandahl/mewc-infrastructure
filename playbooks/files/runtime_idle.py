#!/usr/bin/python3
"""Refuse runtime reconfiguration if any containerd namespace has live tasks."""
import subprocess
import sys


def output(argv):
    return subprocess.check_output(argv, text=True, timeout=20).strip()


def main():
    state = subprocess.run(['systemctl', 'is-active', 'containerd.service'], capture_output=True, text=True)
    if state.returncode == 3:
        return
    if state.returncode != 0:
        raise ValueError('Cannot determine containerd service state')
    namespaces = output(['ctr', '--timeout', '10s', 'namespaces', 'list', '--quiet']).splitlines()
    for namespace in namespaces:
        tasks = output(['ctr', '--timeout', '10s', '--namespace', namespace, 'tasks', 'list', '--quiet'])
        if tasks:
            raise ValueError(f'containerd namespace {namespace} has tasks; stop workloads explicitly first')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'MEWC runtime configuration refused: {error}', file=sys.stderr)
        sys.exit(1)
