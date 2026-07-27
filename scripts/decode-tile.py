"""Decode a vector tile from a running backend and report its actual schema.

    python scripts/decode-tile.py <base_url> [z] [x] [y]

The map-style tests validate the STYLE definition, not a real tile. That leaves
the contract between what a backend emits and what the client reads completely
untested. This decodes the bytes and prints exactly what arrives.
"""

from __future__ import annotations

import sys
import urllib.request

import mapbox_vector_tile

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
Z = int(sys.argv[2]) if len(sys.argv) > 2 else 12
X = int(sys.argv[3]) if len(sys.argv) > 3 else 4036
Y = int(sys.argv[4]) if len(sys.argv) > 4 else 2564

# What apps/web/src/MapView.tsx actually reads.
CLIENT_EXPECTS = ["linkId", "stateHighway", "core", "roadName", "oneway"]


def main() -> int:
    url = f"{BASE}/tiles/{Z}/{X}/{Y}.pbf"
    print(f"GET {url}")
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    print(f"  {len(data)} bytes, HTTP {r.status}\n")

    tile = mapbox_vector_tile.decode(data)
    for layer_name, layer in tile.items():
        feats = layer.get("features", [])
        print(f"layer {layer_name!r}: {len(feats)} features")
        if not feats:
            continue
        f = feats[0]
        print(f"  feature id: {f.get('id')!r}")
        props = f.get("properties", {})
        print(f"  properties ({len(props)}):")
        for k, v in sorted(props.items()):
            print(f"    {k:16} = {v!r}")

    print("\nclient compatibility (MapView.tsx reads these):")
    all_props: set[str] = set()
    for layer in tile.values():
        for f in layer.get("features", []):
            all_props.update(f.get("properties", {}))
            break
    ok = True
    for key in CLIENT_EXPECTS:
        present = key in all_props
        if not present:
            ok = False
        print(f"  {key:16} {'present' if present else 'MISSING'}")

    has_id = any(
        f.get("id") is not None
        for layer in tile.values()
        for f in layer.get("features", [])[:1]
    )
    print(f"  feature.id       {'present' if has_id else 'MISSING'}")

    print()
    if ok:
        print("RESULT: tile satisfies the client contract")
        return 0
    print("RESULT: tile does NOT satisfy the client contract")
    print("  -> map clicks resolve properties.linkId === undefined")
    print("  -> style expressions on stateHighway fall back to the default")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
