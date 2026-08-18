"""Raw geometry and attributes for the three cards and their neighbours."""
from __future__ import annotations
from shapely import wkb
from shapely.geometry import Point
from nzcl import db

SNAP = "amds-national-2026-07-28-5b359d84"
IDS = {"H001": [83156, 83590, 83031, 83032],
       "H040": [278923, 279112],
       "H192": [82774, 327541, 327542, 327539]}

for code, ids in IDS.items():
    print(f"=== {code} ===")
    for lid in ids:
        r = db.query_one(
            "SELECT link_id, amds_id, closure_group_id, source_object_id, road_name,"
            "       road_number, rca_code, rca_name, model_asset_type, surface_type,"
            "       status, oneway, source_node, target_node, length_m, urban_rural,"
            "       quality_flags, ST_AsBinary(geom_2193) g"
            "  FROM links WHERE snapshot_id=%s AND link_id=%s", (SNAP, lid))
        g = wkb.loads(bytes(r["g"]))
        print(f"  link {lid}: amds={r['amds_id']} obj={r['source_object_id']} "
              f"name={r['road_name']!r} num={r['road_number']!r}")
        print(f"    rca={r['rca_code']}/{r['rca_name']} mat={r['model_asset_type']} "
              f"surf={r['surface_type']} status={r['status']} ur={r['urban_rural']} "
              f"flags={r['quality_flags']}")
        print(f"    nodes=({r['source_node']},{r['target_node']}) len={g.length:.1f} "
              f"verts={len(g.coords)}")
        print(f"    coords={[(round(a,1),round(b,1)) for a,b in g.coords][:8]}"
              + (" ..." if len(g.coords) > 8 else ""))
    print()
