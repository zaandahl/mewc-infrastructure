# Complete pipeline validation

This follow-up tests the coordinated source graph in
[pipeline-sources.lock.json](../pipeline-sources.lock.json). Private photographs,
model weights, class maps and operational credentials are excluded from the PR.
The source/image locks and aggregate results make the tested boundary explicit.

## Dataset and scientific boundary

The operator supplied 43 JPEG photographs for the complete pipeline rehearsal.
The original 24-site photographs were no longer available, so this is not a
repetition of that original dataset run. Twelve byte-verified historical crops
from three sites provide a separate classifier execution comparison. Their old
CSV labels are historical predictions, not adjudicated species identifications.

The frozen classifier is a 96-class VTL Keras model with noncontiguous original
string class codes. Its explicit axis declaration preserves the historical
predictor's lexical code ordering. Training/export class order remains unverified.
Runtime shape and hash checks do not resolve that missing scientific provenance.

New runs use the operator-approved `category-confidence-v1` selection policy and
`mixed` review sorting. Historical thresholds are preserved: detector floor 0.2,
lower/upper eligibility confidence 0.1/0.8, overlap 0.25, edge distance 0.05,
minimum edges 0, snip size 600 and classifier batch size 1. Input preprocessing
remains RGB, bilinear 384 x 384 resize, float32 [0,255], with no external
normalization. Every exact maximum is retained; a singular Camelot tag cannot
represent an ambiguous class and fails explicitly.

## Credential-free checks

The infrastructure suite passes 115 tests and 44 subtests, including interrupted
stage recovery, stale output invalidation, missing image/crop identities, complete
score reconciliation, filesystem replacement/loss, original modification times,
model snapshot changes, frozen runner sources, owned-container reconciliation,
partial export ownership and corrupted-transfer rejection.

The companion suites cover typed options, detector failure propagation, original
crop indices, output axis/original-code distinctions, exact ties, full scores,
lossless metadata copies and shipped CLI configuration. GitHub runs the locked
lightweight dependencies without cloud credentials. Real ML execution is separate
from those mocked/synthetic tests.

An independent bounded review found and verified repairs for export
self-certification, unsafe partial paths, source/model integrity, container
ownership/reconciliation and storage checks. The host runner now checks the exact
configured filesystem UUID before staging, execution and writes.

## Runtime checks

Runtime evidence is being finalized against fresh run identities after the
numerical execution repair below. The disposable host uses Linux amd64, an A100
40 GB GPU, Docker/containerd on a separate retained filesystem, NVIDIA driver
580.178.04 and toolkit 1.20.0-1. The classifier runtime remains TensorFlow 2.16.1
and Keras 3.3.3; these inherited versions are frozen, not a general dependency
security certification.

The initial real run verified detection for all 43 images, 45 eligible animal
crops and 45 complete prediction rows. It then correctly failed at a metadata
CLI packaging defect, and refused export. Metadata and boxing now load their own
YAML configuration rather than depending on a helper or defaults inherited from
the wrong runtime family. Their repaired CLI entry points and packaged defaults
have regression tests. Separate runtime checks exported metadata for all 43
images and rendered 43 boxed copies with all original hashes unchanged.

## Numerical execution gate

Before testing, the 12-crop comparison fixed `rtol=1e-5` and `atol=1e-6`, identical
frozen tensors, all 96 probabilities and exact top-class sets. The baseline used
historical `model.predict` execution with the loaded Keras/JIT defaults.

An initial direct-call candidate (`model(batch, training=False)`) failed:
238 of 1,152 probabilities exceeded tolerance across all 12 crops; maximum
absolute difference was 0.0013530254364013672. Top-class sets matched for all
12 and no exact ties occurred. The probability failure rejected that candidate;
matching labels did not excuse it, and the tolerance was not changed.

The repair restores one historical Keras `model.predict` call over the complete
ordered dataset, followed by the existing shape/probability validation. All sites
remain in that dataset, so the model is loaded once. Empty crops continue to skip
model deserialization. SavedModel signatures retain their separate explicit
contract. The follow-up comparison must also agree with the persisted first
baseline, rather than only with a second call in the same process.

Timing observations include initial graph tracing and are not a controlled
performance comparison. The large model took about 456 seconds to load in the
first numerical canary; repeated cached ZIP/HDF5 reads dominated that stage.

## Release and retention boundary

The companion branches and source/image graph are coordinated review artifacts.
No automatic registry release or `latest` tag is published. Maintainers must
publish compatible parent/child images deliberately and record the resulting
OCI digests before distributed deployment.

Successful export verification proves file transport and recorded pipeline
integrity. It does not establish ecological accuracy, abundance, biological
independence of events or the missing training class-order provenance. Original
input/model snapshots must be retained separately from the result export.
