"""End-to-end smoke test against a running API.

    python scripts/smoke-api.py [base_url]

Checks the endpoints the web client depends on, and prints enough of each
response to see that the numbers are sane rather than merely present.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=120) as r:
        return json.load(r)


def main() -> int:
    print("== health ==")
    h = get("/health")
    print(f"  {h['snapshotId']}  links={h['links']} arcs={h['arcs']} nodes={h['nodes']}")
    print(f"  db: postgis {h['database']['postgis']}, pgrouting {h['database']['pgrouting']}")

    print("\n== metadata ==")
    m = get("/api/v1/network/metadata")
    print(f"  graph: {m['graph']}")
    print(f"  status: {m['snapshotStatus']}  clipped: {m['clippedExtract']}")
    print(f"  limitations stated: {len(m['limitations'])}")

    print("\n== search ==")
    s = get("/api/v1/links/search?name=Moonshine&limit=3")
    print(f"  count={s['count']}")
    for r in s["results"]:
        print(f"    {r['roadName']}  {r['lengthM']} m  oneway={r['oneway']}")
    if not s["results"]:
        print("  no results; cannot continue")
        return 1

    link = s["results"][0]
    ref = urllib.parse.quote(link["amdsId"], safe="")

    print("\n== detour ==")
    d = get(f"/api/v1/links/{ref}/detour?metric=distance&vehicle=car"
            f"&closure_scope=physical&direction=both")
    print(f"  link: {d['selectedLink']['roadName']} {d['selectedLink']['lengthM']} m")
    print(f"  closure: {d['closure']['removedLinkCount']} links / "
          f"{d['closure']['removedArcCount']} arcs")
    for key in ("forward", "reverse"):
        dd = d.get(key)
        if not dd:
            continue
        mm = dd["metrics"]
        feats = len(dd["routeGeoJson"]["features"]) if dd["routeGeoJson"] else 0
        print(f"  {key}: {dd['status']} alt={mm['alternativeDistanceM']} "
              f"penalty={mm['networkPenaltyM']} ratio={mm['detourRatioVsLink']} "
              f"routeFeatures={feats}")
        print(f"     flags: {dd['qualityFlags']}")
        if dd.get("corridor"):
            c = dd["corridor"]
            print(f"     corridor {c['status']} penalty={c['penaltyM']}")
        if dd.get("isolation"):
            i = dd["isolation"]
            print(f"     isolation side={i['side']} links={i['pocketLinkCount']}")
    if d.get("fitBounds"):
        print(f"  fitBounds: {[round(b, 4) for b in d['fitBounds']]}")

    print("\n== closure scopes differ ==")
    for scope in ("physical", "directed"):
        r = get(f"/api/v1/links/{ref}/detour?closure_scope={scope}"
                f"&direction=forward&geometry=false")
        print(f"  {scope:9} removes {r['closure']['removedArcCount']} arcs "
              f"-> {r['forward']['status']}")

    print("\n== profiles ==")
    for veh in ("car", "heavy", "emergency"):
        r = get(f"/api/v1/links/{ref}/detour?vehicle={veh}"
                f"&direction=forward&geometry=false")
        print(f"  {veh:9} {r['forward']['status']} "
              f"alt={r['forward']['metrics']['alternativeDistanceM']}")

    print("\n== error handling ==")
    try:
        get("/api/v1/links/does-not-exist/detour")
        print("  FAIL: expected 404")
        return 1
    except urllib.error.HTTPError as e:
        print(f"  unknown link -> HTTP {e.code} (correct)")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
