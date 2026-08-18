"""Coverage of ramp / motorway markers in AMDS RouteName (11) and its join (13).

Read-only.
"""
from __future__ import annotations

import requests

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


def count(lid: int, where: str) -> int:
    return get(f"{BASE}/{lid}/query", where=where,
               returnCountOnly="true").get("count", -1)


def main() -> None:
    m = get(f"{BASE}/13")
    print(f"LAYER 13 {m.get('name')} fields:")
    for f in m.get("fields", []):
        print(f"  - {f['name']:<32} {f['type'][13:]}")
    print(f"  rows: {count(13, '1=1')}")
    for f in m.get("fields", []):
        if f["name"].lower() in ("rampType".lower(),):
            print(f.get("domain"))

    m11 = get(f"{BASE}/11")
    for f in m11.get("fields", []):
        if f["name"] in ("rampType", "direction", "status", "routeSubGroup"):
            dom = f.get("domain") or {}
            print(f"\n  {f['name']} domain:")
            for cv in dom.get("codedValues", []):
                print(f"     {cv['code']!r:>5} = {cv['name']}")

    print("\n### RouteName (11) coverage")
    print(f"  total                      {count(11, '1=1')}")
    print(f"  rampNumber IS NOT NULL     {count(11, 'rampNumber IS NOT NULL')}")
    print(f"  rampType IS NOT NULL       {count(11, 'rampType IS NOT NULL')}")
    print(f"  interchangeNumber NOT NULL {count(11, 'interchangeNumber IS NOT NULL')}")
    print(f"  routeGroup=1 (State Hwy)   {count(11, 'routeGroup=1')}")
    print(f"  nameType=48 (Motorway)     {count(11, 'nameType=48')}")
    print(f"  nameType=26 (Expressway)   {count(11, 'nameType=26')}")
    print(f"  nameType=11 (Bridge)       {count(11, 'nameType=11')}")
    for word in ("BRIDGE", "TUNNEL", "OVERBRIDGE", "VIADUCT", "UNDERPASS",
                 "OVERPASS", "FLYOVER"):
        w = f"UPPER(routeNameFullASCII) LIKE '%{word}%'"
        print(f"  name contains {word:<12} {count(11, w)}")

    j = get(f"{BASE}/11/query", where="nameType=11", outFields="routeNameFullASCII,routeGroup,nameType",
            returnGeometry="false", resultRecordCount=15)
    print("  sample nameType=11 (Bridge) route names:")
    for f in j.get("features", []):
        print(f"    {f['attributes'].get('routeNameFullASCII')!r}")


if __name__ == "__main__":
    main()
