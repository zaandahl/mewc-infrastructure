"""Explicit compute lifecycle; persistent Cinder volumes are never owned here.

Cloud credentials stay in the environment. Configuration, state and saved plans
belong in a private external directory owned by one operator; flock serializes
commands on that machine. This is deliberately not a shared-state backend.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TF_ROOT = ROOT / "infra" / "compute"
REQUIRED = {
    "deployment_id", "state_dir", "profile", "project_id", "region",
    "availability_zone", "volume_availability_zone", "image_id", "flavor_id", "network_id", "keypair_name",
    "private_key_path", "volume_id", "ssh_cidr", "ssh_user", "known_hosts_file",
}
OPTIONAL = {"lease_id", "reservation_id", "format_volume_id", "ssh_timeout_seconds", "detector_image"}
TF_FIELDS = {
    "deployment_id", "project_id", "region", "availability_zone", "image_id",
    "flavor_id", "network_id", "keypair_name", "volume_id", "ssh_cidr",
}
ALLOWED_RESOURCES = {
    "openstack_networking_secgroup_v2.ssh",
    "openstack_networking_secgroup_rule_v2.ssh",
    "openstack_compute_instance_v2.worker",
    "openstack_compute_volume_attach_v2.data",
}


class Error(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Error(message)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Error(f"Cannot read valid JSON from {path}") from exc


def config_load(path):
    cfg = read_json(path)
    require(isinstance(cfg, dict), "Configuration must be a JSON object")
    require(not REQUIRED - cfg.keys(), "Missing configuration fields: " + ", ".join(sorted(REQUIRED - cfg.keys())))
    require(not cfg.keys() - REQUIRED - OPTIONAL, "Unknown configuration fields (credentials must stay in environment)")
    for key, value in cfg.items():
        if key != "ssh_timeout_seconds" and not (key == "availability_zone" and value is None):
            require(isinstance(value, str) and bool(value) and not any(c in value for c in "\n\r\0"), f"Invalid string field: {key}")
    require(re.fullmatch(r"[a-z][a-z0-9-]{2,47}", cfg["deployment_id"]), "Invalid deployment_id")
    require(cfg["profile"] in {"gpu", "cpu", "cpu-web"}, "Supported profiles: gpu, cpu, cpu-web; RStudio is legacy/unsupported")
    for key in ("project_id", "image_id", "network_id", "volume_id"):
        try:
            uuid.UUID(cfg[key])
        except ValueError as exc:
            raise Error(f"{key} must be an exact UUID") from exc
    for key in ("flavor_id", "region", "volume_availability_zone", "keypair_name", "ssh_user"):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]*", cfg[key]), f"Invalid identifier: {key}")
    require(cfg["availability_zone"] is None or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", cfg["availability_zone"]), "Invalid availability_zone")
    require(cfg["profile"] == "gpu" or cfg["availability_zone"] is not None, "CPU requires explicit availability_zone")
    for key in ("state_dir", "private_key_path", "known_hosts_file"):
        p = Path(cfg[key])
        require(p.is_absolute(), f"{key} must be absolute")
        cfg[key] = str(p.resolve())
    state = Path(cfg["state_dir"])
    require(not state.is_relative_to(ROOT), "state_dir must be outside the checkout")
    require(state.name == cfg["deployment_id"], "state_dir basename must equal deployment_id")
    require(Path(cfg["private_key_path"]).is_file(), "Private key does not exist")
    require(Path(cfg["private_key_path"]).stat().st_mode & 0o077 == 0, "Private key must not be accessible by group/others")
    try:
        network = ipaddress.ip_network(cfg["ssh_cidr"], strict=True)
        require(network.version == 4 and network.prefixlen > 0, "ssh_cidr must be a restricted IPv4 CIDR")
    except ValueError as exc:
        raise Error("Invalid ssh_cidr") from exc
    timeout = cfg.setdefault("ssh_timeout_seconds", 300)
    require(type(timeout) is int and 10 <= timeout <= 1800, "ssh_timeout_seconds must be an integer from 10 to 1800")
    if cfg["profile"] == "gpu":
        for key in ("reservation_id", "lease_id"):
            require(key in cfg, f"GPU requires explicit {key}")
            try:
                uuid.UUID(cfg[key])
            except ValueError as exc:
                raise Error(f"Invalid {key}") from exc
    else:
        require(not ({"reservation_id", "lease_id"} & cfg.keys()), "CPU profile must not consume a GPU reservation")
    if cfg["profile"] == "cpu-web":
        require("detector_image" in cfg, "cpu-web requires an explicit detector_image digest")
    if "detector_image" in cfg:
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", cfg["detector_image"]), "detector_image must be an immutable sha256 image reference")
    if "format_volume_id" in cfg:
        require(cfg["format_volume_id"] == cfg["volume_id"], "Formatting authorization must name the exact volume_id")
    return cfg


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    temp.replace(path)


@contextlib.contextmanager
def deployment_lock(cfg):
    state = Path(cfg["state_dir"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(state.stat().st_mode & 0o077 == 0, "state_dir must have mode 0700")
    with (state / ".controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("Another controller command holds this deployment lock") from exc
        identity = state / "deployment.json"
        if identity.exists():
            require(read_json(identity) == cfg, "Deployment identity/configuration changed; explicit reviewed migration or a new deployment is required")
        else:
            require(not any(p.name != ".controller.lock" for p in state.iterdir()), "Refusing an unregistered nonempty state directory; no automatic legacy migration")
            write_json(identity, cfg)
        yield state


class Controller:
    def __init__(self, cfg):
        self.cfg = cfg
        self.state = Path(cfg["state_dir"])
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("TF_VAR_", "TF_CLI_ARGS")) and k not in {"TF_WORKSPACE", "TF_DATA_DIR", "TF_CLI_CONFIG_FILE", "OS_PROJECT_NAME", "OS_TENANT_NAME", "OS_TENANT_ID"}}
        self.env.update(OS_PROJECT_ID=cfg["project_id"], OS_REGION_NAME=cfg["region"],
                        TF_DATA_DIR=str(self.state / "terraform-data"), TF_IN_AUTOMATION="1")

    def run(self, args, *, timeout=1800, check=True):
        phase = args[0]
        if args[0] == "terraform" and len(args) > 2:
            phase += " " + args[2]
        elif args[0] == "ansible-playbook":
            phase += " " + str(Path(args[-1]).relative_to(ROOT))
        elif args[0] == "openstack" and len(args) > 6:
            phase += " " + " ".join(args[5:7])
        try:
            result = subprocess.run(args, env=self.env, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if not check:
                return subprocess.CompletedProcess(args, 124, "", "Readiness command timed out")
            raise Error(f"{phase} exceeded its deadline") from exc
        except OSError as exc:
            raise Error(f"{phase} failed to start") from exc
        if check and result.returncode:
            # Never echo CLI output: providers/auth clients can include credentials.
            raise Error(f"{phase} failed (exit {result.returncode}); resources are retained for recovery")
        return result

    def cloud(self, *args):
        result = self.run(["openstack", "--os-project-id", self.cfg["project_id"], "--os-region-name", self.cfg["region"], *args, "-f", "json"], timeout=90)
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise Error("OpenStack returned invalid JSON") from exc

    def scope(self):
        token = self.cloud("token", "issue")
        require(token.get("project_id") == self.cfg["project_id"], "Authenticated project does not match deployment")

    def instance_id(self):
        state = self.state / "terraform.tfstate"
        if not state.exists():
            return None
        data = read_json(state)
        for resource in data.get("resources", []):
            address = resource.get("type", "") + "." + resource.get("name", "")
            require(resource.get("mode") == "managed" and address in ALLOWED_RESOURCES and not resource.get("module"), "Unexpected resource in compute state; refusing legacy/foreign ownership")
        instances = [r for r in data.get("resources", []) if r.get("type") == "openstack_compute_instance_v2"]
        if not instances or not instances[0].get("instances"):
            return None
        attrs = instances[0]["instances"][0]["attributes"]
        require(attrs.get("name") == self.cfg["deployment_id"], "Instance in state belongs to another deployment")
        require(attrs.get("metadata", {}).get("mewc_deployment") == self.cfg["deployment_id"], "Instance ownership metadata mismatch")
        return attrs["id"]

    def preflight(self):
        c = self.cfg
        self.scope()
        own_id = self.instance_id()
        image = self.cloud("image", "show", c["image_id"])
        require(image.get("id") == c["image_id"] and image.get("status", "").lower() == "active", "Image unavailable or wrong image ID")
        network = self.cloud("network", "show", c["network_id"])
        require(network.get("id") == c["network_id"] and network.get("status") == "ACTIVE", "Network unavailable or wrong network ID")
        if c["availability_zone"] is not None:
            zones = self.cloud("availability", "zone", "list", "--compute")
            require(any(z.get("Zone Name") == c["availability_zone"] and z.get("Zone Status") == "available" for z in zones), "Compute availability zone is unavailable")
        flavor = self.cloud("flavor", "show", c["flavor_id"])
        require(flavor.get("id") == c["flavor_id"], "Wrong flavor ID")
        key = self.cloud("keypair", "show", c["keypair_name"])
        require(key.get("name") == c["keypair_name"], "Wrong keypair")
        public = self.run(["ssh-keygen", "-y", "-f", c["private_key_path"]], timeout=10).stdout.split()
        # OSC intentionally omits public_key from keypair show JSON. Its
        # --public-key mode prints the bare key; do not request a JSON formatter.
        cloud_public = self.run(["openstack", "--os-project-id", c["project_id"], "--os-region-name", c["region"], "keypair", "show", "--public-key", c["keypair_name"]], timeout=90).stdout.split()
        require(public[:2] == cloud_public[:2] and len(public) >= 2, "Private key does not match cloud keypair")
        volume = self.cloud("volume", "show", c["volume_id"])
        require(volume.get("id") == c["volume_id"], "Wrong volume ID")
        require(volume.get("availability_zone") == c["volume_availability_zone"], "Volume availability zone differs from requested placement")
        require(volume.get("os-vol-tenant-attr:tenant_id") == c["project_id"], "Volume project ownership cannot be verified")
        attachments = volume.get("attachments")
        require(isinstance(attachments, list), "Volume attachment metadata is not structured")
        require(volume.get("status") in {"available", "in-use"}, "Volume is not ready")
        require(all(a.get("server_id") == own_id and own_id is not None for a in attachments), "Volume is attached to another instance")
        servers = self.cloud("server", "list")
        require(not any(s.get("Name") == c["deployment_id"] and s.get("ID") != own_id for s in servers), "Deployment name already exists outside this state")
        if c["profile"] == "gpu":
            self.reservation(flavor, own_id)

    def lease(self):
        # Nectar's catalog currently advertises the reservation service root,
        # whereas the OpenStack CLI plugin incorrectly requests /leases there.
        # Use its authenticated catalog endpoint and the documented v1 API.
        try:
            import openstack
            connection = openstack.connect(region_name=self.cfg["region"], project_id=self.cfg["project_id"])
            connection.authorize()
            require(connection.current_project_id == self.cfg["project_id"], "Reservation authentication project mismatch")
            endpoint = connection.session.get_endpoint(service_type="reservation", interface="public", region_name=self.cfg["region"])
            require(isinstance(endpoint, str) and endpoint.startswith("https://"), "Reservation endpoint must use HTTPS")
            endpoint = endpoint.rstrip("/")
            if not endpoint.endswith("/v1"):
                endpoint += "/v1"
            return connection.session.get(endpoint + "/leases/" + self.cfg["lease_id"], timeout=60).json()["lease"]
        except Error:
            raise
        except Exception as exc:
            raise Error("Authenticated reservation API lookup failed") from exc

    def reservation(self, flavor, own_id):
        c = self.cfg
        lease = self.lease()
        require(lease.get("id") == c["lease_id"] and lease.get("project_id") == c["project_id"], "Wrong lease/project")
        require(lease.get("status") == "ACTIVE" and lease.get("degraded") in (False, "False", "false"), "GPU lease is inactive or degraded")
        now = dt.datetime.now(dt.timezone.utc)
        try:
            times = [dt.datetime.fromisoformat(lease[k].replace("Z", "+00:00")) for k in ("start_date", "end_date")]
            start, end = [value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc) for value in times]
        except (KeyError, ValueError, TypeError) as exc:
            raise Error("Lease timing cannot be verified") from exc
        require(start <= now < end, "GPU lease is outside its active time window")
        reservations = lease.get("reservations")
        if isinstance(reservations, str):
            try:
                reservations = json.loads(reservations)
            except ValueError as exc:
                raise Error("Lease reservations must be structured JSON") from exc
        require(isinstance(reservations, list), "Lease reservations unavailable")
        chosen = [r for r in reservations if r.get("id") == c["reservation_id"]]
        require(len(chosen) == 1, "Requested reservation is absent or ambiguous")
        r = chosen[0]
        require(r.get("resource_type") in {"virtual:instance", "flavor:instance"}, "Unsupported reservation resource type")
        require(r.get("status") == "active", "Reservation is not active")
        require(r.get("flavor_id") == c["flavor_id"], "Reservation does not supply the exact requested flavor")
        properties = flavor.get("properties", {})
        require(isinstance(properties, dict), "Flavor extra specs must be structured JSON")
        require(str(properties.get("resources:VGPU", "")).isdigit() and int(properties["resources:VGPU"]) > 0, "Requested flavor does not provide a GPU")
        require(properties.get("aggregate_instance_extra_specs:reservation") == c["reservation_id"], "Flavor reservation extra spec mismatches requested reservation")
        users = self.cloud("server", "list", "--flavor", c["flavor_id"])
        require(not any(s.get("ID") != own_id for s in users), "Reservation flavor is already used by another instance")

    def terraform(self, *args):
        return self.run(["terraform", f"-chdir={TF_ROOT}", *args])

    def initialize(self):
        self.instance_id()  # Validate state before invoking Terraform.
        self.terraform("init", "-input=false", "-reconfigure", f"-backend-config=path={self.state / 'terraform.tfstate'}", "-lockfile=readonly")
        write_json(self.state / "inputs.tfvars.json", {k: self.cfg[k] for k in TF_FIELDS})

    def source_hash(self):
        return digest({p.name: p.read_text() for p in sorted(TF_ROOT.glob("*.tf"))} | {"lock": (TF_ROOT / ".terraform.lock.hcl").read_text()})

    def validate_plan(self, plan):
        for resource in plan.get("resource_changes", []):
            require(resource.get("address") in ALLOWED_RESOURCES and resource.get("mode") == "managed", "Plan contains resources outside compute ownership")
            if resource.get("type") == "openstack_compute_volume_attach_v2":
                for side in ("before", "after"):
                    value = resource.get("change", {}).get(side)
                    if value:
                        require(value.get("volume_id") == self.cfg["volume_id"], "Plan references another data volume")
            if resource.get("type") == "openstack_compute_instance_v2":
                for side in ("before", "after"):
                    value = resource.get("change", {}).get(side)
                    if value:
                        require(value.get("name") == self.cfg["deployment_id"], "Plan references another deployment")

    def plan(self, destroy=False):
        self.scope() if destroy else self.preflight()
        self.initialize()
        path = self.state / ("destroy.tfplan" if destroy else "apply.tfplan")
        path.unlink(missing_ok=True)
        self.terraform("plan", "-input=false", f"-var-file={self.state / 'inputs.tfvars.json'}", f"-out={path}", *(["-destroy"] if destroy else []))
        result = self.terraform("show", "-json", str(path))
        self.validate_plan(json.loads(result.stdout))
        write_json(path.with_suffix(".meta.json"), {"config": digest(self.cfg), "source": self.source_hash(), "plan": hashlib.sha256(path.read_bytes()).hexdigest(), "destroy": destroy})
        # Redacted Terraform's human-readable plan is safe enough for operator review;
        # no provider credentials are input variables in this root.
        print(self.terraform("show", "-no-color", str(path)).stdout)
        print(f"Saved {'compute teardown' if destroy else 'compute'} plan: {path}")

    def apply(self, destroy=False):
        self.scope() if destroy else self.preflight()
        self.initialize()
        path = self.state / ("destroy.tfplan" if destroy else "apply.tfplan")
        require(path.is_file(), "Create and review the corresponding plan first")
        expected = {"config": digest(self.cfg), "source": self.source_hash(), "plan": hashlib.sha256(path.read_bytes()).hexdigest(), "destroy": destroy}
        require(read_json(path.with_suffix(".meta.json")) == expected, "Plan/configuration/source changed; create and review a fresh plan")
        self.validate_plan(json.loads(self.terraform("show", "-json", str(path)).stdout))
        self.terraform("apply", "-input=false", str(path))
        path.unlink()
        path.with_suffix(".meta.json").unlink()
        print("Compute teardown completed; the external data volume is retained." if destroy else "Compute applied. Verify its SSH host key, then run configure.")

    def configure(self):
        plays = [ROOT / "playbooks" / "site.yml"]
        if self.cfg["profile"] == "cpu-web":
            plays.append(ROOT / "cpu-mewc-web" / "web_site.yml")
        for play in plays:
            require(play.is_file(), f"Required playbook is missing: {play}")
        self.scope()
        own_id = self.instance_id()
        require(own_id is not None, "No owned instance in deployment state")
        volume = self.cloud("volume", "show", self.cfg["volume_id"])
        require(volume.get("id") == self.cfg["volume_id"] and volume.get("os-vol-tenant-attr:tenant_id") == self.cfg["project_id"], "Configure volume ownership mismatch")
        attachments = volume.get("attachments")
        require(isinstance(attachments, list) and len(attachments) == 1 and attachments[0].get("server_id") == own_id, "Exact volume attachment to this instance is not verified")
        self.initialize()
        ip = self.terraform("output", "-raw", "instance_ip").stdout.strip()
        try:
            require(ipaddress.ip_address(ip).version == 4, "Expected an IPv4 instance address")
        except ValueError as exc:
            raise Error("Terraform did not return a raw IP address") from exc
        c = self.cfg
        require(Path(c["known_hosts_file"]).is_file(), "Provide known_hosts_file with a host key verified through the cloud console/trusted channel")
        found = self.run(["ssh-keygen", "-F", ip, "-f", c["known_hosts_file"]], timeout=10)
        require(bool(found.stdout.strip()), "Verified known_hosts_file has no entry for this instance IP")
        options = ["-o", "BatchMode=yes", "-o", "PasswordAuthentication=no", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={c['known_hosts_file']}", "-o", "GlobalKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5"]
        deadline = time.monotonic() + c["ssh_timeout_seconds"]
        while True:
            left = deadline - time.monotonic()
            require(left > 0, "SSH/cloud-init readiness deadline exceeded")
            result = self.run(["ssh", "-i", c["private_key_path"], *options, f"{c['ssh_user']}@{ip}", "cloud-init status --wait"], timeout=max(1, min(30, left)), check=False)
            if result.returncode == 0:
                break
            require(not any(t in result.stderr for t in ("REMOTE HOST IDENTIFICATION HAS CHANGED", "Host key verification failed", "Permission denied")), "SSH host identity/authentication failed; refusing to bypass verification")
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        variables = {"ansible_user": c["ssh_user"], "ansible_ssh_private_key_file": c["private_key_path"], "ansible_ssh_common_args": shlex.join(options), "mewc_volume_id": c["volume_id"], "mewc_attachment_verified": True, "mewc_expected_device_serial": c["volume_id"][:20], "mewc_format_volume": c.get("format_volume_id") == c["volume_id"], "mount_point": "/mnt/mewc-volume", "mewc_enable_gpu": c["profile"] == "gpu"}
        if "detector_image" in c:
            variables["detect_image"] = c["detector_image"]
        write_json(self.state / "ansible-vars.json", variables)
        for play in plays:
            self.run(["ansible-playbook", "-i", ip + ",", "--private-key", c["private_key_path"], "-e", "@" + str(self.state / "ansible-vars.json"), str(play)])
        print("Host configuration completed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "plan", "apply", "configure", "destroy-plan", "destroy"])
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        os.umask(0o077)
        cfg = config_load(args.config)
        with deployment_lock(cfg):
            controller = Controller(cfg)
            if args.action in {"plan", "destroy-plan"}:
                controller.plan(destroy=args.action == "destroy-plan")
            elif args.action in {"apply", "destroy"}:
                controller.apply(destroy=args.action == "destroy")
            else:
                getattr(controller, args.action)()
                if args.action == "preflight":
                    print(f"Preflight passed for {cfg['deployment_id']} ({cfg['profile']}); no cloud resources changed.")
    except (Error, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
