#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-9898}"

if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN || true)"
elif command -v ss >/dev/null 2>&1; then
  PIDS="$(ss -ltnp "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\\([0-9]\\+\\).*/\\1/p' | sort -u || true)"
else
  echo "No lsof/ss found; cannot auto-detect listener PID."
  exit 1
fi

if [ -z "${PIDS}" ]; then
  echo "No listeners found on port ${PORT}"
  exit 0
fi

echo "Stopping listeners on port ${PORT}: ${PIDS}"
for pid in ${PIDS}; do
  kill -9 "${pid}" || true
done

