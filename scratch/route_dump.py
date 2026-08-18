"""Dump the links behind a list of arc ids, in order."""
from __future__ import annotations

import sys

from nzcl import db

SNAP = "amds-national-2026-07-28-5b359d84"


def dump(label: str, arc_ids: list[int]) -> None:
    print(f"== {label} ({len(arc_ids)} arcs) ==")
    if not arc_ids:
        print("  (none)")
        return
    rows = db.query(
        "SELECT a.arc_id, a.link_id, a.source, a.target, a.direction, "
        "       a.cost_distance_m, l.amds_id, l.closure_group_id, "
        "       coalesce(dn.display_name, l.road_name) AS name "
        "  FROM arcs a JOIN links l ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id "
        "  LEFT JOIN link_display_names dn ON dn.snapshot_id=a.snapshot_id AND dn.link_id=a.link_id "
        " WHERE a.snapshot_id=%s AND a.arc_id = ANY(%s)",
        (SNAP, arc_ids),
    )
    by_arc = {r["arc_id"]: r for r in rows}
    total = 0.0
    for aid in arc_ids:
        r = by_arc.get(aid)
        if r is None:
            print(f"  arc {aid}: MISSING")
            continue
        total += r["cost_distance_m"]
        print(f"  arc {aid:>7} link {r['link_id']:>7} {r['source']:>7}->{r['target']:<7} "
              f"{r['cost_distance_m']:>9.1f} m  {r['name'] or '(unnamed)'}")
    print(f"  TOTAL {total:.1f} m")
    print()


if __name__ == "__main__":
    import json
    with open(sys.argv[1], encoding="utf-8") as fh:
        d = json.load(fh)
    dump("intact (pre-closure) route", d["principal_movement"]["intact_arc_ids"])
    dump("replacement route", d["principal"]["arc_ids"])
