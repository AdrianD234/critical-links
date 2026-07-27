#!/usr/bin/env bash
# Compare every link of one road across both engines, to pin down a
# cross-validation disagreement to a specific link.
#
#   compare-road.sh <snapshot> <road name> [ts_url]
set -euo pipefail
cd "$(dirname "$0")/../python"
VENV="${VENV:-$HOME/.venvs/nzcl}"

"$VENV/bin/python" - "$1" "$2" "${3:-http://127.0.0.1:8787}" <<'PY'
import json, sys, urllib.parse, urllib.request
from nzcl import db
from nzcl.detour import compute

snap, road, ts_url = sys.argv[1], sys.argv[2], sys.argv[3]

rows = db.query(
    "SELECT link_id, amds_id, length_m FROM links "
    "WHERE snapshot_id=%s AND road_name=%s AND in_analysis_area "
    "AND source_node <> target_node ORDER BY link_id",
    (snap, road))
print(f"{road}: {len(rows)} links in the pgRouting graph\n")

for r in rows:
    ref = urllib.parse.quote(r["amds_id"], safe="")
    try:
        with urllib.request.urlopen(
            f"{ts_url}/api/v1/links/{ref}/detour?geometry=false&direction=forward",
            timeout=120) as fh:
            ts = json.load(fh)
    except Exception as exc:
        print(f"  {r['amds_id']}  ts error: {exc}")
        continue

    pg = compute(snap, r["link_id"], compute_corridor=False).forward
    tf = ts.get("forward")
    if pg is None or tf is None:
        continue

    pg_alt = pg.alternative_distance_m
    ts_alt = tf["metrics"]["alternativeDistanceM"]
    ts_len = ts["selectedLink"]["lengthM"]
    same_len = abs(ts_len - r["length_m"]) <= 1.0
    delta = (abs(pg_alt - ts_alt) if pg_alt is not None and ts_alt is not None
             else None)
    mark = ""
    if pg.status != tf["status"]:
        mark = "  <-- STATUS DIFFERS"
    elif delta is not None and delta > 1.0:
        mark = f"  <-- DELTA {delta:.1f} m"

    print(f"  {r['amds_id']}")
    print(f"    len pg={r['length_m']:.1f} ts={ts_len:.1f} "
          f"{'(same)' if same_len else '(DIFFERENT SPLIT)'}")
    print(f"    pg {pg.status:13} alt={pg_alt}")
    print(f"    ts {tf['status']:13} alt={ts_alt}{mark}")
PY
