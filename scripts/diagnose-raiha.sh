#!/usr/bin/env bash
# Investigate the cross-validation disagreements found on the Wellington pilot.
set -euo pipefail
cd "$(dirname "$0")/../python"
VENV="${VENV:-$HOME/.venvs/nzcl}"
SNAP="${1:-amds-wellington-2026-07-27-6ef785ad}"
TS_URL="${2:-http://127.0.0.1:8787}"

AMDS="$("$VENV/bin/python" - "$SNAP" <<'PY'
import sys
from nzcl import db
row = db.query_one(
    "SELECT amds_id FROM links WHERE snapshot_id=%s AND road_name=%s "
    "AND in_analysis_area ORDER BY length_m DESC LIMIT 1",
    (sys.argv[1], "Raiha Street"))
print(row["amds_id"] if row else "")
PY
)"

if [ -z "$AMDS" ]; then
  echo "Raiha Street not found in $SNAP" >&2
  exit 1
fi

echo "diagnosing $AMDS"
"$VENV/bin/python" -m nzcl.diagnose "$SNAP" "$AMDS" "$TS_URL"
