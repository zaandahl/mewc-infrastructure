#!/usr/bin/env bash
# The fixed worker service is the only execution interface; HTTP never invokes sudo.
set -euo pipefail
exec "${VENV_PY:-/opt/mewc-web/venv/bin/python}" -m app.worker
