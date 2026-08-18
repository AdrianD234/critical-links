"""Is the 327539/327541/327542 chain a second recording of feature 7d966e5b?"""
from __future__ import annotations
from shapely import wkb
from shapely.geometry import LineString
from shapely.ops import linemerge
from nzcl import db

SNAP = "amds-national-2026-07-28-5b359d84"

def feat(grp):
    rows = db.query("SELECT ST_AsBinary(geom_2193) g FROM links "
                    "WHERE snapshot_id=%s AND closure_group_id=%s ORDER BY amds_id",
                    (SNAP, grp))
    gs = [wkb.loads(bytes(r["g"])) for r in rows]
    m = linemerge(gs) if len(gs) > 1 else gs[0]
    return m

A = feat("{7d966e5b-051a-48aa-8742-ddb253b4bdc8}")
chain_ids = ["{6b199afb-8106-4ed1-8e12-126cec5dd138}",
             "{61c2fcad-60fe-4a9a-bf5b-6897a6345b1d}",
             "{ccf7196f-675b-42f5-9fbe-443f62a63b70}"]
parts = [feat(g) for g in chain_ids]
coords = []
for p in parts:
    c = list(p.coords)
    if coords and ((coords[-1][0]-c[0][0])**2 + (coords[-1][1]-c[0][1])**2) > \
                  ((coords[-1][0]-c[-1][0])**2 + (coords[-1][1]-c[-1][1])**2):
        c = list(reversed(c))
    coords.extend(c if not coords else c[1:])
CH = LineString(coords)
print(f"A length {A.length:.1f} m; chain length {CH.length:.1f} m "
      f"({[round(p.length,1) for p in parts]})")
print("separation profile along the chain (every 100 m):")
for d in range(0, int(CH.length) + 1, 100):
    p = CH.interpolate(min(d, CH.length))
    print(f"  {d:5d} m: {A.distance(p):7.2f} m from A")
inside = sum(1 for i in range(0, 1001)
             if A.distance(CH.interpolate(CH.length*i/1000)) <= 8.0)
print(f"chain within 8 m of A for {inside/10.0:.1f}% of its length")
inside25 = sum(1 for i in range(0, 1001)
               if A.distance(CH.interpolate(CH.length*i/1000)) <= 25.0)
print(f"chain within 25 m of A for {inside25/10.0:.1f}% of its length")
# how many times do they cross?
inter = A.intersection(CH)
print(f"intersection: {inter.geom_type} "
      f"{len(inter.geoms) if hasattr(inter,'geoms') else 1} point(s)")
