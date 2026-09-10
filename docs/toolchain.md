# Toolchain and provenance

The initial compatibility target is Linux amd64, Python 3.12 and Ubuntu 24.04
remote hosts. Native ARM controllers and emulation are not validated. The
controller does not run scientific workloads locally.

| Component | Selection |
| --- | --- |
| Python controller image | `python:3.12-slim-bookworm` pinned by digest in Dockerfile. |
| Terraform | 1.16.2, amd64 archive verified against the pinned SHA256. |
| OpenStack provider | 3.4.0 with checked-in provider checksum lock. |
| Ansible core / OpenStack CLI / SDK | 2.21.4 / 10.3.0 / 4.20.0; full hash lock in `requirements-controller.txt`. |
| Ansible collections | Exact versions in `ansible-requirements.yml`, including the transitive collection. |
| Web dependencies | Complete hash lock in the web role, scanned alongside controller dependencies. |
| NVIDIA Container Toolkit | Four matching 1.20.0-1 packages from the signed NVIDIA repository; key checksum pinned. |
| Detector | Explicit image digest; tested 6.0.0 digest recorded in the web guide and validation report. |

Nectar lease inspection uses the authenticated reservation service catalog and
`/v1/leases/<exact-id>` through the locked SDK. It does not scrape CLI tables or
install a separate Blazar CLI plugin. GPU preflight checks project, lease timing,
reservation status, exact flavor/reservation extra specs and conflicting use.
The cloud scheduler remains authoritative under concurrent allocation requests.
The local deployment lock serializes commands on one operator machine only.

This is a controlled dependency set, not a bit-for-bit hermetic operating-system
build: Debian controller packages, Ubuntu Docker/containerd candidates, cloud
images and external repositories can change. Pin host package overrides when
needed; actual host versions are saved in `/etc/mewc-host-packages.txt`. Record
the exact Nectar image UUID and driver version in each private run manifest.
Neither provisioning nor a rerun upgrades an already installed runtime by default.

To update Python locks, edit the `.in` files and regenerate with `uv pip compile
--generate-hashes --python-version 3.12`; retain all transitive hashes. Rebuild,
run contract tests and dependency audits, then repeat affected disposable-host
canaries. Updating a workload digest/model requires compatibility and scientific
review appropriate to the workload. The private detector uses the immutable
image's built-in model unless an explicit model is supplied; custom models get a
SHA256 record. No stage image is pulled implicitly as `latest`.

CI has no cloud credentials. It builds the controller and checks the provider
schema, but neither provisions Nectar nor measures scientific model accuracy.
