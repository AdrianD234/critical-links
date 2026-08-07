"""Performance of the boundary-movement engine, measured by stage.

    python -m nzcl.benchv2 stages   <snapshotId> [n]
    python -m nzcl.benchv2 routers  <snapshotId> [n]

`stages` times each phase of a request separately, because "the request took
900 ms" is not an actionable number and "port generation took 700 ms of it" is.

`routers` benchmarks the shortest-path algorithms this pgRouting build ACTUALLY
has. They are probed rather than assumed: `pgr_trsp` is absent from the Ubuntu
3.6.1 package, which is why turn restrictions are handled with an expanded
graph, so nothing here takes a function's existence on trust.

A* HEURISTICS AND WHY ONLY FOR DISTANCE
---------------------------------------
pgr_aStar and pgr_bdAstar need node coordinates on the edge query, and their
heuristic must never OVERESTIMATE the remaining cost or the answer stops being
a shortest path. In EPSG:2193 the straight-line distance between two nodes is
in metres and can never exceed the distance along the roads, so it is
admissible for the distance metric.

It is NOT admissible for time. A straight line in metres compared against a
cost in seconds is not a bound on anything, and dividing by a maximum speed to
make it one would need a maximum speed this dataset does not publish. So A* is
benchmarked and used for distance only, which is also the canonical metric.

MEASURED, 2026-08-08, national snapshot (375,696 links / 731,286 arcs)
---------------------------------------------------------------------
Fifteen replacement-path queries, each with the closed link's own arcs
excluded:

    pgr_dijkstra     p50  462.7 ms   p95  500.4 ms   exact
    pgr_bddijkstra   p50  466.2 ms   p95  471.3 ms   exact
    pgr_astar        p50  756.5 ms   p95 1047.6 ms   exact
    pgr_bdastar      p50  751.9 ms   p95  775.6 ms   NOT EXACT

Two conclusions, and the second is the important one.

A* is SLOWER here, which is not what the textbook says and is easy to explain:
the dominant cost of any of these queries is loading the edge set, and the A*
edge query needs two extra joins to `nodes` for the coordinates. On this graph
that costs more than the guided search saves. `pgr_dijkstra` therefore remains
the canonical router - on measurement, not on preference.

`pgr_bdAstar` RETURNS A NON-SHORTEST PATH. On link 150288 (nodes 141607 ->
140873, its own two arcs excluded) it reports 28,878.857 m over 37 edges where
pgr_dijkstra, pgr_bdDijkstra and pgr_aStar all report 26,085.832 m over 39 -
10.7% too long. Re-summing the Dijkstra path's arc costs directly from the
`arcs` table gives 26,085.832 m, so the shorter answer is the correct one.

The cause is the heuristic, not the graph: the SAME call with `heuristic => 0`
returns 26,085.832 m. Bidirectional A* in pgRouting 3.6.1 terminates
incorrectly when given an admissible heuristic. Nothing in this system uses
pgr_bdAstar, and nothing should. It is left in the benchmark so the finding
keeps being re-measured rather than becoming folklore.
"""

from __future__ import annotations

import statistics
import sys
import time
from typing import Callable

from . import db, impactv2
from .routing import _edge_sql

#: The four the brief asks about. Probed before use.
CANDIDATE_ROUTERS = ("pgr_dijkstra", "pgr_bddijkstra", "pgr_astar", "pgr_bdastar")

#: Stages reported, in the order a request runs them.
STAGES = ("closure_resolution", "port_generation", "intact_movements",
          "isolation", "replacement_paths", "corridor_expansion",
          "geometry_assembly", "total")


def available_routers() -> list[str]:
    """Which of the candidates this build actually ships."""
    rows = db.query(
        "SELECT DISTINCT p.proname FROM pg_proc p "
        "  JOIN pg_namespace n ON n.oid = p.pronamespace "
        " WHERE p.proname = ANY(%s) ORDER BY 1", (list(CANDIDATE_ROUTERS),))
    return [r["proname"] for r in rows]


def _astar_edge_sql(snapshot_id: str, excluded) -> str:
    """The edge query with node coordinates attached, for the A* family.

    Coordinates are EPSG:2193 metres, so `heuristic => 5` (Euclidean) is a true
    lower bound on the remaining distance.
    """
    base = _edge_sql(snapshot_id, "car", "distance", excluded)
    snap = snapshot_id.replace("'", "''")
    return (
        f"SELECT e.id, e.source, e.target, e.cost, e.reverse_cost, "
        f"       ST_X(ns.geom_2193) AS x1, ST_Y(ns.geom_2193) AS y1, "
        f"       ST_X(nt.geom_2193) AS x2, ST_Y(nt.geom_2193) AS y2 "
        f"  FROM ({base}) e "
        f"  JOIN nodes ns ON ns.snapshot_id='{snap}' AND ns.node_id=e.source "
        f"  JOIN nodes nt ON nt.snapshot_id='{snap}' AND nt.node_id=e.target"
    )


def _time_router(name: str, snapshot_id: str, pairs, excluded) -> dict:
    """One router over the same pairs, with the same exclusions."""
    times: list[float] = []
    costs: list[float | None] = []
    failures = 0
    for u, v, excl in pairs:
        if name in ("pgr_astar", "pgr_bdastar"):
            sql = _astar_edge_sql(snapshot_id, excl)
            call = (f"SELECT max(agg_cost) AS cost FROM {name}"
                    f"(%s, %s::bigint, %s::bigint, directed => true, "
                    f"heuristic => 5)")
        else:
            sql = _edge_sql(snapshot_id, "car", "distance", excl)
            call = (f"SELECT max(agg_cost) AS cost FROM {name}"
                    f"(%s, %s::bigint, %s::bigint, directed => true)")
        t0 = time.perf_counter()
        try:
            row = db.query_one(call, (sql, u, v))
            costs.append(None if row is None or row["cost"] is None
                         else float(row["cost"]))
        except Exception:  # noqa: BLE001
            failures += 1
            costs.append(None)
        times.append((time.perf_counter() - t0) * 1000)
    return {"router": name, "times": times, "costs": costs, "failures": failures}


