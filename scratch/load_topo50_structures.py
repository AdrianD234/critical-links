"""Download LINZ Topo50 bridge and tunnel centrelines nationally into PostGIS.

This is the only AUTHORITATIVE, positive evidence of a road-over-road structure
that exists for New Zealand. AMDS itself has none: no structure attribute in any
of its fourteen layers, and its z-values are a LiDAR terrain drape (layer 4's
own metadata says so - zAccuracyMethodUsed is LiDAR on 611,884 of 621,679 rows
and Surveyed on none).

Topo50 bridges do NOT cover everything: measured on the SH1 Southern Motorway,
a mapped bridge sits within 10 m of 45% of grade-separation candidates. So this
CONFIRMS a structure; the absence of one confirms nothing, and the classifier
must not read it as evidence of an at-grade crossing.

    python ../scratch/load_topo50_structures.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

from nzcl import db
from nzcl.config import REPO_ROOT

BRIDGE = "layer-50244"   # NZ Bridge Centrelines (Topo, 1:50k)
TUNNEL = "layer-50366"   # NZ Tunnel Centrelines (Topo, 1:50k)

# NZ in EPSG:2193, generously bounded, tiled so no single request is huge.
X0, Y0, X1, Y1 = 1_000_000, 4_700_000, 2_100_000, 6_250_000
STEP = 250_000
PAGE = 5000


def key() -> str:
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LINZ_LDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no LINZ_LDS_API_KEY in .env")


def fetch(wfs: str, layer: str, bbox) -> list[dict]:
    x0, y0, x1, y1 = bbox
    out: list[dict] = []
    start = 0
    while True:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": layer, "outputFormat": "application/json",
            "SRSNAME": "EPSG:2193", "count": PAGE, "startIndex": start,
            # EPSG:2193 has official axis order northing,easting; the urn form
            # honours it, so the bbox goes y,x,y,x. Sending x,y silently
            # returns nothing, which is how this is usually got wrong.
            "bbox": f"{y0},{x0},{y1},{x1},urn:ogc:def:crs:EPSG::2193",
        }
        for attempt in range(4):
            try:
                r = requests.get(wfs, params=params, timeout=300)
                r.raise_for_status()
                j = r.json()
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    raise
                print(f"      retry {attempt+1}: {type(exc).__name__}")
                time.sleep(3 * (attempt + 1))
        feats = j.get("features", [])
        out.extend(feats)
        if len(feats) < PAGE:
            return out
        start += PAGE


def main() -> int:
    wfs = f"https://data.linz.govt.nz/services;key={key()}/wfs"

    db.execute("""
        CREATE TABLE IF NOT EXISTS ext_structures (
          source        text    NOT NULL,
          feature_id    bigint  NOT NULL,
          kind          text    NOT NULL,
          use_1         text,
          name          text,
          geom_2193     geometry(LineString, 2193) NOT NULL,
          PRIMARY KEY (source, feature_id)
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS ext_structures_geom "
               "ON ext_structures USING gist (geom_2193)")
    db.execute("DELETE FROM ext_structures WHERE source IN (%s, %s)",
               (BRIDGE, TUNNEL))

    total = 0
    for layer, kind in ((BRIDGE, "bridge"), (TUNNEL, "tunnel")):
        got = 0
        seen: set[int] = set()
        for x in range(X0, X1, STEP):
            for y in range(Y0, Y1, STEP):
                feats = fetch(wfs, layer, (x, y, x + STEP, y + STEP))
                if not feats:
                    continue
                rows = []
                for f in feats:
                    props = f.get("properties") or {}
                    fid = props.get("t50_fid")
                    g = f.get("geometry") or {}
                    if fid is None or g.get("type") != "LineString":
                        continue
                    if fid in seen:
                        continue
                    seen.add(fid)
                    coords = ",".join(f"{c[0]} {c[1]}" for c in g["coordinates"])
                    rows.append((layer, int(fid), kind,
                                 props.get("use_1") or props.get("use1"),
                                 props.get("name"),
                                 f"LINESTRING({coords})"))
                if rows:
                    with db.connection() as conn:
                        with conn.cursor() as cur:
                            cur.executemany(
                                "INSERT INTO ext_structures (source, feature_id, "
                                " kind, use_1, name, geom_2193) VALUES "
                                "(%s,%s,%s,%s,%s, ST_GeomFromText(%s, 2193)) "
                                "ON CONFLICT DO NOTHING", rows)
                    got += len(rows)
                print(f"  {kind} tile ({x},{y}): +{len(rows)}  running {got}",
                      flush=True)
        print(f"{kind}: {got} features")
        total += got

    n = db.query_one("SELECT count(*) n FROM ext_structures")["n"]
    by = db.query("SELECT kind, use_1, count(*) FROM ext_structures "
                  "GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20")
    print(f"\next_structures now holds {n} rows")
    for r in by:
        print(f"  {r['kind']:<8} {str(r['use_1']):<16} {r['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
