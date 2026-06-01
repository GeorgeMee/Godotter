#!/usr/bin/env bash
set -euo pipefail

PORT="${GODOTTER_WEB_PORT:-9898}"
HOST="${GODOTTER_WEB_HOST:-127.0.0.1}"

cd "$(dirname "$0")/.."

echo "Starting Godotter Web Console on http://${HOST}:${PORT}"
uv run --extra web uvicorn godotter_web.app:app --host "${HOST}" --port "${PORT}"

