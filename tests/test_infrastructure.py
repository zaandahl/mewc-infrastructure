"""Credential-free controller regressions; no cloud or Terraform executable needed."""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from mewc_infra import cli, keys


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        key = self.base / "key"
        key.write_text("synthetic private key fixture")
        key.chmod(0o600)
        self.cfg = {
            "deployment_id": "test-worker", "state_dir": str(self.base / "test-worker"),
            "profile": "cpu", "project_id": "11111111-1111-4111-8111-111111111111",
            "region": "TestRegion", "availability_zone": "test-az", "volume_availability_zone": "test-az",
            "image_id": "22222222-2222-4222-8222-222222222222", "flavor_id": "cpu-1",
            "network_id": "33333333-3333-4333-8333-333333333333", "keypair_name": "test-key",
            "private_key_path": str(key), "known_hosts_file": str(self.base / "known_hosts"),
            "volume_id": "44444444-4444-4444-8444-444444444444",
            "ssh_cidr": "192.0.2.5/32", "ssh_user": "ubuntu", "ssh_timeout_seconds": 10,
        }
        self.config_file = self.base / "config.json"
        self.config_file.write_text(json.dumps(self.cfg))
        self.c = cli.Controller(self.cfg)
        self.responses = {
            ("token", "issue"): {"project_id": self.cfg["project_id"]},
            ("image", "show", self.cfg["image_id"]): {"id": self.cfg["image_id"], "status": "active"},
            ("network", "show", self.cfg["network_id"]): {"id": self.cfg["network_id"], "status": "ACTIVE"},
            ("availability", "zone", "list", "--compute"): [{"Zone Name": "test-az", "Zone Status": "available"}],
            ("flavor", "show", "cpu-1"): {"id": "cpu-1", "name": "c3.small"},
            ("keypair", "show", "test-key"): {"name": "test-key"},
            ("volume", "show", self.cfg["volume_id"]): {"id": self.cfg["volume_id"], "availability_zone": "test-az", "os-vol-tenant-attr:tenant_id": self.cfg["project_id"], "status": "available", "attachments": []},
            ("server", "list"): [],
        }
        self.c.cloud = Mock(side_effect=lambda *args: copy.deepcopy(self.responses[args]))
        self.c.run = Mock(return_value=subprocess.CompletedProcess([], 0, "ssh-ed25519 fixture\n", ""))

    def load(self, **changes):
        self.config_file.write_text(json.dumps(self.cfg | changes))
        return cli.config_load(self.config_file)

    def test_valid_config_and_cpu_preflight(self):
        self.assertEqual(self.load(), self.cfg)
        self.c.preflight()
        self.assertFalse(any("reservation" in call.args for call in self.c.cloud.call_args_list))

    def test_typed_config_and_no_credentials(self):
        for fields in ({"password": "do-not-serialize"}, {"ssh_timeout_seconds": True},
                       {"ssh_cidr": "0.0.0.0/0"}, {"image_id": "ubuntu"},
                       {"profile": "cpu-rstudio"}, {"deployment_id": "x"},
                       {"state_dir": str(cli.ROOT / "test-worker")},
                       {"format_volume_id": "other-volume"}):
            with self.subTest(fields=fields), self.assertRaises(cli.Error):
                self.load(**fields)

    def test_web_requires_digest_and_propagates_it(self):
        with self.assertRaisesRegex(cli.Error, "detector_image"):
            self.load(profile="cpu-web")
        with self.assertRaisesRegex(cli.Error, "immutable"):
            self.load(profile="cpu-web", detector_image="example/detect:latest")
        image = "example/detect@sha256:" + "a" * 64
        self.load(profile="cpu-web", detector_image=image)
        self.cfg.update(profile="cpu-web", detector_image=image)
        self.configure_setup()
        with contextlib.redirect_stdout(io.StringIO()):
            self.c.configure()
        variables = cli.read_json(Path(self.cfg["state_dir"]) / "ansible-vars.json")
        self.assertEqual(variables["detect_image"], image)
        plays = [x.args[0] for x in self.c.run.call_args_list if x.args[0][0] == "ansible-playbook"]
        self.assertEqual(len(plays), 2)
        self.assertTrue(plays[-1][-1].endswith("cpu-mewc-web/web_site.yml"))
        self.assertTrue(all(Path(args[-1]).is_file() for args in plays))

    def test_state_identity_is_frozen_and_private(self):
        with cli.deployment_lock(self.cfg) as state:
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            self.assertEqual((state / "deployment.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(cli.Error, "holds"):
                with cli.deployment_lock(self.cfg):
                    pass
        with self.assertRaisesRegex(cli.Error, "identity"):
            with cli.deployment_lock(self.cfg | {"image_id": self.cfg["volume_id"]}):
                pass

    def test_unregistered_legacy_state_is_rejected(self):
        state = Path(self.cfg["state_dir"])
        state.mkdir(mode=0o700)
        (state / "terraform.tfstate").write_text("{}")
        with self.assertRaisesRegex(cli.Error, "legacy"):
            with cli.deployment_lock(self.cfg):
                pass

    def test_preflight_fails_wrong_project_before_other_calls(self):
        self.responses[("token", "issue")]["project_id"] = "wrong-project"
        with self.assertRaisesRegex(cli.Error, "project"):
            self.c.preflight()
        self.assertEqual(self.c.cloud.call_count, 1)

    def test_preflight_checks_each_identity(self):
        failures = [
            (("image", "show", self.cfg["image_id"]), "id", "different-image"),
            (("network", "show", self.cfg["network_id"]), "status", "DOWN"),
            (("flavor", "show", "cpu-1"), "id", "different-flavor"),
            (("volume", "show", self.cfg["volume_id"]), "os-vol-tenant-attr:tenant_id", "different-project"),
            (("volume", "show", self.cfg["volume_id"]), "availability_zone", "wrong-az"),
            (("volume", "show", self.cfg["volume_id"]), "attachments", [{"server_id": "protected-server"}]),
        ]
        for command, field, value in failures:
            original = copy.deepcopy(self.responses)
            with self.subTest(command=command, field=field):
                self.responses[command][field] = value
                with self.assertRaises(cli.Error):
                    self.c.preflight()
            self.responses = original

    def test_cloud_key_uses_bare_public_key_mode_and_checks_match(self):
        self.c.preflight()
        public_calls = [x.args[0] for x in self.c.run.call_args_list if x.args[0][0] == "openstack"]
        self.assertEqual(len(public_calls), 1)
        self.assertIn("--public-key", public_calls[0])
        self.assertNotIn("json", public_calls[0])
        def run(args, **kwargs):
            key = "different" if args[0] == "openstack" else "fixture"
            return subprocess.CompletedProcess([], 0, "ssh-ed25519 " + key, "")
        self.c.run.side_effect = run
        with self.assertRaisesRegex(cli.Error, "Private key does not match"):
            self.c.preflight()

    def test_missing_playbook_is_rejected_before_cloud_or_host_work(self):
        with patch.object(cli, "ROOT", self.base):
            with self.assertRaisesRegex(cli.Error, "Required playbook is missing"):
                self.c.configure()
        self.c.cloud.assert_not_called()
        self.c.run.assert_not_called()

    def test_failed_play_error_names_phase_without_leaking_output(self):
        controller = cli.Controller(self.cfg)
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 7, "private-token", "secret-password")):
            with self.assertRaisesRegex(cli.Error, "ansible-playbook playbooks/site.yml failed") as caught:
                controller.run(["ansible-playbook", str(cli.ROOT / "playbooks" / "site.yml")])
            self.assertNotIn("private-token", str(caught.exception))
            self.assertNotIn("secret-password", str(caught.exception))

    def test_name_collision_cannot_claim_other_instance(self):
        self.responses[("server", "list")] = [{"Name": "test-worker", "ID": "other-server"}]
        with self.assertRaisesRegex(cli.Error, "outside this state"):
            self.c.preflight()

    def gpu(self):
        self.cfg.update(profile="gpu", lease_id="55555555-5555-4555-8555-555555555555",
                        reservation_id="66666666-6666-4666-8666-666666666666", flavor_id="66666666-6666-4666-8666-666666666666")
        now = dt.datetime.now(dt.timezone.utc)
        lease = {"id": self.cfg["lease_id"], "project_id": self.cfg["project_id"], "status": "ACTIVE", "degraded": False,
                 "start_date": (now - dt.timedelta(hours=1)).isoformat(), "end_date": (now + dt.timedelta(hours=1)).isoformat(),
                 "reservations": [{"id": self.cfg["reservation_id"], "resource_type": "virtual:instance", "status": "active", "flavor_id": self.cfg["flavor_id"]}]}
        flavor = {"id": self.cfg["flavor_id"], "name": "reservation:fixture", "properties": {"resources:VGPU": "1", "aggregate_instance_extra_specs:reservation": self.cfg["reservation_id"]}}
        self.c.lease = Mock(return_value=lease)
        self.responses[("server", "list", "--flavor", self.cfg["flavor_id"])] = []
        return lease, flavor

    def test_gpu_explicit_lease_and_flavor(self):
        lease, flavor = self.gpu()
        self.c.reservation(flavor, None)
        self.assertEqual(self.c.lease.call_count, 1)
        lease["reservations"][0]["flavor_id"] = "unrequested-cpu-fallback"
        with self.assertRaisesRegex(cli.Error, "exact requested flavor"):
            self.c.reservation(flavor, None)

    def test_gpu_wrong_expired_degraded_or_cpu_reservation(self):
        for case in ("wrong-id", "expired", "degraded", "cpu", "used", "extra-spec"):
            with self.subTest(case=case):
                lease, flavor = self.gpu()
                if case == "wrong-id":
                    lease["reservations"][0]["id"] = "wrong"
                elif case == "expired":
                    lease["end_date"] = "2000-01-01T00:00:00"
                elif case == "degraded":
                    lease["degraded"] = True
                elif case == "cpu":
                    flavor["properties"]["resources:VGPU"] = "0"
                elif case == "used":
                    self.responses[("server", "list", "--flavor", self.cfg["flavor_id"])] = [{"ID": "other-server"}]
                else:
                    flavor["properties"]["aggregate_instance_extra_specs:reservation"] = "wrong"
                with self.assertRaises(cli.Error):
                    self.c.reservation(flavor, None)

    def test_command_errors_do_not_expose_credentials(self):
        controller = cli.Controller(self.cfg)
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 8, "private-token", "secret-password")):
            with self.assertRaises(cli.Error) as caught:
                controller.cloud("token", "issue")
            self.assertNotIn("private-token", str(caught.exception))
            self.assertNotIn("secret-password", str(caught.exception))

    def test_upstream_failure_prevents_terraform_mutation(self):
        self.c.cloud.side_effect = cli.Error("API failed")
        self.c.terraform = Mock()
        with self.assertRaisesRegex(cli.Error, "API failed"):
            self.c.plan()
        self.c.terraform.assert_not_called()

    def test_init_failure_prevents_plan(self):
        self.c.preflight = Mock()
        self.c.terraform = Mock(side_effect=cli.Error("init failed"))
        with self.assertRaisesRegex(cli.Error, "init failed"):
            self.c.plan()
        self.assertEqual(self.c.terraform.call_count, 1)
        self.assertEqual(self.c.terraform.call_args.args[0], "init")

    def test_plan_rejects_storage_destruction_and_foreign_attachment(self):
        invalid = [
            {"address": "openstack_blockstorage_volume_v3.data", "mode": "managed", "type": "openstack_blockstorage_volume_v3", "change": {"actions": ["delete"]}},
            {"address": "openstack_compute_volume_attach_v2.data", "mode": "managed", "type": "openstack_compute_volume_attach_v2", "change": {"before": {"volume_id": "other-volume"}, "after": None}},
            {"address": "openstack_compute_instance_v2.worker", "mode": "managed", "type": "openstack_compute_instance_v2", "change": {"before": {"name": "existing-worker"}, "after": None}},
        ]
        for resource in invalid:
            with self.subTest(resource=resource), self.assertRaises(cli.Error):
                self.c.validate_plan({"resource_changes": [resource]})
        self.c.validate_plan({"resource_changes": [{"address": "openstack_compute_volume_attach_v2.data", "mode": "managed", "type": "openstack_compute_volume_attach_v2", "change": {"before": {"volume_id": self.cfg["volume_id"]}, "after": None}}]})

    def test_apply_requires_saved_unchanged_reviewed_plan(self):
        self.c.preflight = Mock()
        self.c.initialize = Mock()
        self.c.terraform = Mock()
        with self.assertRaisesRegex(cli.Error, "review"):
            self.c.apply()
        self.c.terraform.assert_not_called()
        state = Path(self.cfg["state_dir"])
        state.mkdir(mode=0o700)
        (state / "apply.tfplan").write_bytes(b"modified")
        cli.write_json(state / "apply.meta.json", {"config": "changed"})
        self.c.source_hash = Mock(return_value="source")
        with self.assertRaisesRegex(cli.Error, "changed"):
            self.c.apply()
        self.c.terraform.assert_not_called()

    def test_expired_reservation_does_not_block_compute_teardown(self):
        self.c.preflight = Mock(side_effect=cli.Error("expired"))
        self.c.initialize = Mock()
        self.c.source_hash = Mock(return_value="source")
        state = Path(self.cfg["state_dir"])
        state.mkdir(mode=0o700)
        def terraform(*args):
            if args[0] == "plan":
                (state / "destroy.tfplan").write_bytes(b"plan")
            return subprocess.CompletedProcess([], 0, "{}", "")
        self.c.terraform = Mock(side_effect=terraform)
        with contextlib.redirect_stdout(io.StringIO()):
            self.c.plan(destroy=True)
        self.c.preflight.assert_not_called()
        self.assertIn("-destroy", self.c.terraform.call_args_list[0].args)

    def configure_setup(self):
        Path(self.cfg["known_hosts_file"]).write_text("192.0.2.9 ssh-ed25519 verified")
        self.c.instance_id = Mock(return_value="owned-instance")
        self.c.initialize = Mock()
        self.c.terraform = Mock(return_value=subprocess.CompletedProcess([], 0, "192.0.2.9\n", ""))
        self.responses[("volume", "show", self.cfg["volume_id"])]["attachments"] = [{"server_id": "owned-instance"}]
        Path(self.cfg["state_dir"]).mkdir(mode=0o700)

    def test_configure_propagates_key_raw_inventory_and_attachment(self):
        self.configure_setup()
        with contextlib.redirect_stdout(io.StringIO()):
            self.c.configure()
        args = self.c.run.call_args.args[0]
        self.assertEqual(args[:3], ["ansible-playbook", "-i", "192.0.2.9,"])
        self.assertEqual(args[args.index("--private-key") + 1], self.cfg["private_key_path"])
        variables = cli.read_json(Path(self.cfg["state_dir"]) / "ansible-vars.json")
        self.assertTrue(variables["mewc_attachment_verified"])
        self.assertFalse(variables["mewc_format_volume"])
        self.assertIn("StrictHostKeyChecking=yes", variables["ansible_ssh_common_args"])
        self.assertEqual(variables["mewc_expected_device_serial"], self.cfg["volume_id"][:20])

    def test_intermediate_play_failure_stops_sequence(self):
        self.configure_setup()
        self.cfg["profile"] = "cpu-web"
        def run(args, **kwargs):
            if args[0] == "ansible-playbook":
                raise cli.Error("play failed")
            return subprocess.CompletedProcess([], 0, "verified", "")
        self.c.run.side_effect = run
        with self.assertRaisesRegex(cli.Error, "play failed"):
            self.c.configure()
        plays = [x for x in self.c.run.call_args_list if x.args[0][0] == "ansible-playbook"]
        self.assertEqual(len(plays), 1)
        self.assertTrue(plays[0].args[0][-1].endswith("playbooks/site.yml"))

    def test_host_key_mismatch_stops_without_ansible(self):
        self.configure_setup()
        def run(args, **kwargs):
            return subprocess.CompletedProcess([], 255 if args[0] == "ssh" else 0, "verified", "Host key verification failed")
        self.c.run.side_effect = run
        with self.assertRaisesRegex(cli.Error, "identity/authentication"):
            self.c.configure()
        self.assertFalse(any(x.args[0][0] == "ansible-playbook" for x in self.c.run.call_args_list))

    def test_readiness_deadline_is_bounded(self):
        self.configure_setup()
        with patch.object(cli.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaisesRegex(cli.Error, "deadline"):
                self.c.configure()
        self.assertFalse(any(x.args[0][0] == "ansible-playbook" for x in self.c.run.call_args_list))

    def test_identity_state_and_tfvars_never_include_environment_secrets(self):
        with patch.dict(os.environ, {"OS_PASSWORD": "do-not-copy", "TF_VAR_password": "do-not-copy", "TF_CLI_ARGS": "-destroy"}):
            controller = cli.Controller(self.cfg)
            controller.terraform = Mock()
            with cli.deployment_lock(self.cfg) as state:
                controller.initialize()
                self.assertNotIn("do-not-copy", (state / "deployment.json").read_text() + (state / "inputs.tfvars.json").read_text())
            self.assertNotIn("TF_VAR_password", controller.env)
            self.assertNotIn("TF_CLI_ARGS", controller.env)
            self.assertEqual(controller.env["OS_PASSWORD"], "do-not-copy")

    def test_key_setup_refuses_partial_and_mismatched_existing_keys(self):
        public = Path(self.cfg["private_key_path"] + ".pub")
        with contextlib.redirect_stderr(io.StringIO()), patch.object(keys, "run") as run:
            self.assertEqual(keys.main(["test-key", self.cfg["private_key_path"]]), 1)
            run.assert_not_called()
        public.write_text("ssh-ed25519 original")
        with contextlib.redirect_stderr(io.StringIO()), patch.object(keys, "run", return_value="ssh-ed25519 different"):
            self.assertEqual(keys.main(["test-key", self.cfg["private_key_path"]]), 1)
        self.assertEqual(public.read_text(), "ssh-ed25519 original")

    def test_reservation_catalog_endpoint_uses_v1_once(self):
        self.gpu()
        self.c = cli.Controller(self.cfg)
        for endpoint in ("https://reservation.example.test", "https://reservation.example.test/v1/"):
            connection = Mock(current_project_id=self.cfg["project_id"])
            connection.session.get_endpoint.return_value = endpoint
            connection.session.get.return_value.json.return_value = {"lease": {"id": self.cfg["lease_id"]}}
            sdk = Mock()
            sdk.connect.return_value = connection
            with patch.dict("sys.modules", {"openstack": sdk}):
                self.assertEqual(self.c.lease(), {"id": self.cfg["lease_id"]})
            connection.session.get.assert_called_once_with(
                "https://reservation.example.test/v1/leases/" + self.cfg["lease_id"], timeout=60)

    def test_repeated_key_setup_verifies_without_create_or_overwrite(self):
        public = Path(self.cfg["private_key_path"] + ".pub")
        public.write_text("ssh-ed25519 fixture")
        def run(args):
            if args[:2] == ["ssh-keygen", "-y"]:
                return "ssh-ed25519 fixture"
            if args[:3] == ["openstack", "keypair", "list"]:
                return '[{"Name": "test-key"}]'
            if args[:3] == ["openstack", "keypair", "show"]:
                self.assertIn("--public-key", args)
                self.assertNotIn("json", args)
                return "ssh-ed25519 fixture"
            self.fail("Unexpected creation command")
        with contextlib.redirect_stdout(io.StringIO()), patch.object(keys, "run", side_effect=run):
            self.assertEqual(keys.main(["test-key", self.cfg["private_key_path"]]), 0)
            self.assertEqual(keys.main(["test-key", self.cfg["private_key_path"]]), 0)
        self.assertEqual(Path(self.cfg["private_key_path"]).read_text(), "synthetic private key fixture")

    def test_legacy_wrappers_fail_without_config(self):
        for profile in ("gpu", "gpu-complex", "cpu-mewc-web", "cpu-rstudio"):
            result = subprocess.run([str(cli.ROOT / profile / "run_terraform.sh"), "destroy"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(cli.ROOT.glob("**/*.tfstate*")))


if __name__ == "__main__":
    unittest.main()
