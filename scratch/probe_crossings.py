"""Do LINZ Topo50 bridge/tunnel centrelines mark AMDS grade separations?

Finds geometric link-link crossings in an Auckland motorway test box that do NOT
share an endpoint (i.e. grade separations, since AMDS nodes at-grade junctions),
then measures the distance from each crossing point to the nearest Topo50
bridge_cl / tunnel_cl. Also pulls AMDS layer 4 zStart/zEnd for the two links.

Read-only.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import requests
from shapely.geometry import LineString, Point, shape
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")

# SH1 Southern Motorway, Greenlane -> Mt Wellington, plus SH16 fringe.
BOX = (1755000, 5908000, 1765000, 5920000)


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=300)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def fetch_links() -> list[dict]:
    cache = HERE / "_amds_box.json"
    if cache.exists():
        return json.loads(cache.read_text())
    out: list[dict] = []
    offset = 0
    geom = json.dumps({"xmin": BOX[0], "ymin": BOX[1], "xmax": BOX[2],
                       "ymax": BOX[3], "spatialReference": {"wkid": 2193}})
    while True:
        j = get(f"{BASE}/1/query", where="status=1", geometry=geom,
                geometryType="esriGeometryEnvelope", inSR=2193, outSR=2193,
                spatialRel="esriSpatialRelIntersects",
                outFields="amdsIDNetworkModel,modelAssetType,modeVehicle,t50FID",
                returnGeometry="true", returnZ="true",
                resultOffset=offset, resultRecordCount=2000)
        feats = j.get("features", [])
        out += feats
        if len(feats) < 2000:
            break
        offset += 2000
    cache.write_text(json.dumps(out))
    return out


def main() -> None:
    feats = fetch_links()
    print(f"AMDS links in test box: {len(feats)}")
    lines, meta = [], []
    roads_only = "--roads" in sys.argv
    for f in feats:
        if roads_only and f["attributes"].get("modelAssetType") != 1:
            continue
        paths = (f.get("geometry") or {}).get("paths") or []
        for p in paths:
            if len(p) < 2:
                continue
            lines.append(LineString([(pt[0], pt[1]) for pt in p]))
            meta.append((f["attributes"], p))
    print(f"  parts: {len(lines)}")

    tree = STRtree(lines)
    seen = set()
    crossings = []
    for i, ln in enumerate(lines):
        for j in tree.query(ln):
            j = int(j)
            if j <= i:
                continue
            other = lines[j]
            if not ln.intersects(other):
                continue
            inter = ln.intersection(other)
            pts = ([inter] if inter.geom_type == "Point"
                   else list(inter.geoms) if inter.geom_type == "MultiPoint"
                   else [])
            for pt in pts:
                ends = [Point(ln.coords[0]), Point(ln.coords[-1]),
                        Point(other.coords[0]), Point(other.coords[-1])]
                if min(pt.distance(e) for e in ends) < 1.0:
                    continue  # noded == at-grade junction
                k = (round(pt.x, 1), round(pt.y, 1))
                if k in seen:
                    continue
                seen.add(k)
                crossings.append((pt, meta[i][0], meta[j][0], meta[i][1],
                                  meta[j][1]))
    print(f"  grade-separated candidates (crossing, no shared node): "
          f"{len(crossings)}")

    bridges = [shape(f["geometry"]) for f in
               json.loads((HERE / "_layer-50244_akl.json").read_text())]
    tunnels = [shape(f["geometry"]) for f in
               json.loads((HERE / "_layer-50366_akl.json").read_text())]
    btree = STRtree(bridges)
    ttree = STRtree(tunnels)

    buckets = Counter()
    dists = []
    for pt, a, b, pa, pb in crossings:
        nb = btree.nearest(pt)
        d = pt.distance(bridges[int(nb)]) if nb is not None else 9e9
        nt = ttree.nearest(pt)
        dt = pt.distance(tunnels[int(nt)]) if nt is not None else 9e9
        d = min(d, dt)
        dists.append(d)
        for lim in (5, 10, 25, 50, 100):
            if d <= lim:
                buckets[lim] += 1
    n = len(crossings)
    print("  distance from crossing point to nearest Topo50 bridge/tunnel:")
    for lim in (5, 10, 25, 50, 100):
        print(f"    <= {lim:>3} m : {buckets[lim]:>4} / {n} "
              f"({100 * buckets[lim] / max(n, 1):.1f}%)")
    dists.sort()
    if dists:
        print(f"    median {dists[len(dists) // 2]:.1f} m, "
              f"p90 {dists[int(len(dists) * 0.9)]:.1f} m, max {dists[-1]:.1f} m")

    # Reverse: of the Topo50 bridges inside the test box, how many sit on a
    # road-road crossing candidate?
    from shapely.geometry import box as sbox
    bx = sbox(*BOX)
    bprops = json.loads((HERE / "_layer-50244_akl.json").read_text())
    inbox = [(shape(f["geometry"]), f["properties"]) for f in bprops
             if shape(f["geometry"]).intersects(bx)]
    print(f"\n  Topo50 bridge_cl inside test box: {len(inbox)} "
          f"(use_1: {Counter(p['use_1'] for _, p in inbox)})")
    pts = [c[0] for c in crossings]
    ptree = STRtree(pts) if pts else None
    near = 0
    for g, p in inbox:
        if ptree is None:
            break
        idx = ptree.nearest(g)
        if idx is not None and g.distance(pts[int(idx)]) <= 10:
            near += 1
    print(f"    of those, within 10 m of a road-road crossing candidate: "
          f"{near} / {len(inbox)}")

    # Z at the crossing point, from the layer-1 drape, for a sample.
    print("\n  sample crossings (dz from layer-1 Z at nearest vertex):")
    for pt, a, b, pa, pb in crossings[:8]:
        za = min(pa, key=lambda v: (v[0] - pt.x) ** 2 + (v[1] - pt.y) ** 2)
        zb = min(pb, key=lambda v: (v[0] - pt.x) ** 2 + (v[1] - pt.y) ** 2)
        nb = btree.nearest(pt)
        d = pt.distance(bridges[int(nb)])
        print(f"    ({pt.x:.0f},{pt.y:.0f}) types={a['modelAssetType']}/"
              f"{b['modelAssetType']} z={za[2]:.2f}/{zb[2]:.2f} "
              f"dz={abs(za[2] - zb[2]):.2f}  nearest bridge {d:.1f} m")

    # AMDS layer 4 z for the two links of a few crossings.
    if "--geom4" in sys.argv:
        print("\n  AMDS layer 4 (Geometry) rows for crossing link pairs:")
        for pt, a, b, pa, pb in crossings[:6]:
            ids = [a["amdsIDNetworkModel"], b["amdsIDNetworkModel"]]
            w = ("amdsIDNetworkModel IN ("
                 + ",".join(f"'{i}'" for i in ids) + ")")
            j = get(f"{BASE}/4/query", where=w, outFields="*",
                    returnGeometry="false")
            print(f"    crossing ({pt.x:.0f},{pt.y:.0f}):")
            for f in j.get("features", []):
                at = f["attributes"]
                print(f"      zStart={at.get('zStart')} zEnd={at.get('zEnd')} "
                      f"zAcc={at.get('zAccuracy')} m={at.get('zAccuracyMethodUsed')} "
                      f"len={at.get('measuredLength')} len3D={at.get('length3D')}")


if __name__ == "__main__":
    main()
