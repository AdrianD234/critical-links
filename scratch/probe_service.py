"""Ask the live AMDS service what it publishes: z-values, structure attributes,
and the coded-value domains behind modelAssetType and friends.

Read-only. No writes, no state."""
from __future__ import annotations

import json
import sys

import requests

BASE = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
        "/AMDS_NetworkModel_PROD/FeatureServer")


def get(url: str, **params) -> dict:
    params.setdefault("f", "json")
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"{url}: {j['error']}")
    return j


def layer_meta(lid: int) -> None:
    m = get(f"{BASE}/{lid}")
    print("=" * 78)
    print(f"LAYER {lid}  {m.get('name')}   type={m.get('type')} "
          f"geom={m.get('geometryType')}")
    for k in ("hasZ", "hasM", "supportsReturningGeometryProperties",
              "hasGeometryProperties", "maxRecordCount"):
        print(f"  {k:<38} {m.get(k)}")
    print(f"  extent has Z: "
          f"{'zmin' in (m.get('extent') or {})}  "
          f"{ {k: v for k, v in (m.get('extent') or {}).items() if k.startswith('z')} }")
    print("  fields:")
    for f in m.get("fields", []):
        dom = f.get("domain")
        print(f"    - {f['name']:<36} {f['type'][13:]:<12} {f.get('alias','')}")
        if dom and dom.get("codedValues"):
            for cv in dom["codedValues"]:
                print(f"          {cv['code']!r:>5} = {cv['name']}")


def sample_geometry(lid: int, n: int = 3) -> None:
    print("-" * 78)
    print(f"  sample of layer {lid} WITH returnZ=true")
    j = get(f"{BASE}/{lid}/query", where="1=1", outFields="*",
            returnGeometry="true", returnZ="true", returnM="true",
            resultRecordCount=n, outSR=2193)
    print(f"    hasZ on response: {j.get('hasZ')}  hasM: {j.get('hasM')}")
    for feat in j.get("features", [])[:n]:
        g = feat.get("geometry") or {}
        paths = g.get("paths") or []
        pt = paths[0][0] if paths and paths[0] else None
        print(f"    first vertex: {pt}  (len {len(pt) if pt else 0})")
    return j


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]] or [1, 4]
    for lid in ids:
        layer_meta(lid)
        if lid in (0, 1):
            sample_geometry(lid)
