#!/usr/bin/env bash
# Repro harness only: start the API against the reproduction snapshot on 8010.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO/python"
export PYTHONPATH="$PWD/src"
export API_PORT=8010
SQUEEZE="${1:-5}"
setsid nohup ~/.venvs/nzcl/bin/python \
  ../docs/audits/v1-timeout/ui_fixture.py serve "$SQUEEZE" \
  > /tmp/nzcl-v1-timeout-api.log 2>&1 < /dev/null &
for _ in $(seq 1 40); do
  if curl -sf -m 2 "http://127.0.0.1:8010/health" >/dev/null 2>&1; then
    echo "API ready (squeeze=$SQUEEZE)"; exit 0
  fi
  sleep 1
done
echo "API failed to start"; tail -30 /tmp/nzcl-v1-timeout-api.log; exit 1
