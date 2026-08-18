"""Dump full field/domain metadata for AMDS layers. Read-only."""
from __future__ import annotations

import sys

import requests

BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def main() -> None:
    root = get(BASE)
    print("### SERVICE LAYERS/TABLES")
    for entry in root.get("layers", []) + root.get("tables", []):
        print(f"  id={entry['id']:<3} {entry.get('name')}  "
              f"type={entry.get('type')} geom={entry.get('geometryType')}")
    print()
    for lid in [int(a) for a in sys.argv[1:]]:
        m = get(f"{BASE}/{lid}")
        print("=" * 78)
        print(f"LAYER {lid}  {m.get('name')}  type={m.get('type')} "
              f"geom={m.get('geometryType')} hasZ={m.get('hasZ')}")
        print(f"  description: {(m.get('description') or '')[:600]}")
        try:
            cnt = get(f"{BASE}/{lid}/query", where="1=1",
                      returnCountOnly="true").get("count")
        except SystemExit:
            cnt = "?"
        print(f"  rowcount: {cnt}")
        print(f"  fields ({len(m.get('fields') or [])}):")
        for f in m.get("fields", []):
            dom = f.get("domain")
            print(f"    - {f['name']:<38} {f['type'][13:]:<12} "
                  f"{f.get('alias', '')}")
            if dom and dom.get("codedValues"):
                for cv in dom["codedValues"]:
                    print(f"          {cv['code']!r:>6} = {cv['name']}")
            elif dom:
                print(f"          domain: {str(dom)[:200]}")
        print()


if __name__ == "__main__":
    main()
