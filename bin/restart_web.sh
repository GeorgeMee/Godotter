#!/usr/bin/env bash
set -euo pipefail

PORT="${GODOTTER_WEB_PORT:-9898}"
HOST="${GODOTTER_WEB_HOST:-127.0.0.1}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"${SCRIPT_DIR}/stop_web.sh" "${PORT}"
sleep 0.2
exec "${SCRIPT_DIR}/run_web.sh"

