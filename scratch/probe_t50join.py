"""Does AMDS layer 1's t50FID join to LINZ Topo50 bridge_cl / road_cl?

Read-only.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def count(where: str, lid: int = 1) -> int:
    return get(f"{BASE}/{lid}/query", where=where,
               returnCountOnly="true").get("count", -1)


def main() -> None:
    print("### AMDS layer 1 t50FID coverage")
    tot = count("1=1")
    print(f"  total links                {tot}")
    print(f"  t50FID IS NOT NULL         {count('t50FID IS NOT NULL')}")
    print(f"  t50FID > 0                 {count('t50FID > 0')}")
    print(f"  roadway links (type=1)     {count('modelAssetType=1')}")
    print(f"  roadway with t50FID>0      {count('modelAssetType=1 AND t50FID > 0')}")

    bridges = json.loads((HERE / "_layer-50244_akl.json").read_text())
    fids = sorted({f["properties"]["t50_fid"] for f in bridges})
    print(f"\n### Auckland Topo50 bridge_cl fids: {len(fids)}")
    hits = 0
    matched = []
    for i in range(0, len(fids), 200):
        chunk = fids[i:i + 200]
        w = "t50FID IN (" + ",".join(str(v) for v in chunk) + ")"
        j = get(f"{BASE}/1/query", where=w,
                outFields="amdsIDNetworkModel,t50FID,modelAssetType",
                returnGeometry="false", resultRecordCount=2000)
        feats = j.get("features", [])
        hits += len(feats)
        matched += [f["attributes"] for f in feats[:5]]
    print(f"  AMDS links whose t50FID equals a bridge_cl fid: {hits}")
    for a in matched[:10]:
        print(f"    {a}")

    tunnels = json.loads((HERE / "_layer-50366_akl.json").read_text())
    tfids = sorted({f["properties"]["t50_fid"] for f in tunnels})
    w = "t50FID IN (" + ",".join(str(v) for v in tfids) + ")"
    j = get(f"{BASE}/1/query", where=w,
            outFields="amdsIDNetworkModel,t50FID,modelAssetType",
            returnGeometry="false")
    print(f"\n### Auckland Topo50 tunnel_cl fids: {len(tfids)}; "
          f"AMDS t50FID matches: {len(j.get('features', []))}")
    for f in j.get("features", [])[:10]:
        print(f"    {f['attributes']}")


if __name__ == "__main__":
    main()
