"""Ramp subgroup counts; t50FID -> road_cl verification; road_cl noding test."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import requests
from shapely.geometry import shape

HERE = Path(__file__).resolve().parent
ENV = HERE.parent / ".env"
BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")
BOX = (1755000, 5908000, 1765000, 5920000)


def key() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("LINZ_LDS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no key")


K = key()
WFS = f"https://data.linz.govt.nz/services;key={K}/wfs"


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def count(lid: int, where: str) -> int:
    return get(f"{BASE}/{lid}/query", where=where,
               returnCountOnly="true").get("count", -1)


def wfs_box(type_name: str, box) -> list[dict]:
    x0, y0, x1, y1 = box
    r = requests.get(WFS, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": f"data.linz.govt.nz:{type_name}",
        "outputFormat": "application/json", "SRSNAME": "EPSG:2193",
        "bbox": f"{y0},{x0},{y1},{x1},urn:ogc:def:crs:EPSG::2193"}, timeout=600)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text.replace(K, '<KEY>')[:300]}")
        return []
    return r.json().get("features", [])


def main() -> None:
    print("### RouteName routeSubGroup")
    for code, label in ((16, "Ramp"), (26, "State Highway"), (25, "Slip"),
                        (13, "Loop"), (30, "Crossing"), (22, "Toll Road")):
        print(f"  {code:>3} {label:<16} {count(11, f'routeSubGroup={code}')}")

    print("\n### t50FID -> which Topo50 class?")
    j = get(f"{BASE}/1/query", where="t50FID > 0",
            outFields="t50FID,modelAssetType", returnGeometry="false",
            resultRecordCount=25)
    fids = [f["attributes"]["t50FID"] for f in j.get("features", [])]
    print(f"  sample AMDS t50FIDs: {fids[:10]}")
    cql = "t50_fid IN (" + ",".join(str(v) for v in fids) + ")"
    r = requests.get(WFS, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "data.linz.govt.nz:layer-50329",
        "outputFormat": "application/json", "cql_filter": cql}, timeout=300)
    n = len(r.json().get("features", [])) if r.status_code == 200 else -1
    print(f"  of {len(fids)} sample t50FIDs, found in road_cl (50329): {n}")
    r2 = requests.get(WFS, params={
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": "data.linz.govt.nz:layer-50244",
        "outputFormat": "application/json", "cql_filter": cql}, timeout=300)
    n2 = len(r2.json().get("features", [])) if r2.status_code == 200 else -1
    print(f"  ... found in bridge_cl (50244): {n2}")

    print("\n### Topo50 road_cl noding in the Auckland test box")
    feats = wfs_box("layer-50329", BOX)
    print(f"  road_cl features in box: {len(feats)}")
    ends = Counter()
    lines = []
    for f in feats:
        g = shape(f["geometry"])
        geoms = [g] if g.geom_type == "LineString" else list(g.geoms)
        for ln in geoms:
            lines.append(ln)
            ends[(round(ln.coords[0][0], 1), round(ln.coords[0][1], 1))] += 1
            ends[(round(ln.coords[-1][0], 1), round(ln.coords[-1][1], 1))] += 1
    print(f"  parts: {len(lines)}; distinct endpoints: {len(ends)}; "
          f"shared endpoints (deg>=2): {sum(1 for v in ends.values() if v >= 2)}")
    print(f"  degree histogram: {dict(Counter(ends.values()).most_common(8))}")
    props = Counter()
    for f in feats:
        props[f["properties"].get("hway_num") is not None] += 1
    print(f"  hway_num populated: {props}")


if __name__ == "__main__":
    main()
