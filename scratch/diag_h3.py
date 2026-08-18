"""Local network context for the three contradicted cards."""
from __future__ import annotations
from shapely import wkb
from shapely.geometry import Point
from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"
CASES = [("H001", 83156, 83590), ("H040", 278923, 279112), ("H192", 82774, 327541)]

for code, la, lb in CASES:
    r = db.query_one("SELECT * FROM scratch_features WHERE link_a=%s AND link_b=%s",
                     (la, lb))
    x, y = float(r["px"]), float(r["py"])
    ll = db.query_one("SELECT ST_X(p) lon, ST_Y(p) lat FROM ("
                      "SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),2193),4326) p) t",
                      (x, y))
    print(f"=== {code}  ({x:.1f},{y:.1f})  lon/lat {ll['lon']:.6f},{ll['lat']:.6f} ===")
    near = db.query(
        "SELECT link_id, closure_group_id, road_name, surface_type, urban_rural,"
        "       model_asset_type, length_m, rca_name,"
        "       ST_Distance(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193)) d"
        "  FROM links WHERE snapshot_id=%s"
        "   AND ST_DWithin(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), 40)"
        " ORDER BY d", (x, y, SNAP, x, y))
    for n in near:
        mark = " <-A" if n["link_id"] == la else (" <-B" if n["link_id"] == lb else "")
        print(f"  d={float(n['d']):6.2f} link={n['link_id']:<7} len={float(n['length_m']):7.1f} "
              f"name={str(n['road_name'])[:28]:<28} surf={n['surface_type']} ur={n['urban_rural']} "
              f"mat={n['model_asset_type']} rca={str(n['rca_name'])[:22]}{mark}")
    print()
