#!/usr/bin/env bash
# Report batch progress and a measured rate, without guessing.
#
#   batch-progress.sh [snapshot_id] [sample_seconds]
set -euo pipefail
export PGPASSWORD="${NZCL_DB_PASSWORD:-nzcl_local_dev}"

SNAP="${1:-}"
WINDOW="${2:-30}"

q() { psql -h 127.0.0.1 -U nzcl -d nzcl -tAqc "$1" | tr -d ' '; }

if [ -z "$SNAP" ]; then
  SNAP="$(q "SELECT snapshot_id FROM network_snapshots WHERE snapshot_id NOT LIKE 'test-%' ORDER BY retrieved_at_utc DESC LIMIT 1")"
fi

ELIGIBLE="$(q "SELECT count(*) FROM links WHERE snapshot_id='${SNAP}' AND in_analysis_area AND length_m > 0")"
A="$(q "SELECT count(DISTINCT link_id) FROM detour_results WHERE snapshot_id='${SNAP}'")"
sleep "$WINDOW"
B="$(q "SELECT count(DISTINCT link_id) FROM detour_results WHERE snapshot_id='${SNAP}'")"

RATE=$(awk -v a="$A" -v b="$B" -v w="$WINDOW" 'BEGIN{printf "%.2f", (b-a)/w}')
REMAIN=$((ELIGIBLE - B))

echo "snapshot:  ${SNAP}"
echo "eligible:  ${ELIGIBLE}"
echo "done:      ${B}  ($(awk -v b="$B" -v e="$ELIGIBLE" 'BEGIN{printf "%.1f", b*100/e}')%)"
echo "rate:      ${RATE} links/s (measured over ${WINDOW}s)"
if [ "$(awk -v r="$RATE" 'BEGIN{print (r>0)}')" = "1" ]; then
  echo "eta:       $(awk -v r="$REMAIN" -v s="$RATE" 'BEGIN{printf "%.0f", r/s/60}') min"
else
  echo "eta:       stalled - no rows added in ${WINDOW}s"
fi
