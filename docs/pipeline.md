# Running the complete inference pipeline

The command-line pipeline runs detection, snipping, prediction, canonical metadata/Camelot export, and boxed review output on an existing prepared Linux amd64 host. It snapshots inputs and models, verifies identities and content after each stage, and exports only a verified complete run. The web application remains detector-only.

The examples below use synthetic paths and class codes. Replace every `REPLACE_WITH_...` value and size resources for the host. They are not records of a completed dataset run. Validation evidence is maintained separately in [pipeline-validation.md](pipeline-validation.md); this guide does not claim a complete 24-site rehearsal.

## 1. Check the prepared host

Use Ubuntu 24.04 or another compatible host with Python 3.12+, Git, rsync, Docker, and an existing separate storage mount. GPU runs also need a working NVIDIA driver and Docker GPU runtime. The runner does not provision these services.

The example mount is `/srv/mewc`. It must be a mount point on a different filesystem from `/`, with run directories beneath it. Set `storage_uuid` to the filesystem UUID reported by `findmnt --mountpoint /srv/mewc --output UUID --noheadings`; the runner checks that exact UUID before staging, stage execution and writes. Docker's data directory must also be beneath this mount. A directory merely named `/srv/mewc` on the root disk fails preflight. Confirm the retained volume is mounted before creating directories or transferring data:

```bash
mountpoint /srv/mewc
findmnt -T /srv/mewc
df -h /srv/mewc
docker info --format '{{.DockerRootDir}}'
nvidia-smi
```

Run `nvidia-smi` when using `gpu: true`. Leave room for Docker images, the input/model snapshots, crops, outputs and retained failed attempts. Storage configuration and any Docker data-directory migration belong to host preparation.

The operator must be able to run Docker directly. On a newly prepared Ubuntu host where the operator is `ubuntu`, an administrator can grant that access:

```bash
sudo usermod -aG docker ubuntu
```

End that SSH session and reconnect before checking `docker info`; the existing session does not acquire the new group. Use an operator-owned workspace on the retained mount. Docker group membership grants privileged host access.

## 2. Check out the locked sources and build the images

Check out the reviewed infrastructure commit, including its source lock. Do not substitute a moving branch or an older published stage image if a locked commit is unavailable.

```bash
INFRA_REPOSITORY_URL='REPLACE_WITH_REVIEWED_INFRASTRUCTURE_REPOSITORY_URL'
INFRA_COMMIT='REPLACE_WITH_REVIEWED_FULL_COMMIT'
mkdir -p /srv/mewc/src /srv/mewc/locks
git clone "$INFRA_REPOSITORY_URL" /srv/mewc/src/mewc-infrastructure
git -C /srv/mewc/src/mewc-infrastructure checkout --detach "$INFRA_COMMIT"
cd /srv/mewc/src/mewc-infrastructure
```

Fetch each companion repository and check out exactly the commit named by [pipeline-sources.lock.json](../pipeline-sources.lock.json). This command expects a fresh companion checkout directory and remotes that expose the locked commits:

```bash
python3 - <<'PY'
import json
from pathlib import Path
import subprocess

sources = json.loads(Path('pipeline-sources.lock.json').read_text())['sources']
parent = Path('/srv/mewc/src/companions')
parent.mkdir(parents=True, exist_ok=True)
for role, source in sources.items():
    checkout = parent / f'mewc-{role}'
    subprocess.run(['git', 'clone', '--no-checkout', source['repository'], str(checkout)], check=True)
    subprocess.run(['git', '-C', str(checkout), 'fetch', 'origin', source['commit']], check=True)
    subprocess.run(['git', '-C', str(checkout), 'checkout', '--detach', source['commit']], check=True)
PY
python3 scripts/build_pipeline_images.py \
  --sources pipeline-sources.lock.json \
  --checkouts /srv/mewc/src/companions \
  --output /srv/mewc/locks/images-example.json
```

The builder creates six images in dependency order: detect, flow, snip, predict, EXIF and box. Flow supplies the prediction runtime; the generated `images` object contains the five executable stage image IDs. Builds use clean Git archives of the locked commits, record source archive hashes and parent image IDs, and require Linux amd64 output. See each companion's `BUILDING.md` for the parent-image contract.

`images-example.json` records immutable local `sha256:` image IDs. It is not a DockerHub release or a claim that these fixes have been published. The detector/flow bases freeze an existing runtime; application integrity fixes do not establish that every inherited dependency is current. Preserve this generated lock. To move local builds to another host, also preserve a Docker image archive, load it on that host, and use the same IDs:

