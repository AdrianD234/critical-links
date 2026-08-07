#!/usr/bin/env bash
# Repro harness only: start the API on 8010 against a named snapshot, with an
# optional squeezed statement-timeout budget ("none" for the product default).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
SNAP="${1:?snapshot id}"
SQUEEZE="${2:-none}"
pkill -f "ui_fixture.py serve" 2>/dev/null || true
sleep 1
cd "$REPO/python"
export PYTHONPATH="$PWD/src"
export API_PORT=8010 NZCL_REPRO_SNAPSHOT="$SNAP"
setsid nohup ~/.venvs/nzcl/bin/python \
  ../docs/audits/v1-timeout/ui_fixture.py serve "$SQUEEZE" \
  > /tmp/nzcl-v1-timeout-api.log 2>&1 < /dev/null &
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://127.0.0.1:8010/health" >/dev/null 2>&1; then
    echo "API ready: snapshot=$SNAP squeeze=$SQUEEZE"; exit 0
  fi
  sleep 1
done
echo "API failed to start"; tail -30 /tmp/nzcl-v1-timeout-api.log; exit 1
