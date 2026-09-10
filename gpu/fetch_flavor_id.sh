#!/usr/bin/env bash
set -euo pipefail
echo "Automatic reservation selection is disabled. Set exact lease_id, reservation_id and flavor_id in deployment JSON, then run python3 -m mewc_infra preflight --config /absolute/deployment.json." >&2
exit 2
