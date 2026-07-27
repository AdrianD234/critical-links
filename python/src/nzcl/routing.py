"""Shortest-path search over the arc network, using pgRouting.

Graph model
-----------
`arcs` holds one row per DIRECTED traversal, so pgRouting sees a directed graph
with `reverse_cost` omitted. Closing a road is expressed as excluding arc ids
from the edge query, which is the whole reason the arc table exists in this
shape.

Turn restrictions
-----------------
The Ubuntu build of pgRouting 3.6.1 does not ship `pgr_trsp` (verified with
scripts/probe-pgrouting.sh - only the dijkstra/astar families are present), so
banned manoeuvres cannot be handed to pgRouting directly.

They are handled instead with an edge-expanded (line) graph: `arc_transitions`
has one row per permitted arc -> arc movement, with banned movements simply
absent. A search over that graph cannot make a prohibited turn, because the
edge does not exist.

Running every query against the expanded graph would be wasteful: AMDS
publishes 60 restricted turns nationally, so the overwhelming majority of
routes cannot possibly violate one. The strategy is therefore:

  1. route on the plain arc graph (fast)
  2. check the resulting path against the restriction list (cheap)
  3. only if it violates one, re-route on the expanded graph (exact)

The answer is always correct; the expensive path is only paid when it can
change the result. `RouteResult.used_expanded_graph` records which ran.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db

Metric = Literal["distance", "time"]
Profile = Literal["car", "heavy", "emergency"]
Status = Literal[
    "OK", "DISCONNECTED", "UNRESOLVED_TIMEOUT", "INVALID_GRAPH",
    "SOURCE_DATA_ERROR", "UNSUPPORTED_PROFILE", "API_ERROR",
]

_MODE_COLUMN = {
    "car": "mode_vehicle",
    "heavy": "mode_vehicle_heavy",
    "emergency": "mode_emergency",
}

_RESTRICTION_COLUMN = {
    "car": "restricted_vehicle",
    "heavy": "restricted_heavy",
    "emergency": "restricted_emergency",
}


@dataclass
class RouteResult:
    status: Status
    #: Cost in the requested metric: metres for distance, seconds for time.
    cost: float | None
    distance_m: float | None
    time_s: float | None
    arc_ids: list[int] = field(default_factory=list)
    runtime_ms: int = 0
    detail: str | None = None
    used_expanded_graph: bool = False


def _mode_column(profile: Profile) -> str:
    col = _MODE_COLUMN.get(profile)
    if col is None:
        raise ValueError(f"unsupported vehicle profile {profile!r}")
    return col


def _cost_column(metric: Metric) -> str:
    return "cost_time_s" if metric == "time" else "cost_distance_m"


def _edge_sql(snapshot_id: str, profile: Profile, metric: Metric,
              excluded: Sequence[int]) -> str:
    """The edge query pgRouting consumes.

    Literals are inlined because pgRouting takes the inner query as text, so it
    cannot carry bind parameters. Every value interpolated here is either
    server-derived (snapshot id from the database) or an integer arc id; the
    snapshot id is escaped and arc ids are cast through int().
    """
    mode = _mode_column(profile)
    cost = _cost_column(metric)
    snap = snapshot_id.replace("'", "''")
    clauses = [
        f"snapshot_id = '{snap}'",
        mode,
        f"{cost} IS NOT NULL",
    ]
    if excluded:
        ids = ",".join(str(int(a)) for a in excluded)
        clauses.append(f"arc_id <> ALL (ARRAY[{ids}]::bigint[])")
    return (
        f"SELECT arc_id AS id, source, target, {cost} AS cost, "
        f"-1::double precision AS reverse_cost FROM arcs "
        f"WHERE {' AND '.join(clauses)}"
    )


def _transition_sql(snapshot_id: str, profile: Profile, metric: Metric,
                    excluded: Sequence[int]) -> str:
    """Edge query over the expanded graph, where a node IS an arc."""
    mode = _mode_column(profile)
    cost = _cost_column(metric)
    snap = snapshot_id.replace("'", "''")
    clauses = [
        f"t.snapshot_id = '{snap}'",
        f"a_to.{mode}", f"a_from.{mode}",
        f"a_to.{cost} IS NOT NULL",
    ]
    if excluded:
        ids = ",".join(str(int(a)) for a in excluded)
        clauses.append(f"t.from_arc <> ALL (ARRAY[{ids}]::bigint[])")
        clauses.append(f"t.to_arc <> ALL (ARRAY[{ids}]::bigint[])")
    return (
        f"SELECT t.transition_id AS id, t.from_arc AS source, t.to_arc AS target, "
        f"a_to.{cost} AS cost, -1::double precision AS reverse_cost "
        f"FROM arc_transitions t "
        f"JOIN arcs a_to ON a_to.snapshot_id = t.snapshot_id AND a_to.arc_id = t.to_arc "
        f"JOIN arcs a_from ON a_from.snapshot_id = t.snapshot_id AND a_from.arc_id = t.from_arc "
        f"WHERE {' AND '.join(clauses)}"
    )


def _same_component(snapshot_id: str, u: int, v: int) -> bool:
    row = db.query_one(
        """
        SELECT (SELECT component_id FROM nodes WHERE snapshot_id=%s AND node_id=%s)
             = (SELECT component_id FROM nodes WHERE snapshot_id=%s AND node_id=%s)
             AS same
        """,
        (snapshot_id, u, snapshot_id, v),
    )
    return bool(row and row["same"])


def _restrictions(snapshot_id: str, profile: Profile) -> list[list[int]]:
    col = _RESTRICTION_COLUMN[profile]
    rows = db.query(
        f"SELECT link_seq FROM turn_restrictions "
        f"WHERE snapshot_id=%s AND {col}",
        (snapshot_id,),
    )
    return [list(r["link_seq"]) for r in rows]


def _violates(link_path: list[int], restrictions: list[list[int]]) -> bool:
    """True when the traversed link sequence contains a banned sub-sequence."""
    if not restrictions:
        return False
    for seq in restrictions:
        n = len(seq)
        if n < 2 or n > len(link_path):
            continue
        for i in range(len(link_path) - n + 1):
            if link_path[i:i + n] == seq:
                return True
    return False


def _summarise(snapshot_id: str, arc_ids: list[int]) -> tuple[float, float | None,
                                                              list[int]]:
    """Total distance, total time (None if any arc lacks one), and link path."""
    if not arc_ids:
        return 0.0, 0.0, []
    rows = db.query(
        "SELECT arc_id, link_id, cost_distance_m, cost_time_s FROM arcs "
        "WHERE snapshot_id=%s AND arc_id = ANY(%s)",
        (snapshot_id, arc_ids),
    )
    by_id = {r["arc_id"]: r for r in rows}
    distance = 0.0
    time_s: float | None = 0.0
    links: list[int] = []
    for a in arc_ids:
        r = by_id.get(a)
        if r is None:
            continue
        distance += r["cost_distance_m"]
        if time_s is not None:
            if r["cost_time_s"] is None:
                time_s = None
            else:
                time_s += r["cost_time_s"]
        if not links or links[-1] != r["link_id"]:
            links.append(r["link_id"])
    return distance, time_s, links


def route(
    snapshot_id: str,
    source_node: int,
    target_node: int,
    *,
    metric: Metric = "distance",
    profile: Profile = "car",
    excluded_arcs: Sequence[int] = (),
    statement_timeout_ms: int = 20_000,
) -> RouteResult:
    """Shortest path from `source_node` to `target_node` with `excluded_arcs` closed."""
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        _mode_column(profile)
    except ValueError as exc:
        return RouteResult("UNSUPPORTED_PROFILE", None, None, None,
                           runtime_ms=elapsed(), detail=str(exc))

    if source_node == target_node:
        return RouteResult("OK", 0.0, 0.0, 0.0, runtime_ms=elapsed(),
                           detail="source and target are the same node")

    # Cheap definitive negative: different weak components can never connect.
    if not _same_component(snapshot_id, source_node, target_node):
        return RouteResult(
            "DISCONNECTED", None, None, None, runtime_ms=elapsed(),
            detail="endpoints lie in different weakly connected components",
        )

    plain = _run_dijkstra(snapshot_id, source_node, target_node, metric, profile,
                          excluded_arcs, statement_timeout_ms)
    if plain.status != "OK":
        plain.runtime_ms = elapsed()
        return plain

    restrictions = _restrictions(snapshot_id, profile)
    if restrictions:
        _, _, link_path = _summarise(snapshot_id, plain.arc_ids)
        if _violates(link_path, restrictions):
            # Rare. Re-run on the expanded graph, where the banned movement
            # simply has no edge.
            exact = _run_expanded(snapshot_id, source_node, target_node, metric,
                                  profile, excluded_arcs, statement_timeout_ms)
            exact.runtime_ms = elapsed()
            exact.used_expanded_graph = True
            return exact

    plain.runtime_ms = elapsed()
    return plain


def _run_dijkstra(snapshot_id: str, u: int, v: int, metric: Metric,
                  profile: Profile, excluded: Sequence[int],
                  timeout_ms: int) -> RouteResult:
    sql = _edge_sql(snapshot_id, profile, metric, excluded)
    try:
        with db.connection() as conn:
            # statement_timeout cannot take a bind parameter, and SET LOCAL only
            # applies inside a transaction - the pool runs autocommit, so an
            # explicit transaction block is what makes the timeout take effect.
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(timeout_ms),),
                    )
                    cur.execute(
                        "SELECT edge, cost FROM pgr_dijkstra(%s, %s::bigint, "
                        "%s::bigint, directed => true) ORDER BY seq",
                        (sql, u, v),
                    )
                    rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        # A timeout is NOT a finding about the network.
        if "statement timeout" in str(exc).lower() or "canceling" in str(exc).lower():
            return RouteResult("UNRESOLVED_TIMEOUT", None, None, None,
                               detail=f"statement timeout after {timeout_ms} ms")
        return RouteResult("API_ERROR", None, None, None, detail=str(exc))

    arc_ids = [int(r["edge"]) for r in rows if r["edge"] is not None and r["edge"] != -1]
    if not arc_ids:
        return RouteResult(
            "DISCONNECTED", None, None, None,
            detail="search space exhausted with no route to target",
        )
    distance, time_s, _ = _summarise(snapshot_id, arc_ids)
    cost = time_s if metric == "time" else distance
    return RouteResult("OK", cost, distance, time_s, arc_ids=arc_ids)


def route_many(
    snapshot_id: str,
    sources: Sequence[int],
    targets: Sequence[int],
    *,
    metric: Metric = "distance",
    profile: Profile = "car",
    excluded_arcs: Sequence[int] = (),
    statement_timeout_ms: int = 20_000,
) -> dict[tuple[int, int], float]:
    """Shortest-path cost for every (source, target) pair, in ONE call.

    Each pgr_dijkstra invocation reloads the whole edge set - measured at 39.9 ms
    for the 69,948-arc pilot, which is the dominant cost of any operation that
    would otherwise loop over pairs. pgRouting accepts array endpoints, so the
    corridor search pays that load once instead of once per probe.

    Returns agg_cost keyed by (start_vid, end_vid); pairs with no path are absent.
    """
    if not sources or not targets:
        return {}
    sql = _edge_sql(snapshot_id, profile, metric, excluded_arcs)
    try:
        with db.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(statement_timeout_ms),),
                    )
                    cur.execute(
                        "SELECT start_vid, end_vid, max(agg_cost) AS cost "
                        "FROM pgr_dijkstra(%s, %s::bigint[], %s::bigint[], "
                        "directed => true) GROUP BY start_vid, end_vid",
                        (sql, list(sources), list(targets)),
                    )
                    rows = cur.fetchall()
    except Exception:  # noqa: BLE001 - caller falls back to per-pair routing
        return {}
    return {(int(r["start_vid"]), int(r["end_vid"])): float(r["cost"]) for r in rows}


def _run_expanded(snapshot_id: str, u: int, v: int, metric: Metric,
                  profile: Profile, excluded: Sequence[int],
                  timeout_ms: int) -> RouteResult:
    """Route over the edge-expanded graph, where a prohibited turn has no edge."""
    mode = _mode_column(profile)
    cost_col = _cost_column(metric)
    excl = list(excluded) or [-1]

    start_arcs = db.query(
        f"SELECT arc_id, {cost_col} AS cost FROM arcs WHERE snapshot_id=%s "
        f"AND source=%s AND {mode} AND {cost_col} IS NOT NULL "
        f"AND arc_id <> ALL(%s)",
        (snapshot_id, u, excl),
    )
    end_arcs = db.query(
        f"SELECT arc_id FROM arcs WHERE snapshot_id=%s AND target=%s AND {mode} "
        f"AND {cost_col} IS NOT NULL AND arc_id <> ALL(%s)",
        (snapshot_id, v, excl),
    )
    if not start_arcs or not end_arcs:
        return RouteResult("DISCONNECTED", None, None, None,
                           detail="no usable arc leaves the source or enters the target")

    start_cost = {int(r["arc_id"]): float(r["cost"]) for r in start_arcs}
    sql = _transition_sql(snapshot_id, profile, metric, excluded)
    try:
        with db.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        (str(timeout_ms),),
                    )
                    cur.execute(
                        "SELECT start_vid, end_vid, node, edge, agg_cost "
                        "FROM pgr_dijkstra(%s, %s::bigint[], %s::bigint[], "
                        "directed => true) ORDER BY start_vid, end_vid, seq",
                        (sql, list(start_cost), [int(r['arc_id']) for r in end_arcs]),
                    )
                    rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        if "statement timeout" in str(exc).lower() or "canceling" in str(exc).lower():
            return RouteResult("UNRESOLVED_TIMEOUT", None, None, None,
                               detail=f"statement timeout after {timeout_ms} ms")
        return RouteResult("API_ERROR", None, None, None, detail=str(exc))

    # Group into paths and pick the cheapest, adding the cost of the first arc
    # (the expanded graph charges an edge for arriving AT an arc, so the
    # starting arc's own cost is never counted).
    paths: dict[tuple[int, int], list[int]] = {}
    totals: dict[tuple[int, int], float] = {}
    for r in rows:
        key = (int(r["start_vid"]), int(r["end_vid"]))
        node = int(r["node"])
        paths.setdefault(key, []).append(node)
        totals[key] = float(r["agg_cost"])

    best_key = None
    best_cost = float("inf")
    for key, total in totals.items():
        total += start_cost.get(key[0], 0.0)
        if total < best_cost:
            best_cost, best_key = total, key
    if best_key is None:
        return RouteResult("DISCONNECTED", None, None, None,
                           detail="no permitted manoeuvre sequence reaches the target")

    arc_ids = paths[best_key]
    distance, time_s, _ = _summarise(snapshot_id, arc_ids)
    cost = time_s if metric == "time" else distance
    return RouteResult("OK", cost, distance, time_s, arc_ids=arc_ids)