def _pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


def _summary(name: str, times: list[float]) -> str:
    return (f"  {name:<28} n={len(times):<4} "
            f"p50={_pct(times, 50):8.1f} ms  p95={_pct(times, 95):8.1f} ms  "
            f"max={max(times) if times else float('nan'):8.1f} ms  "
            f"mean={statistics.mean(times):8.1f} ms")


def _sample_links(snapshot_id: str, n: int) -> list[dict]:
    links = db.query(
        "SELECT link_id, source_node, target_node, closure_group_id "
        "  FROM links WHERE snapshot_id=%s AND in_analysis_area "
        "   AND source_node <> target_node ORDER BY link_id", (snapshot_id,))
    stride = max(1, len(links) // n)
    return links[::stride][:n]


# ------------------------------------------------------------------- stages
def stages(snapshot_id: str, n: int = 40, *, warm: bool = False) -> dict:
    sample = _sample_links(snapshot_id, n)
    per_stage: dict[str, list[float]] = {s: [] for s in STAGES}
    headlines: dict[str, int] = {}
    unresolved = 0
    gapped = 0

    for l in sample:
        r = impactv2.analyse(snapshot_id, int(l["link_id"]), with_geometry=True)
        for s in STAGES:
            if s in r.stage_ms:
                per_stage[s].append(float(r.stage_ms[s]))
        headlines[r.headline] = headlines.get(r.headline, 0) + 1
        if r.headline == "Analysis unresolved":
            unresolved += 1
        for g in (r.replacement_geometry, r.intact_geometry):
            if g is not None and g.has_gaps:
                gapped += 1
                break

    print(f"\n{'warm' if warm else 'cold'} run: {len(sample)} link(s) "
          f"from {snapshot_id}")
    for s in STAGES:
        if per_stage[s]:
            print(_summary(s, per_stage[s]))
    print(f"  headlines: {headlines}")
    print(f"  unresolved: {unresolved}   geometry-gapped: {gapped}")
    total = per_stage["total"]
    return {
        "n": len(sample),
        "p50_total_ms": _pct(total, 50),
        "p95_total_ms": _pct(total, 95),
        "max_total_ms": max(total) if total else None,
        "unresolved": unresolved,
        "gapped": gapped,
        "headlines": headlines,
        "stage_p95_ms": {s: _pct(v, 95) for s, v in per_stage.items() if v},
    }


# ------------------------------------------------------------------ routers
def routers(snapshot_id: str, n: int = 25) -> dict:
    have = available_routers()
    missing = [r for r in CANDIDATE_ROUTERS if r not in have]
    print(f"probed: available {have}")
    if missing:
        print(f"        MISSING from this build: {missing} - not benchmarked")

    sample = _sample_links(snapshot_id, n)
    pairs = []
    for l in sample:
        arcs = [int(r["arc_id"]) for r in db.query(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND link_id=%s",
            (snapshot_id, l["link_id"]))]
        pairs.append((int(l["source_node"]), int(l["target_node"]), arcs))

    results = {}
    baseline_costs = None
    for name in have:
        out = _time_router(name, snapshot_id, pairs, None)
        results[name] = out
        print(_summary(name, out["times"])
              + f"  failures={out['failures']}")
        if name == "pgr_dijkstra":
            baseline_costs = out["costs"]

    # An algorithm that is fast and wrong is not a candidate. Every router must
    # agree with Dijkstra to the metre on the same pairs.
    if baseline_costs is not None:
        for name, out in results.items():
            disagreements = [
                (i, a, b) for i, (a, b) in enumerate(zip(baseline_costs,
                                                         out["costs"]))
                if (a is None) != (b is None)
                or (a is not None and b is not None and abs(a - b) > 1e-6)]
            verdict = ("agrees with pgr_dijkstra on every pair"
                       if not disagreements
                       else f"DISAGREES on {len(disagreements)} pair(s): "
                            f"{disagreements[:3]}")
            print(f"  {name:<28} {verdict}")
    return {name: {"p50_ms": _pct(o["times"], 50), "p95_ms": _pct(o["times"], 95),
                   "failures": o["failures"]} for name, o in results.items()}


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    mode = argv[0]
    snapshot = argv[1] if len(argv) > 1 else db.query_one(
        "SELECT snapshot_id FROM network_snapshots WHERE coverage_kind='national'"
        " ORDER BY retrieved_at_utc DESC LIMIT 1")["snapshot_id"]
    n = int(argv[2]) if len(argv) > 2 else 40

    if mode == "stages":
        cold = stages(snapshot, n)
        warm = stages(snapshot, n, warm=True)
        print("\ncold vs warm total p95: "
              f"{cold['p95_total_ms']:.1f} ms -> {warm['p95_total_ms']:.1f} ms")
        # Cold and warm must be SEMANTICALLY identical: the same headlines in
        # the same counts. A cache that changed an answer would show here.
        if cold["headlines"] != warm["headlines"]:
            print("  *** cold and warm disagree on headlines: "
                  f"{cold['headlines']} vs {warm['headlines']}")
        else:
            print("  cold and warm agree on every headline count")
        return 0
    if mode == "routers":
        routers(snapshot, n)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
