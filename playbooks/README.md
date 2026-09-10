# Supported host setup

Run `site.yml` on Ubuntu 24.04 with explicit inventory SSH user, private key, and
verified known-hosts file. The controller supplies these variables:

| Variable | Contract |
| --- | --- |
| `mewc_volume_id` | Required full Cinder volume UUID attached to this instance. |
| `mount_point` | Default `/mnt/mewc-volume`; a single directory under `/mnt`. |
| `mewc_format_volume` | Default false. True authorises initialising only the selected blank, signature-free whole disk as ext4. Existing filesystems are never reformatted. |
| `mewc_expected_device_serial` | Optional exact serial. Only the full canonical UUID or its first 20 characters (virtio serial limit) is supported. |
| `mewc_attachment_verified` | Must be true when a serial override is supplied. The controller must first verify the full volume UUID is attached to the exact selected instance through the authenticated cloud API. This is an attestation, not a device-name shortcut. |
| `mewc_enable_gpu` | Default false. True requires the image's already functioning NVIDIA driver. |
| `mewc_images` | Default empty list; selected `repository@sha256:<64 lowercase hex>` images only. No implicit training or `latest` pulls. |
| `mewc_docker_package_version`, `mewc_containerd_package_version` | Optional exact Ubuntu package versions. Without overrides, install the distribution candidate if absent; do not upgrade existing packages. Actual versions are recorded in `/etc/mewc-host-packages.txt`. |
| `mewc_nvidia_toolkit_version` | Default `1.20.0-1` for all four NVIDIA toolkit packages. No driver installation or replacement. |
| `mewc_researchers` | Default empty list. Each entry requires `name` and a complete `authorized_keys` string. Accounts receive locked passwords and no supplementary privileged groups; existing direct sudo grants cause failure. |

Storage is inspected before package installation. Device selection rejects an
absent/ambiguous serial, root/partitioned/read-only disks, unfamiliar signatures,
foreign mounts, and unsafe mount directories. Truncated serial matching requires
both the control-plane attestation and the exact expected transform. Mounting uses
the filesystem UUID; every runtime startup checks both its Cinder identity and
the mounted filesystem identity.

`docker.service`, `docker.socket`, and `containerd.service` depend on the mount;
the service guards prevent root-storage fallback. The disk remains `nofail` in
fstab so a missing research volume does not prevent administrator SSH recovery.
Application services must also depend on the mount and run the root-owned
storage guard with sufficient privileges in the host mount namespace. The web
role supplies the tested `RequiresMountsFor`/`BindsTo` and fixed privileged
`nsenter` pre-start command; its application processes remain unprivileged. A
direct unprivileged guard inside a private device/mount sandbox is insufficient.

The runtime helper merges Docker's `data-root` while retaining other JSON
settings. It changes only containerd's top-level `root`, preserving plugin
configuration and unrelated comments. Unreviewed imported containerd configurations and
custom systemd runtime overrides are refused. The previous repository's exact
Docker override is recognised and replaced with the managed configuration.
Configuration changes require idle workloads in every containerd namespace;
unchanged reruns do not stop/restart services or reload systemd.

## Existing runtime data

Provisioning never moves or deletes runtime data. If an old runtime location
contains files, setup stops with the path requiring migration. An administrator
must schedule downtime, stop workload services and socket activation, stop both
runtimes, create a recoverable backup, and explicitly migrate data with ownership,
hardlinks, extended attributes and sparse files preserved. Reconcile Docker and
containerd configuration to the verified mounted destination, verify retained
images/containers, and retain the old copy until validation and recovery checks
pass. Custom service arguments and containerd imports need the same review.
There is intentionally no automatic migration flag.

## GPU source and validation

Toolkit setup follows NVIDIA's [official installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
checked 2026-09-10: signed production `stable/deb/amd64` repository, four matching
versioned packages, and `nvidia-ctk runtime configure --runtime=docker`. The
repository signing key is pinned by SHA256. The Nectar vGPU image's licensed
driver remains untouched. Toolkit integration does not install the CUDA SDK or
run a floating CUDA test image.

Passing `nvidia-smi` and Docker runtime registration verifies integration only.
Release validation must run actual GPU inference with the chosen immutable image
and model, rerun provisioning, reboot, recheck both runtime data paths and volume
identity, and test missing/wrong-volume failure on disposable resources.

Local fixture checks: `python3 -m unittest discover -s tests -p 'test_host*.py'`.
PowerShell remains outside the supported path; its legacy entry point explains
that an independently reviewed pinned installation is needed.
