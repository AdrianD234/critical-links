"""Diagnose the three contradicted AT_GRADE holdout cards, from the source data."""
from __future__ import annotations
import sys
from shapely import wkb
from shapely.geometry import Point
from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"
CASES = [("H001", 83156, 83590), ("H040", 278923, 279112), ("H192", 82774, 327541)]


def link(lid):
    return db.query_one(
        "SELECT link_id, closure_group_id, road_name, oneway, model_asset_type,"
        "       rca_code, source_node, target_node, length_m,"
        "       ST_AsBinary(geom_2193) AS g"
        "  FROM links WHERE snapshot_id=%s AND link_id=%s", (SNAP, lid))


for code, la, lb in CASES:
    a, b = link(la), link(lb)
    ga, gb = wkb.loads(bytes(a["g"])), wkb.loads(bytes(b["g"]))
    inter = ga.intersection(gb)
    p = inter if inter.geom_type == "Point" else list(inter.geoms)[0]
    aa, ab = ga.project(p), gb.project(p)
    print(f"=== {code}: {la} x {lb} ===")
    for tag, r, g, al in (("A", a, ga, aa), ("B", b, gb, ab)):
        print(f"  {tag} link={r['link_id']} group={r['closure_group_id']} "
              f"name={r['road_name']!r} len={g.length:.1f} along={al:.1f} "
              f"oneway={r['oneway']} mat={r['model_asset_type']} "
              f"nodes=({r['source_node']},{r['target_node']})")
    print(f"  angle={crossings.crossing_angle_deg(ga, aa, gb, ab):.2f} "
          f"dup={crossings.is_duplicate_corridor(ga, aa, gb, ab)}")
    # why not dup: report per-direction sampling
    for tag, line, along, other in (("A", ga, aa, gb), ("B", gb, ab, ga)):
        for sign in (-1.0, 1.0):
            far = along + sign * crossings.DUPLICATE_RUN_M
            if far < 0.0 or far > line.length:
                print(f"    {tag}{'+' if sign>0 else '-'}: SKIPPED "
                      f"(needs {far:.1f} of {line.length:.1f})")
                continue
            ds = [other.distance(line.interpolate(along + sign * crossings.DUPLICATE_RUN_M * f))
                  for f in (0.25, 0.5, 0.75, 1.0)]
            print(f"    {tag}{'+' if sign>0 else '-'}: "
                  + " ".join(f"{d:6.2f}" for d in ds))
    # how far do they actually stay close, on the shorter available run?
    for tag, line, along, other in (("A", ga, aa, gb), ("B", gb, ab, ga)):
        for sign in (-1.0, 1.0):
            reach = 0.0
            step = 2.0
            while True:
                t = along + sign * (reach + step)
                if t < 0.0 or t > line.length:
                    break
                if other.distance(line.interpolate(t)) > crossings.DUPLICATE_CORRIDOR_M:
                    break
                reach += step
            lim = along if sign < 0 else line.length - along
            print(f"    stay-within-8m {tag}{'+' if sign>0 else '-'}: "
                  f"{reach:.0f} m (available {lim:.0f} m)")
    # do the two source features touch anywhere else?
    print(f"  same closure group: {a['closure_group_id'] == b['closure_group_id']}")
    shared = {a["source_node"], a["target_node"]} & {b["source_node"], b["target_node"]}
    print(f"  shared graph nodes: {sorted(shared)}")
    print()
