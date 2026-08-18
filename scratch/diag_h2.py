"""Diagnose the three contradicted AT_GRADE cards on the EXACT rows classified."""
from __future__ import annotations
from shapely import wkb
from shapely.geometry import Point
from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"
CASES = [("H001", 83156, 83590), ("H040", 278923, 279112), ("H192", 82774, 327541)]

geoms: dict[int, object] = {}
def geom(lid):
    if lid not in geoms:
        r = db.query_one("SELECT ST_AsBinary(geom_2193) AS g FROM links "
                         "WHERE snapshot_id=%s AND link_id=%s", (SNAP, lid))
        geoms[lid] = wkb.loads(bytes(r["g"]))
    return geoms[lid]

for code, la, lb in CASES:
    rows = db.query("SELECT * FROM scratch_features WHERE link_a=%s AND link_b=%s",
                    (la, lb))
    print(f"=== {code}: {la} x {lb}  ({len(rows)} row(s)) ===")
    for r in rows:
        ga, gb = geom(la), geom(lb)
        p = Point(float(r["px"]), float(r["py"]))
        aa, ab = ga.project(p), gb.project(p)
        print(f"  names: {r['name_a']!r} / {r['name_b']!r}   "
              f"groups {r['group_a']} / {r['group_b']}")
        print(f"  stored angle_deg={float(r['angle_deg']):.2f} raw={float(r['raw_angle']):.2f}"
              f"  recomputed={crossings.crossing_angle_deg(ga, aa, gb, ab):.2f}")
        print(f"  n_intersections={r['n_intersections']} nodes_within_1m={r['nodes_within_1m']}"
              f"  len_a={float(r['len_a']):.1f} len_b={float(r['len_b']):.1f}")
        print(f"  along_a={aa:.1f}/{ga.length:.1f}  along_b={ab:.1f}/{gb.length:.1f}")
        print(f"  dup={crossings.is_duplicate_corridor(ga, aa, gb, ab)}")
        # Hausdorff-ish: how far apart are the two whole links?
        print(f"  A within 8 m of B for {sum(1 for i in range(0,101) if gb.distance(ga.interpolate(ga.length*i/100))<=8.0)}% of A;"
              f"  B within 8 m of A for {sum(1 for i in range(0,101) if ga.distance(gb.interpolate(gb.length*i/100))<=8.0)}% of B")
        # shortest available run either side
        for tag, line, along in (("A", ga, aa), ("B", gb, ab)):
            print(f"    {tag}: {along:.1f} m before, {line.length-along:.1f} m after")
    print()
