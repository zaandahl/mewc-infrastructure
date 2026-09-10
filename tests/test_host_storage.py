"""Fixture checks for the dangerous decisions, without touching a disk/service."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'playbooks/files' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storage = load('storage', 'storage_guard.py')
runtime = load('runtime', 'runtime_config.py')
VOLUME = '6c93b4a8-4d12-49c8-9183-9f14311176c2'
MOUNT = '/mnt/mewc-volume'


def disk(**changes):
    result = {'name': '/dev/vdb', 'type': 'disk', 'serial': VOLUME,
              'fstype': 'ext4', 'uuid': 'fs-uuid', 'mountpoints': [MOUNT],
              'maj:min': '252:16', 'ro': False}
    result.update(changes)
    return result


class VolumeIdentity(unittest.TestCase):
    def select(self, *disks, **kwargs):
        return storage.select_volume({'blockdevices': list(disks)}, VOLUME, MOUNT, **kwargs)

    def test_matches_full_serial_independent_of_device_name(self):
        self.assertEqual(self.select(disk(name='/dev/vdc'))['name'], '/dev/vdc')

    def test_full_compact_uuid_is_valid(self):
        self.assertEqual(self.select(disk(serial=VOLUME.replace('-', '')))['uuid'], 'fs-uuid')

    def test_wrong_absent_and_ambiguous_serials_are_rejected(self):
        for disks in [(), (disk(serial='other'),), (disk(), disk(name='/dev/vdc'))]:
            with self.subTest(disks=disks), self.assertRaises(ValueError):
                self.select(*disks)

    def test_truncated_serial_requires_exact_control_plane_attestation(self):
        truncated = disk(serial=VOLUME[:20])
        with self.assertRaises(ValueError):
            self.select(truncated)
        with self.assertRaises(ValueError):
            self.select(truncated, expected_serial=VOLUME[:20])
        self.assertEqual(self.select(disk(), expected_serial=VOLUME[:20],
                                     attachment_verified=True)['serial'], VOLUME)
        self.assertEqual(self.select(truncated, expected_serial=VOLUME[:20],
                                     attachment_verified=True)['serial'], VOLUME[:20])
        with self.assertRaises(ValueError):
            self.select(truncated, expected_serial=VOLUME[:18], attachment_verified=True)

    def test_root_partitioned_readonly_and_foreign_filesystem_rejected(self):
        unsafe = [disk(mountpoints=['/']), disk(type='part'), disk(ro=True),
                  disk(children=[{'name': '/dev/vdb1', 'mountpoints': ['/']}]),
                  disk(fstype='LVM2_member'), disk(mountpoints=['/srv/other'])]
        for candidate in unsafe:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.select(candidate)

    def test_unknown_signatures_block_blank_volume_initialisation(self):
        probe = {'blockdevices': [disk(fstype=None, uuid=None, mountpoints=[None])]}
        path = Mock()
        path.is_symlink.return_value = False
        path.exists.return_value = False
        with patch.object(storage, 'Path', return_value=path), \
             patch.object(storage.subprocess, 'run', return_value=Mock(returncode=1)), \
             patch.object(storage, 'run_json', side_effect=[probe, {'signatures': [{'type': 'gpt'}]}]):
            with self.assertRaisesRegex(ValueError, 'signatures'):
                storage.inspect(VOLUME, MOUNT, verify_blank=True)

    def test_mount_path_must_be_safe_and_narrow(self):
        for path in ['/', '/mnt/x/../root', '/var/lib', '/mnt/a b', '/mnt/a;reboot']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                storage.select_volume({'blockdevices': [disk()]}, VOLUME, path)

    def test_mount_requires_exact_uuid_device_and_rw_ext4(self):
        good = {'target': MOUNT, 'uuid': 'fs-uuid', 'maj:min': '252:16',
                'fstype': 'ext4', 'options': 'rw,nosuid,nodev'}
        storage.check_mount(disk(), {'filesystems': [good]}, MOUNT, 'fs-uuid')
        for change in [{'target': '/'}, {'uuid': 'wrong'}, {'maj:min': '252:0'},
                       {'fstype': 'xfs'}, {'options': 'ro'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                storage.check_mount(disk(), {'filesystems': [good | change]}, MOUNT, 'fs-uuid')


class RuntimeConfiguration(unittest.TestCase):
    def test_docker_preserves_nvidia_and_unrelated_options(self):
        source = '{"runtimes":{"nvidia":{"path":"nvidia-container-runtime"}},"log-driver":"local"}'
        updated, old = runtime.docker_config(source, MOUNT + '/docker')
        self.assertEqual(old, '/var/lib/docker')
        self.assertIn('nvidia-container-runtime', updated)
        self.assertIn('"log-driver": "local"', updated)
        self.assertEqual(runtime.docker_config(updated, MOUNT + '/docker')[0], updated)

    def test_containerd_only_changes_global_root_preserving_nested_config(self):
        source = ('version = 2\nroot = "/var/lib/containerd"\n'
                  '[plugins."io.containerd.grpc.v1.cri"]\n'
                  'root = "leave-nested-alone"\nfoo = "bar"\n')
        updated, old = runtime.containerd_config(source, MOUNT + '/containerd')
        self.assertEqual(old, '/var/lib/containerd')
        self.assertIn('root = "leave-nested-alone"', updated)
        self.assertIn('foo = "bar"', updated)
        self.assertEqual(runtime.containerd_config(updated, MOUNT + '/containerd')[0], updated)

    def test_containerd_missing_top_level_root_inserted_before_tables(self):
        updated, _ = runtime.containerd_config('[grpc]\naddress = "/run/containerd.sock"\n', MOUNT + '/containerd')
        self.assertTrue(updated.startswith('root = '))

    def test_containerd_imports_require_manual_reconciliation(self):
        with self.assertRaises(ValueError):
            runtime.containerd_config('imports = ["/etc/containerd/extra.toml"]\n', MOUNT + '/containerd')

    def test_effective_containerd_root_accepts_equivalent_toml_quoting(self):
        for quote in ("'", '"'):
            with self.subTest(quote=quote):
                runtime.verify_containerd_root(f'root = {quote}{MOUNT}/containerd{quote}\n',
                                               MOUNT + '/containerd')
        with self.assertRaises(ValueError):
            runtime.verify_containerd_root('root = "/var/lib/containerd"\n', MOUNT + '/containerd')
        with self.assertRaises(ValueError):
            runtime.verify_containerd_root('[plugins.example]\nroot = "/mnt/mewc-volume/containerd"\n',
                                           MOUNT + '/containerd')

    def test_existing_data_requires_migration_but_is_never_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = directory + '-new'
            self.assertFalse(runtime.migration_needed(directory, target))
            data = Path(directory) / 'research-layer'
            data.write_text('retain me')
            self.assertTrue(runtime.migration_needed(directory, target))
            self.assertFalse(runtime.migration_needed(directory, directory))
            self.assertEqual(data.read_text(), 'retain me')
            self.assertFalse(Path(target).exists())


if __name__ == '__main__':
    unittest.main()
