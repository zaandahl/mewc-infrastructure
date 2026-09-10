# Infrastructure hardening scope

Baseline: `c061a9c89675ee7196f43f7917778198a8457ee6`.

This change retains Terraform, OpenStack, Ansible and independently reusable
MEWC containers. It targets new private Ubuntu 24.04 deployments and a private,
single-operator detector interface. It does not implement the complete five-stage
MEWC pipeline or repair companion repositories.

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

## Companion work

The detector subprocess exit contract, crop identity/indexing, prediction rename
and tie handling, metadata joins and boxing/filter semantics need coordinated
fixes in their owning repositories. This PR must not claim those findings closed.
A complete five-stage runner, performance optimisation, full dataset rehearsal,
public hosting and RStudio support are subsequent work.
