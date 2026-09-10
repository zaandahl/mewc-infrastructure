# Response to the September 2026 infrastructure reviews

Baseline: `c061a9c89675ee7196f43f7917778198a8457ee6`. The first review uses
`I/W/S/D` identifiers; the second uses `F01–F16`. This table maps the implemented
boundary, not a claim that every release gate or companion repository is fixed.
Private operational logs, datasets, credential material and old state are excluded.

| Findings | Response in this change | Evidence / boundary |
| --- | --- | --- |
| I1, F07 | Per-deployment external state; Terraform owns compute, SSH group/rule and attachment only. Separate saved teardown plan; volume deletion absent. | Infrastructure contract tests and disposable retention evidence. Legacy roots blocked; existing-state migration requires review. |
| I2, F13 | Exact lease/reservation/flavor and full project/resource identities, structured APIs, fail-closed checks, no GPU-to-CPU fallback. | Negative-selection fixtures and live reserved GPU provisioning. One-machine command lock is not a distributed reservation lock. |
| I3, F09 | One command layer, bounded verified SSH/cloud-init, explicit key, propagated failures, non-overwriting key creation. | Failure/identity/plan fixtures, live configure. Resources remain for recovery after failure. |
| I4, F10 | Cloud attachment attestation plus guest serial/filesystem checks before formatting or runtime setup; retain Docker and containerd; guarded mount dependencies. | Storage/runtime fixtures, live rerun/reboot/missing-mount checks recorded separately. Existing runtime migration is explicit. |
| I5, F01 | Remove reusable password configuration; optional named key-only nonprivileged researcher accounts. | Syntax and contract checks. Existing deployed credentials are not remediated by a repository change. |
| I5, F08 | Remove tracked state backup, ignore state/plan variants and secrets; track provider lock. | Git review. History/forks retain earlier copies; no unrequested history rewrite or credential rotation. |
| I6, F06, F15 | Pinned controller and Terraform checksums, hash-locked Python stacks, exact collections/provider/toolkit versions, selected workload digests, minimal Docker context and CI. | Build, dependency audits and static checks. OS package/image reproducibility limits in toolchain.md; no full detector-image vulnerability certification. |
| W1, F04 | Central path validation/containment, ZIP special-file rejection, strict job IDs, worker-owned snapshots and outputs. | ASGI direct/ZIP/path/download fixtures. |
| W2, F02 | Loopback nginx/API and restricted SSH security group; single-operator tunnel contract with origin/Host checks. | Service/network checks and negative origin tests. Public authenticated multi-user hosting remains out of scope. |
| W2, F03 | Root-owned code/config, non-login API with no Docker/sudo access, separate trusted Docker worker, narrow spool. | Unit definitions, ownership/sandbox live checks. Worker remains a privileged trusted component. |
| W3, F05 | Bound actual multipart bytes, expanded ZIP bytes/count, image decoding, space admission, proxy buffers and logs; serialize upload admission and jobs. | Real ASGI multipart/ZIP fixtures. No automatic archival deletion or multi-tenant quotas. |
| W4, F11 | No synthetic production fallback; fresh attempts, strict exit/JSON/completeness validation, explicit failed-image accounting, cancellation/timeout/restart cleanup. | Real detector smoke plus malformed/stale/partial/failure fixtures. First PR wrapper validated complete output independently; the companion follow-up now propagates detector exit status. |
| W5, F12 | One canonical converter preserves confidence zero, labels, nested paths and failed-image distinctions. | Converter/API/export fixtures; real synthetic-image downloads. Detection categories are not species predictions. |
| W5, F14 | Shared storage contract, one durable worker, bounded resources/pagination and explicit queued/running/terminal states. | Concurrency, retry, cancellation and API tests. Large-gallery performance optimisation remains future work. |
| D1, F16 | Rewrite quickstart; document supported profiles, state, private UI, migration, recovery, retention and evidence limits. | Source/command consistency review. Independent operator reproduction remains a maintainer release step. |
| I7, S1–S6 | Document companion exit, crop identity/indexing, prediction, metadata and boxing contracts requiring coordinated work. | First PR deferred these findings; coordinated fixes and the full runner are covered by the follow-up below. No scientific accuracy claim. |

Independent review of the implementation found two further defects: cancellation
could claim success without verified container removal, and a queued retry could
serve previous results. Both were repaired with regressions and independently
rechecked. Cleanup uncertainty now retains working files and blocks subsequent
jobs; queued retries reject previous-result downloads across routes.

The staged review's W00–W08 workstreams are addressed only to the new private
infrastructure/detector boundary documented in implementation.md. This change is
not approval for public hosting, automatic migration, permanent storage deletion,
companion-stage release or biological inference.

## Complete pipeline follow-up

| Finding | Repair and evidence boundary |
| --- | --- |
| I7 | Versioned source/image graph; required fixed parent builds; no branch-triggered registry publishing; host runner checks immutable configuration, model assets, image IDs and retained storage. |
| S1 | Detector subprocess argv and exact exit propagation; separate output root supports read-only input mounts. |
| S2 | Every original detection index has a crop or explicit omission; runner recomputes expected eligibility and checks exact identities, including person-first fixtures. |
| S3 | Original JPEG/EXIF bytes preserved; source/index joins; flash bit decoding; timestamp provenance; canonical records plus lossless Camelot copies and output-collision rejection. |
| S4 | Crops never renamed; interrupted writes cannot complete; full score archive, CSV and pickle reconciled inside an isolated image; tied maxima preserved. |
| S5 | Strict typed options and architecture validation; explicit output-axis to original class-code mapping, class order, model/class-map hashes, shape and probability checks. Historical training provenance is not inferred. |
| S6 | Approved same-category confidence policy shared by crop/metadata/boxing; typed drawing/sorting options; mixed folder and no-EXIF handling. |

The runner validates stage content before reuse, rejects changed run identities,
retains failed attempts, and independently checks export hashes. It freezes input
modification times because metadata may use them as an explicit timestamp fallback.
The private web keeps its bounded detector-only contract. See [pipeline.md](pipeline.md)
and [pipeline-validation.md](pipeline-validation.md) for supported execution and evidence.
