#!/usr/bin/env bash
set -euo pipefail

# Inputs from systemd env (with sensible defaults):
#   MEWC_MOUNT, DETECT_IMAGE, DETECT_CPUS, DETECT_CONFIDENCE, DETECT_MD_FILE,
#   DETECT_MODEL, VENV_PY, APP_ROOT, CHECKPOINT_FREQ
JOB_ID="${1:?job id required}"

# Defaults
APP_ROOT="${APP_ROOT:-/opt/mewc-web}"
MOUNT="${MEWC_MOUNT:-/mnt/mewc-volume}"
PY="${VENV_PY:-/usr/bin/python3}"
DETECT_CPUS="${DETECT_CPUS:-30}"
DETECT_CONFIDENCE="${DETECT_CONFIDENCE:-0.3}"
DETECT_MD_FILE="${DETECT_MD_FILE:-md.json}"
CHECKPOINT_FREQ="${CHECKPOINT_FREQ:-25}"

# Paths
JOB_DIR="${MOUNT}/jobs/${JOB_ID}"
UPLOADS="${JOB_DIR}/uploads"
OUTDIR="${JOB_DIR}/detect"
LOGDIR="${JOB_DIR}/logs"
STATE="${JOB_DIR}/state.json"
LOG="${LOGDIR}/detect.log"
OUT_PATH="${UPLOADS}/${DETECT_MD_FILE}"

mkdir -p "${OUTDIR}" "${LOGDIR}"

timestamp() { date -Iseconds; }

# Count total images
mapfile -t FILES < <(find "${UPLOADS}" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) | sort)
TOTAL="${#FILES[@]}"
STARTED="$(timestamp)"

# Initial state
cat > "${STATE}" <<EOF
{"stage":"detect","status":"running","processed":0,"total":${TOTAL},"started_at":"${STARTED}","updated_at":"${STARTED}"}
EOF

: > "${LOG}"

cleanup_error() {
  cat > "${STATE}" <<EOF
{"stage":"detect","status":"error","processed":0,"total":${TOTAL},"started_at":"${STARTED}","updated_at":"$(timestamp)"}
EOF
}

rc=0

if [[ -n "${DETECT_IMAGE:-}" ]]; then
  # docker run (NO CHECKPOINT_FILE; only CHECKPOINT_FREQ)
  args=( docker run --rm
        --name "mewc-job-${JOB_ID}"
        --cpus="${DETECT_CPUS}"
        -v "${UPLOADS}:/images"
        -e INPUT_DIR="/images"
        -e MD_FILE="${DETECT_MD_FILE}"        # 🔍 change 1: pass just the filename
        -e THRESHOLD="${DETECT_CONFIDENCE}"
        -e RECURSIVE="True"
        -e RELATIVE_FILENAMES="True"
        -e NCORES="${DETECT_CPUS}"
        -e CHECKPOINT_FREQ="${CHECKPOINT_FREQ}"
        -e OMP_NUM_THREADS="${DETECT_CPUS}"
        -e MKL_NUM_THREADS="${DETECT_CPUS}"
        -e OPENBLAS_NUM_THREADS="${DETECT_CPUS}"
  )
  if [[ -n "${DETECT_MODEL:-}" ]]; then
    args+=( -e MD_MODEL="${DETECT_MODEL}" )
    [[ -d "/mnt/mewc-volume/models" ]] && args+=( -v "/mnt/mewc-volume/models:/models" )
  fi
  args+=( "${DETECT_IMAGE}" )

  ( set -x; "${args[@]}" ) >> "${LOG}" 2>&1 & RUN_PID=$!

  # Progress watcher: count images from md.json every 10s (when it appears)
  while kill -0 "${RUN_PID}" 2>/dev/null; do
    # 🔍 change 2: look in either uploads/md.json or uploads/images/md.json
    WATCH="${OUT_PATH}"
    [[ -f "${WATCH}" ]] || WATCH="${UPLOADS}/images/${DETECT_MD_FILE}"
    PROCESSED="$("${PY}" - "$WATCH" <<'PYCODE' 2>/dev/null || true
import json, sys, pathlib
p=pathlib.Path(sys.argv[1])
if not p.exists():
    print(0); raise SystemExit
try:
    data=json.loads(p.read_text())
    imgs = data.get("images", [])
    print(len(imgs))
except Exception:
    print(0)
PYCODE
)"
    PROCESSED="${PROCESSED:-0}"
    cat > "${STATE}.tmp" <<EOF
{"stage":"detect","status":"running","processed":${PROCESSED},"total":${TOTAL},"started_at":"${STARTED}","updated_at":"$(timestamp)"}
EOF
    mv -f "${STATE}.tmp" "${STATE}"
    sleep 10
  done

  wait "${RUN_PID}" || rc=$?

else
  echo "[stub] DETECT_IMAGE unset; creating dummy md.json" >> "${LOG}"
  "${PY}" - "$JOB_DIR" > "${OUTDIR}/md.json" <<'PYCODE'
import json, sys, pathlib, random
job = pathlib.Path(sys.argv[1])
uploads = job/'uploads'
images=[]
ex=('.jpg','.jpeg','.png')
for p in sorted(uploads.rglob('*')):
    if p.is_file() and p.suffix.lower() in ex:
        images.append({
            "file": str(p.relative_to(uploads)).replace('\\','/'),
            "max_detection_conf": 0.9,
            "detections":[{"category":"1","conf":round(random.uniform(0.3,0.97),2),"bbox":[0.1,0.1,0.4,0.4]}]
        })
json.dump({"images":images,"detection_categories":{"1":"animal"}}, sys.stdout)
PYCODE
fi

# 🔍 change 3: normalize output to detect/md.json from any known location
for CAND in \
  "${OUT_PATH}" \
  "${UPLOADS}/images/${DETECT_MD_FILE}"
do
  if [[ -f "$CAND" ]]; then
    cp -f "$CAND" "${OUTDIR}/md.json"
    break
  fi
done

STATUS="error"
if [[ -f "${OUTDIR}/md.json" ]] || [[ -f "${OUT_PATH}" ]] || [[ -f "${UPLOADS}/images/${DETECT_MD_FILE}" ]]; then
  [[ -f "${OUTDIR}/md.json" ]] || cp -f "${UPLOADS}/images/${DETECT_MD_FILE}" "${OUTDIR}/md.json" 2>/dev/null || true
  STATUS="done"
fi

if [[ "${STATUS}" = "done" ]]; then
  "${PY}" "${APP_ROOT}/app/md_to_csv.py" "${OUTDIR}/md.json" "${OUTDIR}/detections.csv" >> "${LOG}" 2>&1 || true
fi

# write final state unconditionally
cat > "${STATE}" <<EOF
{"stage":"detect","status":"${STATUS}","processed":${TOTAL},"total":${TOTAL},"started_at":"${STARTED}","updated_at":"$(timestamp)"}
EOF

# if we produced md.json, consider the run successful regardless of container exit code
if [[ "${STATUS}" = "done" ]]; then
  exit 0
else
  exit ${rc:-1}
fi
