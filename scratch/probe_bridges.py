"""Pull LINZ Topo50 bridge/tunnel/road-centreline schema and Auckland extracts.

Read-only. The API key is never printed.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV = HERE.parent / ".env"


def key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LINZ_LDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no key")


K = key()
WFS = f"https://data.linz.govt.nz/services;key={K}/wfs"
AKL = (1740000, 5890000, 1780000, 5940000)


def describe(type_name: str) -> None:
    r = requests.get(WFS, params={"service": "WFS", "version": "2.0.0",
                                  "request": "DescribeFeatureType",
                                  "typeNames": type_name,
                                  "outputFormat": "application/json"},
                     timeout=180)
    print(f"--- DescribeFeatureType {type_name}  HTTP {r.status_code}")
    try:
        j = r.json()
    except Exception:
        print(r.text.replace(K, "<KEY>")[:2000])
        return
    for ft in j.get("featureTypes", []):
        for p in ft.get("properties", []):
            print(f"    {p.get('name'):<28} {p.get('localType')} "
                  f"(nillable={p.get('nillable')})")


def fetch(type_name: str, bbox=None, count=None, cql=None) -> list[dict]:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
              "typeNames": type_name, "outputFormat": "application/json",
              "SRSNAME": "EPSG:2193"}
    if bbox:
        # EPSG:2193 has official axis order (northing, easting); the urn form
        # honours it, so send y,x,y,x.
        x0, y0, x1, y1 = bbox
        params["bbox"] = (f"{y0},{x0},{y1},{x1},"
                          "urn:ogc:def:crs:EPSG::2193")
    if count:
        params["count"] = count
    if cql:
        params["cql_filter"] = cql
    r = requests.get(WFS, params=params, timeout=600)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text.replace(K, '<KEY>')[:600]}")
        return []
    return r.json().get("features", [])


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"

    if what in ("all", "schema"):
        for t in ("layer-50244", "layer-50366", "layer-50329"):
            describe(f"data.linz.govt.nz:{t}")
            print()

    if what in ("all", "akl"):
        for t, label in (("layer-50244", "Bridge centrelines Topo50"),
                         ("layer-50366", "Tunnel centrelines Topo50")):
            feats = fetch(f"data.linz.govt.nz:{t}", bbox=AKL)
            print(f"=== {label} in Auckland bbox: {len(feats)} features")
            if feats:
                geoms = Counter(f["geometry"]["type"] for f in feats if f.get("geometry"))
                print(f"    geometry types: {dict(geoms)}")
                keys = sorted(feats[0]["properties"].keys())
                print(f"    properties: {keys}")
                for f in feats[:5]:
                    print(f"    {json.dumps(f['properties'], ensure_ascii=False)[:220]}")
                for fld in keys:
                    vals = Counter(str(f["properties"].get(fld))[:30] for f in feats)
                    if len(vals) <= 12:
                        print(f"    values[{fld}]: {dict(vals.most_common(12))}")
                out = HERE / f"_{t}_akl.json"
                out.write_text(json.dumps(feats), encoding="utf-8")
                print(f"    saved -> {out.name}")


if __name__ == "__main__":
    main()
