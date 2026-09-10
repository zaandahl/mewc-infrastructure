#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 KEYPAIR_NAME /absolute/private-key-path (OpenStack credentials from environment)" >&2
  exit 2
fi
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$REPO_DIR"
exec python3 -m mewc_infra.keys "$@"
