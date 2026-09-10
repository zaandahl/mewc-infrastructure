#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 3 || "$2" != "--config" ]]; then
  echo "Legacy implicit-state operations are disabled. Usage: $0 {preflight|plan|apply|configure|destroy-plan|destroy} --config /absolute/deployment.json" >&2
  exit 2
fi
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_DIR"
exec python3 -m mewc_infra "$@"
