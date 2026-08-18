"""Re-run the SHIPPED detector+classifier on the three cards, at SOURCE-FEATURE level.

topology.split_at_junctions() classifies crossings between AMDS SOURCE FEATURES,
not between graph links. The national audit record (scratch_features) was built
on the links table, i.e. AFTER splitting. The two are not the same geometry, so
what the audit says a card is and what the ingest would actually do can differ.
"""
from __future__ import annotations
from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.ops import linemerge
from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"
CASES = [("H001", 83156, 83590), ("H040", 278923, 279112), ("H192", 82774, 327541)]


def feature(link_id):
    r = db.query_one("SELECT closure_group_id FROM links WHERE snapshot_id=%s AND link_id=%s",
                     (SNAP, link_id))
    grp = r["closure_group_id"]
    rows = db.query("SELECT link_id, amds_id, ST_AsBinary(geom_2193) g FROM links "
                    "WHERE snapshot_id=%s AND closure_group_id=%s ORDER BY amds_id",
                    (SNAP, grp))
    geoms = [wkb.loads(bytes(r["g"])) for r in rows]
    merged = linemerge(geoms) if len(geoms) > 1 else geoms[0]
    return grp, rows, merged


for code, la, lb in CASES:
    print(f"=== {code}: link {la} x {lb} ===")
    ga_grp, ga_rows, ga = feature(la)
    gb_grp, gb_rows, gb = feature(lb)
    for tag, grp, rows, m in (("A", ga_grp, ga_rows, ga), ("B", gb_grp, gb_rows, gb)):
        print(f"  {tag} feature {grp}: {len(rows)} link piece(s), merged type "
              f"{m.geom_type}, length "
              f"{m.length if m.geom_type=='LineString' else sum(x.length for x in m.geoms):.1f} m")
    if ga.geom_type != "LineString" or gb.geom_type != "LineString":
        print("  (not a single mergeable line - reporting per-piece instead)")
    lines_a = [ga] if ga.geom_type == "LineString" else list(ga.geoms)
    lines_b = [gb] if gb.geom_type == "LineString" else list(gb.geoms)
    for ia, A in enumerate(lines_a):
        for ib, B in enumerate(lines_b):
            found = crossings.detect([A, B], ["A", "B"], end_guard_m=0.05)
            for f in found:
                dup = crossings.is_duplicate_corridor(A, f.along_a, B, f.along_b)
                print(f"  crossing at ({f.x:.1f},{f.y:.1f}) angle={f.angle_deg:.2f} "
                      f"along_a={f.along_a:.1f}/{A.length:.1f} "
                      f"along_b={f.along_b:.1f}/{B.length:.1f} dup={dup}")
            if not found:
                print(f"  piece {ia}x{ib}: NO interior crossing detected")
    print()
