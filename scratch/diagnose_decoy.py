"""Why was Clintons x Greendale absent from the candidate set?

Diagnosis, not a guess. Read-only against the national snapshot.
"""
from __future__ import annotations

from nzcl import db

SNAP = "amds-national-2026-07-28-5b359d84"
LINK = 234872
CAUSAL = (1525969.0, 5182907.6)   # Clintons x McLaughlins - was found
DECOY = (1526312.0, 5181822.6)    # Clintons x Greendale   - was not

print("=== distance from the CLOSED LINK to each crossing ===")
for label, (x, y) in (("causal", CAUSAL), ("decoy", DECOY)):
    r = db.query_one(
        "SELECT ST_Distance(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193))"
        "       AS d FROM links WHERE snapshot_id=%s AND link_id=%s",
        (x, y, SNAP, LINK))
    print(f"  {label}: {r['d']:.1f} m from link {LINK}")

print("\n=== distance from the CANONICAL ROUTE to each crossing ===")
# The 17 arcs of the canonical replacement, as link ids.
from nzcl import impactv2
imp = impactv2.analyse(SNAP, LINK, scope="segment", metric="distance",
                       profile="car", with_geometry=False,
                       with_corridor=False, with_isolation=False)
route = list(getattr(imp.principal, "link_ids", ()) or ())
print(f"  canonical route has {len(route)} links")
for label, (x, y) in (("causal", CAUSAL), ("decoy", DECOY)):
    r = db.query_one(
        "SELECT MIN(ST_Distance(geom_2193,"
        "       ST_SetSRID(ST_MakePoint(%s,%s),2193))) AS d"
        "  FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)",
        (x, y, SNAP, route))
    print(f"  {label}: {r['d']:.1f} m from the canonical route")

print("\n=== is the decoy INSIDE the convex hull of route+closure? ===")
r = db.query_one(
    "SELECT ST_Contains((SELECT ST_ConvexHull(ST_Collect(geom_2193))"
    "         FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)),"
    "       ST_SetSRID(ST_MakePoint(%s,%s),2193)) AS inside",
    (SNAP, route + [LINK], DECOY[0], DECOY[1]))
print(f"  decoy inside hull: {r['inside']}")
r = db.query_one(
    "SELECT ST_Contains((SELECT ST_ConvexHull(ST_Collect(geom_2193))"
    "         FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)),"
    "       ST_SetSRID(ST_MakePoint(%s,%s),2193)) AS inside",
    (SNAP, route + [LINK], CAUSAL[0], CAUSAL[1]))
print(f"  causal inside hull: {r['inside']}")

print("\n=== what are the NAMES of the links at each crossing? ===")
for label, (x, y) in (("causal", CAUSAL), ("decoy", DECOY)):
    rows = db.query(
        "SELECT l.link_id, l.closure_group_id, n.display_name"
        "  FROM links l LEFT JOIN link_names n"
        "    ON n.snapshot_id = l.snapshot_id"
        "   AND n.closure_group_id = l.closure_group_id"
        " WHERE l.snapshot_id=%s"
        "   AND ST_DWithin(l.geom_2193,"
        "       ST_SetSRID(ST_MakePoint(%s,%s),2193), 2.0)",
        (SNAP, x, y))
    print(f"  {label}:")
    for r in rows:
        print(f"    link {r['link_id']} group {r['closure_group_id'][:12]}... "
              f"name={r['display_name']!r}")

print("\n=== does link_names have rows at all? ===")
print(db.query_one("SELECT count(*) AS n FROM link_names WHERE snapshot_id=%s",
                   (SNAP,)))
print(db.query("SELECT display_name FROM link_names WHERE snapshot_id=%s "
               " AND display_name IS NOT NULL LIMIT 3", (SNAP,)))
