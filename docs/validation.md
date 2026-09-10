# Validation record

Validation date: 2026-09-10. Baseline:
`c061a9c89675ee7196f43f7917778198a8457ee6`. This record covers new disposable
Nectar hosts, synthetic images and private detector operation. No ecological
accuracy, species-classification or full five-stage pipeline claim follows.
The existing operator VM and all pre-existing volumes were excluded from mutation.

## Local and controller checks

- Python contract suite: 94 tests and 44 subtests passed; two dependency
  deprecation warnings, no failed tests. The final suite includes the live-compatibility regressions.
- Terraform 1.16.2 and OpenStack provider 3.4.0: locked init and schema validation
  passed. The controller Docker image built successfully on a disposable CPU host;
  its Terraform init/validate and command help ran successfully from that image.
  Live cloud commands used the same locked native Python environment; this is
  separate from the container build/schema check.
- Hash-locked controller and web Python dependencies: `pip-audit` reported no
  known vulnerabilities on the validation date. This does not certify the
  separately maintained detector image or every OS package.
- Python compilation, production-source Ruff and ShellCheck passed. Combined host/web Ansible production lint passed across 11 files. The narrow role-variable naming exemption preserves the documented
  operator/controller configuration interface.
- Independent implementation review found two additional result-handling defects;
  both fixes passed a separate 52-test web recheck. Tests distinguish mocked
  Docker failures from actual runtime evidence below.

## Disposable host checks

| Check | Recorded outcome |
| --- | --- |
| CPU host | Ubuntu 24.04, 4 vCPU / 8 GiB, separate 120 GiB data volume; controller build and private detector workload run on the data volume. |
| GPU allocation | Exact active reserved GPU flavor; no compute AZ override. New Ubuntu 24.04 vGPU image and separate 150 GiB volume. |
| GPU host | GRID A100D-40C, 40 GiB GPU memory, existing driver 580.178.04 preserved. Initial configure passed; identical second play: 47 OK, 0 changed, 0 failed. |
| Host identity | Each SSH key matched the public key from its authenticated cloud console before strict SSH use. |
| Device identity | Full Cinder attachment checked through API, exact virtio20 serial matched in guest; filesystem mounted by UUID. |
| Private service startup | Actual systemd units active; privileged guard enters the host mount namespace to avoid service bind-mount aliases. Main API/worker commands remain under their configured accounts and sandbox restrictions. |
| CPU real inference | Three synthetic JPEGs from direct + nested ZIP upload, pinned detector 6.0.0 with built-in `md_v1000.0.0-redwood.pt`; 3 processed, 0 failed, valid zero detections. CPU limit 2, memory 4 GiB. Canonical header-only CSV and encoded nested raw-image download passed. |
| GPU real inference | The same pinned image executed a CUDA matrix calculation and real MegaDetector inference under non-root, read-only-root, network-disabled restrictions. One synthetic JPEG was accounted for in fresh valid output; GPU=True was confirmed in the detector log. This checks GPU execution and output integrity, not accuracy. |
| Cancellation / retry / UI | Real headless Chromium ZIP upload, start, polling and downloads passed. An explicitly supplied read-only copy of the same model processed 2/2 images and matched its provenance hash. A running container was observed before cancellation and independently verified absent afterwards. Retries hid stale downloads; source/snapshot hashes matched provenance. |
| Reboot / missing mount | GPU reboot preserved driver, runtime roots, image cache and data/output hashes. On CPU, stopping the mount stopped all six dependent runtime/web units; with the exact data disk detached, startup failed for all six, the guard rejected the absent device, and root runtime paths stayed unchanged. Reattachment restored services and the sentinel. CPU reboot restored all six services and preserved the sentinel plus six exported MD/CSV hashes; a temporary wrong-UUID guard configuration was also rejected. |
| Compute teardown / retained-volume recovery | Reviewed GPU teardown deleted only four compute/SSH/attachment resources and left an empty compute state. The exact 150 GiB volume remained available. Re-plan/apply/configure created a replacement instance around that volume; filesystem UUID, sentinel SHA256, MD output SHA256 and detector image digest were unchanged. Both runtime roots and the GPU driver were correct. No reformatting occurred. |
| Disposable resource cleanup | Both GPU instance generations and the CPU instance were removed; the two synthetic-test volumes, test security groups and test keypair were deleted after export and retention verification. No test resources remained. The excluded operator VM stayed ACTIVE with its original attachment, all four pre-existing volumes remained present, and the GPU reservation was not changed. |

The first real host run caught a quote-style assumption in containerd verification;
verification now parses TOML structurally. Actual service startup also exposed
mount aliases introduced by systemd sandboxing; the storage guard now runs in the
host mount namespace without weakening its device/UUID checks. These findings
were repaired and are covered by the final reruns reported here.

## Reproduction and release boundary

Run the README's new-deployment sequence in a private operator directory. Preserve
local state, configuration, plans, verified host keys and full logs privately;
record package/image/model versions and hash a retention sentinel. Use only new
disposable resources for missing/wrong-volume and teardown tests. Review every
plan to ensure it references only the selected compute and external attachment.

Public CI uses synthetic fixtures and no cloud credentials. The maintainer should
repeat affected cloud canaries when changing images, dependencies, storage rules
or runtime restrictions. This report is a dated implementation check, not ongoing
monitoring. Native ARM/emulation, legacy deployment migration, public hosting,
full detector image security certification, companion-stage repairs, performance
benchmarks and scientific accuracy validation remain outside this scope.
