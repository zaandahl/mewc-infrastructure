# Infrastructure and pipeline scope

Baseline: `c061a9c89675ee7196f43f7917778198a8457ee6`.

The first hardening PR retained Terraform, OpenStack, Ansible and independently
reusable MEWC containers, and secured new private Ubuntu 24.04 deployments plus
a single-operator detector interface. The subsequent integration adds the full
five-stage command-line workflow and consumes coordinated companion fixes via
[pipeline-sources.lock.json](../pipeline-sources.lock.json). See [pipeline.md](pipeline.md).

## Decisions

- Each deployment has explicit immutable identity and private state outside the
  checkout. A name change is not a way to select another deployment.
- Compute references an exact existing Cinder volume ID; its Terraform state
  does not own the volume. Compute teardown retains storage. Permanent volume
  deletion remains a separate operator action after verified export.
- New blank storage is formatted only with explicit authority, after the full
  cloud attachment identity and guest device identity have been checked.
- Reserved GPU placement is selected by Nectar. Volume placement is explicit.
  There is no CPU fallback for a missing GPU reservation.
- Retain both Docker and containerd storage on the verified volume. Existing
  runtime stores require a deliberate migration, not an automatic move.
- The optional web profile is private, detector-only and single-operator.
  A separate worker owns runtime privileges. Public multi-user hosting is outside
  this change's support contract.
- Freeze detector/model and scientific settings. Report failed images and
  incomplete outputs explicitly; no implicit synthetic detections.
- Legacy Terraform roots are blocked against accidental operation. Existing
  resources/state are not imported or migrated automatically.

## Review and evidence

The two September 2026 reviews supply findings and baseline characterisations.
Their diagnostic success means the old defects were reproduced. New tests assert
correct behaviour. Review identifiers are mapped to changes and evidence in
`audit-response.md`.

Verification proceeds from unit/contract checks, controller build and Terraform/
Ansible validation to new disposable CPU/GPU instances. Public CI uses synthetic
inputs and no cloud credentials. Runtime, retention, reboot and cleanup evidence
must be labelled separately from static or mocked checks.

## Companion integration

The follow-up integrates detector exit propagation, original detection-index crop
identities, immutable predictions, complete probability vectors, explicit model
axis-to-class-code mapping, lossless Camelot exports, and eligible-only boxing.
A versioned source lock builds the compatible image graph without vendoring or
patching installed stage code. The optional web remains detector-only.

New runs use the operator-approved `category-confidence-v1` policy: descending
confidence suppression only within a detector category, preserving configured
numeric thresholds. Mixed categories export to `mixed`. Existing results are
never migrated or relabelled. Exact classification ties remain canonical; the
single-class Camelot adapter fails explicitly when it cannot represent them.

The operator reported that the original 24-site photographs had been removed,
and supplied a separate small JPEG example for integration testing. Frozen old
crops support an execution-equivalence comparison, not a new claim about training
labels or species accuracy. Evidence belongs in the pipeline validation record.
