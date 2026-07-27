"""Investigate a cross-validation disagreement between the two engines.

    python -m nzcl.diagnose <pg_snapshot> <amds_id> <ts_url>

When two independent implementations differ, the question is whether the ENGINES
disagree or the GRAPHS do. The two snapshots are ingested separately, so a
junction can land in a slightly different place; that produces different graphs,
on each of which a different answer is legitimately correct.

This checks the TypeScript route link-by-link against the pgRouting graph: every
link present, and every consecutive pair actually adjacent. A missing link or a
broken adjacency is a graph difference. If the whole route is traversable in the
pgRouting graph yet pgRouting returned something longer, that is an engine bug.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

from . import db


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 2:
        raise SystemExit("usage: diagnose <pg_snapshot> <amds_id> [ts_url]")
    snap, amds_id = argv[0], argv[1]
    ts_url = argv[2] if len(argv) > 2 else "http://127.0.0.1:8787"

    ref = urllib.parse.quote(amds_id, safe="")
    with urllib.request.urlopen(
        f"{ts_url}/api/v1/links/{ref}/detour?geometry=true&direction=forward",
        timeout=120,
    ) as r:
        ts = json.load(r)

    fwd = ts["forward"]
    print(f"link {amds_id}  ({ts['selectedLink']['roadName']})")
    print(f"  ts status={fwd['status']} alt={fwd['metrics']['alternativeDistanceM']}")

    feats = (fwd.get("routeGeoJson") or {}).get("features") or []
    ts_amds = [f["properties"]["amdsId"] for f in feats]
    print(f"  ts route uses {len(ts_amds)} arcs")

    # Which of the TypeScript route's links exist in the pgRouting graph?
    rows = db.query(
        "SELECT amds_id, link_id, source_node, target_node, length_m "
        "FROM links WHERE snapshot_id=%s AND amds_id = ANY(%s)",
        (snap, ts_amds),
    )
    found = {r["amds_id"]: r for r in rows}
    missing = [a for a in ts_amds if a not in found]
    print(f"  present in the pgRouting graph: {len(found)} / {len(set(ts_amds))} distinct")
    if missing:
        print(f"  MISSING from pgRouting graph ({len(set(missing))} distinct):")
        for a in list(dict.fromkeys(missing))[:8]:
            print(f"    {a}")
            # Was it split differently?
            base = a.split("#")[0]
            sibs = db.query(
                "SELECT amds_id, length_m FROM links WHERE snapshot_id=%s "
                "AND closure_group_id=%s ORDER BY amds_id", (snap, base))
            print(f"      pg has {len(sibs)} piece(s) of {base}: "
                  f"{[s['amds_id'] for s in sibs]}")
        print("\n  => the two engines have DIFFERENT GRAPHS, not different answers.")
        return 0

    # Every link exists: is the route actually traversable in the pg graph?
    breaks = 0
    prev = None
    for a in ts_amds:
        cur = found[a]
        if prev is not None:
            adjacent = bool({prev["source_node"], prev["target_node"]}
                            & {cur["source_node"], cur["target_node"]})
            if not adjacent:
                breaks += 1
                if breaks <= 5:
                    print(f"  BREAK between {prev['amds_id']} and {a}: "
                          f"nodes {prev['source_node']}/{prev['target_node']} vs "
                          f"{cur['source_node']}/{cur['target_node']}")
        prev = cur

    if breaks:
        print(f"\n  {breaks} adjacency break(s): the pgRouting graph does not "
              f"connect this route. DIFFERENT GRAPHS.")
    else:
        print("\n  The whole TypeScript route is traversable in the pgRouting "
              "graph, so pgRouting returning something longer would be an "
              "ENGINE BUG. Investigate the edge query.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
