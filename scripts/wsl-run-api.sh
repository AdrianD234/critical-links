#!/usr/bin/env bash
# Start (or restart) the FastAPI service inside WSL, detached.
#
#   wsl -d Ubuntu -- bash scripts/wsl-run-api.sh [snapshot_id]
#
# A plain background job dies when the invoking `wsl.exe` command exits, so this
# uses setsid + nohup to detach the process from the WSL session entirely.
set -euo pipefail

VENV="${VENV:-$HOME/.venvs/nzcl}"
PORT="${API_PORT:-8000}"
LOG="${LOG:-/tmp/nzcl-api.log}"
SNAP="${1:-}"

# Ensure PostgreSQL is up (WSL does not always keep it running between sessions).
if ! pg_isready -q -h 127.0.0.1 2>/dev/null; then
  echo "starting PostgreSQL..."
  sudo -n pg_ctlcluster 16 main start 2>/dev/null || \
    pg_ctlcluster 16 main start 2>/dev/null || true
  sleep 3
fi

# Stop any previous instance on this port.
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
  sleep 1
fi

cd "$(dirname "$0")/../python"
export PYTHONPATH="$PWD/src"
[ -n "$SNAP" ] && export SNAPSHOT_ID="$SNAP"

setsid nohup "$VENV/bin/uvicorn" nzcl.api:app \
  --host 0.0.0.0 --port "$PORT" --log-level info \
  > "$LOG" 2>&1 < /dev/null &

# Wait for readiness rather than guessing at a sleep duration.
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo "API ready on port ${PORT}"
    curl -s "http://127.0.0.1:${PORT}/health"
    echo
    exit 0
  fi
  sleep 1
done

echo "API failed to become ready; last log lines:" >&2
tail -20 "$LOG" >&2
exit 1