```bash
python3 - <<'PY'
import json
from pathlib import Path
import subprocess

lock = json.loads(Path('/srv/mewc/locks/images-example.json').read_text())
ids = [build['image_id'] for build in lock['builds'].values()]
subprocess.run(['docker', 'image', 'save', '--output',
                '/srv/mewc/locks/images-example.tar', *ids], check=True)
PY
# After transferring both archive and lock to the other prepared host:
docker image load --input /srv/mewc/locks/images-example.tar
```

## 3. Declare the model and transfer inputs with hashes

The full Camelot pipeline accepts JPEG files only, including nested `.jpg` and `.jpeg` paths. Create a dedicated input tree containing only those files, with no symlinks, checksum files or other sidecars inside it. Keep equal basenames at different sites in their original subdirectories.

Prepare detector weights, the classifier `.keras` file, its class-map YAML, and a model manifest. Start from [model-manifest.json](../examples/model-manifest.json), replacing its synthetic architecture, dimensions, class identities and digests with the actual model declaration. The supported preprocessing declaration must match the predictor: RGB, bilinear resize, no aspect-ratio crop, float32 values in `[0,255]`, and no external normalization. Do not alter these fields to conceal an incompatible model.

`class_ids` declares the model output axis in order using the **original class-map codes**. Codes can be strings and noncontiguous. `class_order` declares the corresponding names in exactly the same order. The synthetic example means output column 0 is code `"10"` and column 1 is code `"999"`; it does not renumber these classes to 0 and 1. Its matching YAML is:

```yaml
"10": example_species_a
"999": example_species_b
```

Preserve the original YAML key types. Canonical prediction records distinguish `class_id` from the zero-based `class_index`; the score archive preserves ordered codes. CSV readers may infer numeric types, so use the pickle/NPZ and the declared code order when that distinction matters. Camelot camera tags require representable numeric class codes; they cannot represent arbitrary string labels.

Set `model_sha256` to the classifier file's SHA-256 and `class_map_sha256` to the class-map file's SHA-256. The runner separately hashes the detector and all four model assets. Architecture and input shape must agree with the actual model; matching dimensions alone do not prove the architecture or training class order. The example uses `class_order_provenance: historical-unverified` to retain unknown historical training/export provenance. Use `training-export-verified` only with that evidence; other declarations remain explicitly unverified in prediction provenance.

On the sending machine, set paths to prepared directories. Finish the model manifest before computing transfer checksums:

```bash
PIPELINE_HOST='REPLACE_WITH_PREPARED_HOST_SSH_ALIAS'
LOCAL_JPEG_DIR='/absolute/path/to/example-jpeg'
LOCAL_MODELS_DIR='/absolute/path/to/example-models'

(cd "$LOCAL_JPEG_DIR" && find . -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) -print0 | sort -z | xargs -0 -r sha256sum) > "$LOCAL_JPEG_DIR.sha256"
(cd "$LOCAL_MODELS_DIR" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum) > "$LOCAL_MODELS_DIR.sha256"
ssh "$PIPELINE_HOST" 'mkdir -p /srv/mewc/datasets/example-jpeg /srv/mewc/models/example /srv/mewc/manifests'
rsync -a --checksum --prune-empty-dirs \
  --include='*/' --include='*.[jJ][pP][gG]' --include='*.[jJ][pP][eE][gG]' --exclude='*' \
  "$LOCAL_JPEG_DIR/" "$PIPELINE_HOST:/srv/mewc/datasets/example-jpeg/"
rsync -a --checksum "$LOCAL_MODELS_DIR/" "$PIPELINE_HOST:/srv/mewc/models/example/"
rsync -a "$LOCAL_JPEG_DIR.sha256" "$PIPELINE_HOST:/srv/mewc/manifests/example-jpeg.sha256"
rsync -a "$LOCAL_MODELS_DIR.sha256" "$PIPELINE_HOST:/srv/mewc/manifests/example-models.sha256"
ssh "$PIPELINE_HOST" 'cd /srv/mewc/datasets/example-jpeg && sha256sum -c /srv/mewc/manifests/example-jpeg.sha256'
ssh "$PIPELINE_HOST" 'cd /srv/mewc/models/example && sha256sum -c /srv/mewc/manifests/example-models.sha256'
```

Use fresh destination trees; these commands do not delete unrelated files already there. Preserve source modification times, because metadata extraction records a filesystem-time fallback when camera timestamps are absent.

## 4. Configure and run

On the prepared host, create an operator configuration from [pipeline.json](../examples/pipeline.json). Its `sha256:REPLACE_WITH_...` values are intentionally unusable placeholders. Insert the generated stage IDs automatically, then review the actual storage, input, model and run paths and resource limits:

