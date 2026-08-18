"""Is the AMDS z-value structure-aware, or a terrain drape?

If it is a drape off a digital elevation model, an overbridge and the road
beneath it share one surface and dz at the crossing is ~0 - in which case z
cannot classify anything. If it is structure-aware, a motorway overbridge
should show several metres.

This asks that question on a handful of crossings before any effort is spent
downloading z nationally.
"""
from __future__ import annotations

import json

import requests

from nzcl import db

SNAP = "amds-national-2026-07-28-5b359d84"
BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer/1")


def fetch_z(object_ids: list[int]) -> dict[int, list[list[float]]]:
    out: dict[int, list[list[float]]] = {}
    for i in range(0, len(object_ids), 200):
        chunk = object_ids[i:i + 200]
        r = requests.get(f"{BASE}/query", timeout=120, params={
            "f": "json", "objectIds": ",".join(str(o) for o in chunk),
            "outFields": "OBJECTID", "returnGeometry": "true",
            "returnZ": "true", "outSR": 2193})
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise SystemExit(j["error"])
        for feat in j.get("features", []):
            oid = feat["attributes"]["OBJECTID"]
            paths = (feat.get("geometry") or {}).get("paths") or []
            if paths:
                out[oid] = paths[0]
    return out


def z_at(path: list[list[float]], x: float, y: float) -> float | None:
    """Z of the polyline at the point on it nearest (x, y)."""
    best = None
    for (x1, y1, z1, *_), (x2, y2, z2, *_) in zip(path, path[1:]):
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        px, py = x1 + t * dx, y1 + t * dy
        d2 = (px - x) ** 2 + (py - y) ** 2
        z = z1 + t * (z2 - z1)
        if best is None or d2 < best[0]:
            best = (d2, z)
    return None if best is None else best[1]


CASES = {
    "known grade separation (motorway crossed by a local road)": """
        SELECT a.link_id la, b.link_id lb, a.source_object_id oa, b.source_object_id ob,
               ST_X(x.p) px, ST_Y(x.p) py,
               na.display_name nam_a, nb.display_name nam_b
          FROM scratch_crossings c
          JOIN links a ON a.snapshot_id=%s AND a.link_id=c.link_a
          JOIN links b ON b.snapshot_id=%s AND b.link_id=c.link_b
          CROSS JOIN LATERAL (SELECT ST_PointOnSurface(c.igeom) p) x
          LEFT JOIN link_display_names na ON na.snapshot_id=%s AND na.link_id=a.link_id
          LEFT JOIN link_display_names nb ON nb.snapshot_id=%s AND nb.link_id=b.link_id
         WHERE c.itype='ST_Point'
           AND ((a.rca_code=1 AND a.oneway=1) OR (b.rca_code=1 AND b.oneway=1))
           AND ST_Y(ST_Transform(x.p,4326)) > -37.2
         ORDER BY a.link_id LIMIT 8
    """,
    "rural crossroads (neither is a state highway)": """
        SELECT a.link_id la, b.link_id lb, a.source_object_id oa, b.source_object_id ob,
               ST_X(x.p) px, ST_Y(x.p) py,
               na.display_name nam_a, nb.display_name nam_b
          FROM scratch_crossings c
          JOIN links a ON a.snapshot_id=%s AND a.link_id=c.link_a
          JOIN links b ON b.snapshot_id=%s AND b.link_id=c.link_b
          CROSS JOIN LATERAL (SELECT ST_PointOnSurface(c.igeom) p) x
          LEFT JOIN link_display_names na ON na.snapshot_id=%s AND na.link_id=a.link_id
          LEFT JOIN link_display_names nb ON nb.snapshot_id=%s AND nb.link_id=b.link_id
         WHERE c.itype='ST_Point'
           AND a.rca_code<>1 AND b.rca_code<>1
           AND a.urban_rural='rural' AND b.urban_rural='rural'
           AND na.display_name IS NOT NULL AND nb.display_name IS NOT NULL
         ORDER BY a.link_id LIMIT 8
    """,
    "the Greendale case": """
        SELECT a.link_id la, b.link_id lb, a.source_object_id oa, b.source_object_id ob,
               ST_X(x.p) px, ST_Y(x.p) py,
               na.display_name nam_a, nb.display_name nam_b
          FROM scratch_crossings c
          JOIN links a ON a.snapshot_id=%s AND a.link_id=c.link_a
          JOIN links b ON b.snapshot_id=%s AND b.link_id=c.link_b
          CROSS JOIN LATERAL (SELECT ST_PointOnSurface(c.igeom) p) x
          LEFT JOIN link_display_names na ON na.snapshot_id=%s AND na.link_id=a.link_id
          LEFT JOIN link_display_names nb ON nb.snapshot_id=%s AND nb.link_id=b.link_id
         WHERE (c.link_a, c.link_b) IN ((232709, 234053), (232708, 234875))
    """,
}


def main() -> int:
    all_rows = {}
    oids = set()
    for label, sql in CASES.items():
        rows = db.query(sql, (SNAP, SNAP, SNAP, SNAP))
        all_rows[label] = rows
        for r in rows:
            oids.add(r["oa"])
            oids.add(r["ob"])
    print(f"fetching z for {len(oids)} source features")
    z = fetch_z(sorted(oids))
    print(f"  got {len(z)}")

    for label, rows in all_rows.items():
        print()
        print("=" * 78)
        print(label)
        print("=" * 78)
        for r in rows:
            pa, pb = z.get(r["oa"]), z.get(r["ob"])
            if not pa or not pb:
                print(f"  {r['la']} x {r['lb']}: geometry not returned")
                continue
            za = z_at(pa, r["px"], r["py"])
            zb = z_at(pb, r["px"], r["py"])
            print(f"  {r['la']:>7} x {r['lb']:<7} "
                  f"z {za:8.3f} / {zb:8.3f}   dz {abs(za - zb):7.3f} m   "
                  f"{(r['nam_a'] or '?')[:24]:<24} x {(r['nam_b'] or '?')[:24]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
