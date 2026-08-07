"""Reproduction harness for the V1 corridor timeout misclassification.

Run against a real PostgreSQL + PostGIS + pgRouting database:

    cd python && PYTHONPATH=src python ../docs/audits/v1-timeout/reproduce.py

It builds one synthetic snapshot and asks the SAME question of the SAME
network four times, changing nothing but the statement-timeout budget. Every
timeout in here is a real PostgreSQL cancellation, not a simulated one.

The fixture
-----------
A 16x16 two-way street grid over (0,0)-(1500,1500) - the ballast: it is what
makes the multi-target corridor query cost real time - plus a one-way bypass
running south of it and rejoining at two grid nodes:

    (0,0) --CONN_W--> (0,-400) ==EB1==> (300,-400) ==EB2==> (600,-400) --CONN_E--> (1100,0)
      grid              400 m    one-way    300 m   one-way    300 m      640.3 m     grid
                                                   [closed]

EB1 and EB2 are one-way. Closing EB2 leaves its start node (300,-400) with no
outgoing arc at all, so the ENDPOINT measure - is there a path from the closed
link's start back to its end - is DISCONNECTED. That is correct and routine: it
is the same shape as a state-highway carriageway, where the endpoint question is
ill-posed.

The question a user actually has is the THROUGH trip: can traffic still get
past? It can. The grid carries it, 259.7 m further (1500.0 m instead of
1240.3 m between (0,-400) and (1100,0)). `detour._corridor` is what computes
that, and it is the search this reproduction times out.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from nzcl import db, detour, routing
from nzcl.fixtures import load_synthetic

GRID_N = 16
GRID_STEP = 100


def spec() -> list[dict]:
    links: list[dict] = []
    for r in range(GRID_N):
        for c in range(GRID_N - 1):
            links.append({"id": f"H{r}-{c}", "pts": [
                (c * GRID_STEP, r * GRID_STEP),
                ((c + 1) * GRID_STEP, r * GRID_STEP)]})
    for c in range(GRID_N):
        for r in range(GRID_N - 1):
            links.append({"id": f"V{r}-{c}", "pts": [
                (c * GRID_STEP, r * GRID_STEP),
                (c * GRID_STEP, (r + 1) * GRID_STEP)]})
    links += [
        {"id": "CONN_W", "pts": [(0, 0), (0, -400)]},
        {"id": "EB1", "pts": [(0, -400), (300, -400)], "oneway": True},
        {"id": "EB2", "pts": [(300, -400), (600, -400)], "oneway": True},
        {"id": "CONN_E", "pts": [(600, -400), (1100, 0)]},
    ]
    return links


def search_status(result) -> str:
    """`route_many` returned a bare mapping before the fix and a status-carrying
    result after it. This harness has to run against both, because the whole
    point is to run it on either side of the change and compare."""
    if isinstance(result, dict):
        return "(none: a bare mapping carries no status)"
    return result.status


def search_costs(result) -> dict:
    return result if isinstance(result, dict) else result.costs


def corridor_dict(c) -> dict:
    if c is None:
        return {}
    return {
        "status": c.status,
        "entryNode": c.entry_node,
        "exitNode": c.exit_node,
        "normalDistanceM": c.normal_distance_m,
        "alternativeDistanceM": c.alternative_distance_m,
        "penaltyM": c.penalty_m,
        "truncated": c.truncated,
        "exitReachable": c.exit_reachable,
        "detail": c.detail,
    }


def main() -> int:
    out = Path(__file__).parent
    net = load_synthetic(spec())
    snap = net.snapshot_id
    try:
        arcs = db.query_one("SELECT count(*) AS n FROM arcs WHERE snapshot_id=%s",
                            (snap,))["n"]
        print(f"fixture snapshot {snap}: {len(net.links)} links, "
              f"{len(net.node_coords)} nodes, {arcs} arcs\n")

        eb2 = net.link_id("EB2")
        u, v = net.nodes_of("EB2")
        excluded = [r["arc_id"] for r in db.query(
            "SELECT a.arc_id FROM arcs a JOIN links l "
            " ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id "
            "WHERE a.snapshot_id=%s AND a.closure_group_id="
            " (SELECT closure_group_id FROM links WHERE snapshot_id=%s "
            "  AND link_id=%s)", (snap, snap, eb2))]

        # ---------------------------------------------------------------- 1
        print("=" * 72)
        print("1. What the database actually does when the corridor query "
              "runs out of time")
        print("=" * 72)
        sql = routing._edge_sql(snap, "car", "distance", excluded)
        nodes = list(range(len(net.node_coords)))
        try:
            with db.connection() as conn:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT set_config('statement_timeout', '1', true)")
                        cur.execute(
                            "SELECT start_vid, end_vid, max(agg_cost) AS cost "
                            "FROM pgr_dijkstra(%s, %s::bigint[], %s::bigint[], "
                            "directed => true) GROUP BY start_vid, end_vid",
                            (sql, nodes, nodes))
                        print("   query COMPLETED - budget was not tight enough")
        except Exception as exc:  # noqa: BLE001
            print(f"   raised {type(exc).__module__}.{type(exc).__name__}: "
                  f"{str(exc).splitlines()[0]}")

        print("\n   routing.route_many with the same 1 ms budget:")
        t0 = time.perf_counter()
        timed_out = routing.route_many(snap, nodes, nodes,
                                       excluded_arcs=excluded,
                                       statement_timeout_ms=1)
        print(f"     pairs   {len(search_costs(timed_out))}")
        print(f"     status  {search_status(timed_out)}"
              f"   ({(time.perf_counter()-t0)*1000:.1f} ms)")
        t0 = time.perf_counter()
        completed = routing.route_many(snap, nodes, nodes,
                                       excluded_arcs=excluded,
                                       statement_timeout_ms=60_000)
        print("   routing.route_many with a 60 s budget:")
        print(f"     pairs   {len(search_costs(completed))}")
        print(f"     status  {search_status(completed)}"
              f"   ({(time.perf_counter()-t0)*1000:.1f} ms)")
        print("\n   Before the fix both calls returned a bare mapping, so a "
              "search that\n   found nothing and a search that never finished "
              "were the same value and\n   no caller could tell them apart.")

        # ---------------------------------------------------------------- 2
        print()
        print("=" * 72)
        print("2. What the V1 corridor search makes of that")
        print("=" * 72)
        for budget in (60_000, 20, 5, 1):
            c = detour._corridor(snap, u, v, excluded, "distance", "car",
                                 500.0, budget)
            print(f"\n   statement_timeout = {budget} ms")
            print(f"     status  {c.status}")
            print(f"     detail  {c.detail!r}")
            print(f"     penalty {c.penalty_m}")
        print("\n   Same network. Same closure. Same code. The only thing that "
              "changed is\n   how long the database was allowed to take.")
        print("   Before the fix the tight budgets read DISCONNECTED, 'search "
              "space\n   exhausted with no route to target' - a claim about the "
              "road network.\n   After it they read UNRESOLVED_TIMEOUT, which "
              "is a claim about the search.")

        # ---------------------------------------------------------------- 3
        print()
        print("=" * 72)
        print("3. What the user is told")
        print("=" * 72)
        good = detour.compute(snap, eb2, directions=["forward"],
                              statement_timeout_ms=60_000).forward
        bad = detour.compute(snap, eb2, directions=["forward"],
                             statement_timeout_ms=CORRIDOR_SQUEEZE_MS).forward
        for label, d in (("adequate budget", good),
                         (f"corridor squeezed to {CORRIDOR_SQUEEZE_MS} ms", bad)):
            print(f"\n   -- {label} --")
            print(f"     direction status  {d.status}")
            print(f"     corridor          {json.dumps(corridor_dict(d.corridor))}")
            print(f"     qualityFlags      {d.quality_flags}")
            print(f"     isolation pocket  "
                  f"{d.isolation.pocket_link_count} links, "
                  f"{d.isolation.pocket_length_m} m")
            print(f"     UI headline       {headline(d)}")

        (out / "captured-directions-after-fix.json").write_text(json.dumps({
            "adequateBudget": as_json(good),
            "corridorTimedOut": as_json(bad),
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\n   written: {out / 'captured-directions-after-fix.json'}")
        return 0
    finally:
        db.execute("DELETE FROM network_snapshots WHERE snapshot_id=%s", (snap,))


#: Budget that the single-pair endpoint searches meet comfortably and the
#: multi-target corridor search - roughly forty dijkstra runs off one edge-set
#: load - does not. Measured on the fixture above; see README.md.
CORRIDOR_SQUEEZE_MS = 5

UNRESOLVED = {"UNRESOLVED_TIMEOUT", "API_ERROR", "INVALID_GRAPH",
              "SOURCE_DATA_ERROR", "UNSUPPORTED_PROFILE"}


def headline(d) -> str:
    """The headline `apps/web/src/inspector/ResultView.tsx` renders for this.

    A transcription, and checked against the real thing rather than trusted:
    `capture-ui.mjs` drives the built app in Chromium and prints what it
    actually rendered. Both agree, before the fix and after it.
    """
    if d.status in UNRESOLVED:
        return "Analysis unresolved"
    if d.status == "DISCONNECTED":
        c, iso = d.corridor, d.isolation
        if c and c.status == "OK" and c.penalty_m is not None:
            return (f"Added distance - through trip: "
                    f"{c.penalty_m / 1000:+.2f} km")
        if c and c.status in UNRESOLVED:
            return "Analysis unresolved"
        if iso and (iso.pocket_link_count > 0 or iso.pocket_length_m > 0):
            return f"Road cut off: {iso.pocket_length_m / 1000:.2f} km"
        return "No replacement path"
    return "Added distance"


def as_json(d) -> dict:
    return {
        "status": d.status,
        "corridor": corridor_dict(d.corridor),
        "isolation": {
            "pocketLinkCount": d.isolation.pocket_link_count,
            "pocketLengthM": d.isolation.pocket_length_m,
        } if d.isolation else None,
        "qualityFlags": d.quality_flags,
        "errorDetail": d.error_detail,
        "uiHeadline": headline(d),
    }


if __name__ == "__main__":
    sys.exit(main())