```bash
cd /srv/mewc/src/mewc-infrastructure
mkdir -p /srv/mewc/config
python3 - <<'PY'
import json
from pathlib import Path

config = json.loads(Path('examples/pipeline.json').read_text())
config['images'] = json.loads(Path('/srv/mewc/locks/images-example.json').read_text())['images']
Path('/srv/mewc/config/example-run-001.json').write_text(json.dumps(config, indent=2) + '\n')
PY
python3 -m mewc_pipeline preflight --config /srv/mewc/config/example-run-001.json
python3 -m mewc_pipeline stage --config /srv/mewc/config/example-run-001.json
python3 -m mewc_pipeline run --config /srv/mewc/config/example-run-001.json
```

| Action | Contract |
| --- | --- |
| `preflight` | Checks model declarations/hashes, available immutable images, host/runtime/storage and the JPEG inventory. It does not prove classifier inference on a nonempty batch. |
| `stage` | Freezes the run identity and copies verified inputs/model assets into the run directory. |
| `run` | Stages if necessary, runs five stages, verifies their artifacts and reuses only completed outputs that still pass validation. |
| `status` | Reads recorded run/stage states; it does not certify file integrity. |
| `verify` | Rechecks completed run snapshots, stage files and cross-stage identities and writes `verification.json`. |
| `export` | Verifies first, then creates a separate hashed export at a fresh destination. |
| `verify-export` | Checks the transferred export inventory and hashes independently of its original run/configuration. |

The integrated scientific policy is explicit: `category-confidence-v1` selects eligible detections using higher-confidence-first suppression within each detector category, with the configured numeric thresholds. Snips retain eligible animals and their original detection indices. Mixed eligible categories go to the review `mixed` folder. `DRAW=false` disables drawing and preserves copied image bytes; it does not disable the final stage or its verification.

`THRESHOLD` is the detector output floor; `LOWER_CONF` is the downstream eligibility threshold. Preserve both declared values. The detector floor may exceed `LOWER_CONF`, in which case lower-scoring detections are absent from its output. Configuration requires `LOWER_CONF <= UPPER_CONF` without imposing an ordering between `THRESHOLD` and `LOWER_CONF`.

With `TOP_CLASSES=true`, every exact maximum is retained, including ties. Full probability vectors remain in `prediction_scores.npz`. Canonical metadata preserves those rows. The singular Camelot camera-field adapter refuses an ambiguous classification it cannot represent; that failure prevents a certified full-pipeline export. Change the policy only through an explicit model/scientific decision and a new run.

A dataset with no eligible animal crops still runs detection, accounts for omissions, produces empty prediction artifacts, copies metadata/review images and completes as `success-empty`. The predictor skips classifier model loading/inference and records `model_runtime_validated=false`; this outcome does not validate the classifier runtime.

## 5. Inspect, resume and export

```bash
python3 -m mewc_pipeline status --config /srv/mewc/config/example-run-001.json
python3 -m mewc_pipeline verify --config /srv/mewc/config/example-run-001.json
python3 -m mewc_pipeline export \
  --config /srv/mewc/config/example-run-001.json \
  --destination /srv/mewc/exports/example-run-001
python3 -m mewc_pipeline verify-export --destination /srv/mewc/exports/example-run-001
```

The run directory contains `manifest.json`, verified `inputs/` and `models/` snapshots, stage attempts under `work/`, and `logs/`. Originals and model assets are mounted read-only into isolated stage containers; derived metadata, Camelot copies and renderings go to separate stage outputs. Source paths are rebased under `originals/` without dropping their nested identity. Original detector output remains in the detection attempt alongside the rebased artifact.

After an interruption or timeout, inspect the recorded failure and log, then repeat `run` with the same configuration. Failed attempts remain available; the runner creates new attempts and invalidates downstream outputs when an upstream result fails revalidation. It does not rewrite a failed attempt in place. Do not edit the run manifest or manually relabel an incomplete stage as complete.

The immutable run identity includes effective configuration, source file hashes and modification times, model hashes/declaration, image IDs and runner source hashes. Changing any of these requires a new `run_id` and a fresh `run_dir`. Reusing the old directory with modified inputs, model declarations or code is rejected. Keep the existing run and evidence for comparison.

Exports contain run/verification records, detection/crop/prediction manifests, prediction CSV/pickle/NPZ, canonical metadata, Camelot copies, review images and logs. They do not include the original input snapshot or model files; preserve the retained run separately when those are needed for reproduction. A failed full run cannot produce a certified export. Transfer the completed export using rsync, then run `verify-export --destination ...` on the receiving host from this checkout. That check verifies transport integrity; it is not a new scientific validation. Interrupted exports use an owned `.partial` directory and can be retried for the same run and destination.
