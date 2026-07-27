#!/usr/bin/env bash
# Block until the batch has an outcome recorded for every eligible link.
#
#   wait-batch.sh <snapshot_id> [poll_seconds]
#
# Exits non-zero if the batch process disappears before finishing, so a killed
# run is distinguishable from a completed one.
set -euo pipefail
export PGPASSWORD="${NZCL_DB_PASSWORD:-nzcl_local_dev}"

SNAP="$1"
POLL="${2:-120}"

q() { psql -h 127.0.0.1 -U nzcl -d nzcl -tAqc "$1" | tr -d ' '; }

ELIGIBLE="$(q "SELECT count(*) FROM links WHERE snapshot_id='${SNAP}' AND in_analysis_area AND length_m > 0")"
echo "waiting for ${ELIGIBLE} eligible links"

STALL=0
LAST=0
while :; do
  DONE="$(q "SELECT count(DISTINCT link_id) FROM detour_results WHERE snapshot_id='${SNAP}'")"
  if [ "$DONE" -ge "$ELIGIBLE" ]; then
    echo "complete: ${DONE}/${ELIGIBLE}"
    exit 0
  fi

  if ! pgrep -f "nzcl.batch" > /dev/null 2>&1; then
    echo "batch process is gone at ${DONE}/${ELIGIBLE}" >&2
    exit 1
  fi

  if [ "$DONE" -eq "$LAST" ]; then
    STALL=$((STALL + 1))
    # 20 consecutive quiet polls with the process alive: report rather than hang.
    if [ "$STALL" -ge 20 ]; then
      echo "stalled at ${DONE}/${ELIGIBLE}" >&2
      exit 2
    fi
  else
    STALL=0
  fi
  LAST="$DONE"
  sleep "$POLL"
done
