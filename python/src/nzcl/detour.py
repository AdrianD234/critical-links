"""Detour / replacement-path metrics for a single closure.

Definitions (docs/METRIC_DEFINITIONS.md restates these for non-developers):

  alternative_distance_m   = shortest u->v path length after every arc in the
                             closure group is removed
  added_distance_vs_link_m = alternative_distance_m - selected_link_length_m
  normal_shortest_path_m   = shortest u->v distance on the INTACT graph
  network_penalty_m        = alternative_distance_m - normal_shortest_path_m
  detour_ratio_vs_link     = alternative_distance_m / selected_link_length_m

`network_penalty_m` is the more rigorous comparison because it does not assume
the closed link was itself the normal shortest way between its own endpoints.
On a divided carriageway or a slip lane it frequently is not.

Two further measures are reported ALONGSIDE, never instead:

  corridor  - a through-trip comparison, used where the endpoint measure is
              undefined. On a one-way carriageway the downstream endpoint is an
              internal node of the one-way system, so no u->v path exists for
              reasons unrelated to criticality. Measured on the pilot: 82% of
              state-highway links returned DISCONNECTED under the endpoint
              measure alone.
  isolation - what is stranded when nothing gets past, so that a cut-off
              driveway is distinguishable from a cut-off settlement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db
from .config import ALGORITHM, ALGORITHM_VERSION
from .routing import Metric, Profile, RouteResult, route, route_many

ClosureScope = Literal["physical", "directed"]
Direction = Literal["forward", "reverse"]

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}


@dataclass
class Corridor:
    status: str
    entry_node: int
    exit_node: int
    hops_upstream: int
    hops_downstream: int
    normal_distance_m: float | None
    alternative_distance_m: float | None
    penalty_m: float | None
    normal_time_s: float | None
    alternative_time_s: float | None
    penalty_time_s: float | None
    truncated: bool
    exit_reachable: bool
    detail: str | None


@dataclass
class Isolation:
    side: str
    pocket_node_count: int
    pocket_link_count: int
    pocket_length_m: float
    bounded: bool
    exact: bool


@dataclass
class DirectionResult:
    direction: Direction
    status: str
    source_node: int
    target_node: int
    selected_link_length_m: float
    normal_path_distance_m: float | None = None
    alternative_distance_m: float | None = None
    added_distance_vs_link_m: float | None = None
    network_penalty_m: float | None = None
    detour_ratio_vs_link: float | None = None
    normal_path_time_s: float | None = None
    alternative_time_s: float | None = None
    added_time_s: float | None = None
    removed_arc_ids: list[int] = field(default_factory=list)
    route_arc_ids: list[int] = field(default_factory=list)
    route_link_ids: list[int] = field(default_factory=list)
    corridor: Corridor | None = None
    isolation: Isolation | None = None
    quality_flags: list[str] = field(default_factory=list)
    error_detail: str | None = None
    runtime_ms: int = 0
    used_expanded_graph: bool = False


@dataclass
class DetourResult:
    snapshot_id: str
    link_id: int
    amds_id: str
    closure_group_id: str
    vehicle_profile: Profile
    metric: Metric
    closure_scope: ClosureScope
    removed_arc_ids: list[int]
    removed_link_ids: list[int]
    removed_amds_ids: list[str]
    forward: DirectionResult | None
    reverse: DirectionResult | None
    algorithm: str = ALGORITHM
    algorithm_version: str = ALGORITHM_VERSION
    calculated_at_utc: str = ""


def _link(snapshot_id: str, link_id: int) -> dict:
    row = db.query_one(
        "SELECT * FROM links WHERE snapshot_id=%s AND link_id=%s",
        (snapshot_id, link_id),
    )
    if row is None:
        raise KeyError(f"unknown link {link_id} in snapshot {snapshot_id}")
    return row


def _snapshot(snapshot_id: str) -> dict:
    row = db.query_one(
        "SELECT * FROM network_snapshots WHERE snapshot_id=%s", (snapshot_id,)
    )
    if row is None:
        raise KeyError(f"unknown snapshot {snapshot_id}")
    return row


def compute(
    snapshot_id: str,
    link_id: int,
    *,
    metric: Metric = "distance",
    profile: Profile = "car",
    closure_scope: ClosureScope = "physical",
    directions: Sequence[Direction] | None = None,
    compute_corridor: bool = True,
    statement_timeout_ms: int = 20_000,
) -> DetourResult:
    link = _link(snapshot_id, link_id)
    snap = _snapshot(snapshot_id)
    clipped = snap["extent_2193"] is not None

    group_arcs = [
        r["arc_id"] for r in db.query(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND closure_group_id=%s",
            (snapshot_id, link["closure_group_id"]),
        )
    ]

    def excluded_for(direction: Direction) -> list[int]:
        if closure_scope == "physical":
            return group_arcs
        rows = db.query(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND link_id=%s "
            "AND direction=%s",
            (snapshot_id, link_id, direction),
        )
        return [r["arc_id"] for r in rows]

    wanted: list[Direction] = list(directions) if directions else [
        d for d, ok in (("forward", link["forward_allowed"]),
                        ("reverse", link["reverse_allowed"])) if ok
    ]

    results: dict[str, DirectionResult] = {}
    for direction in wanted:
        results[direction] = _run_direction(
            snapshot_id, link, direction, metric, profile, closure_scope,
            excluded_for(direction), clipped, compute_corridor,
            statement_timeout_ms,
        )

    removed = sorted({a for r in results.values() for a in r.removed_arc_ids})
    removed_links = sorted({
        r["link_id"] for r in db.query(
            "SELECT DISTINCT link_id FROM arcs WHERE snapshot_id=%s "
            "AND arc_id = ANY(%s)", (snapshot_id, removed or [-1]))
    }) if removed else []
    removed_amds = [
        r["amds_id"] for r in db.query(
            "SELECT amds_id FROM links WHERE snapshot_id=%s AND link_id = ANY(%s) "
            "ORDER BY link_id", (snapshot_id, removed_links or [-1]))
    ] if removed_links else []

    from datetime import datetime, timezone
    return DetourResult(
        snapshot_id=snapshot_id,
        link_id=link_id,
        amds_id=link["amds_id"],
        closure_group_id=link["closure_group_id"],
        vehicle_profile=profile,
        metric=metric,
        closure_scope=closure_scope,
        removed_arc_ids=removed,
        removed_link_ids=removed_links,
        removed_amds_ids=removed_amds,
        forward=results.get("forward"),
        reverse=results.get("reverse"),
        calculated_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def _run_direction(snapshot_id, link, direction, metric, profile, closure_scope,
                   removed, clipped, want_corridor,
                   timeout_ms) -> DirectionResult:
    t0 = time.perf_counter()
    u = link["source_node"] if direction == "forward" else link["target_node"]
    v = link["target_node"] if direction == "forward" else link["source_node"]
    flags: list[str] = []

    def elapsed() -> int:
        return int((time.perf_counter() - t0) * 1000)

    if link["source_node"] == link["target_node"]:
        return DirectionResult(
            direction=direction, status="SOURCE_DATA_ERROR", source_node=u,
            target_node=v, selected_link_length_m=link["length_m"],
            removed_arc_ids=removed, quality_flags=["SELF_LOOP"],
            error_detail="link starts and ends at the same node",
            runtime_ms=elapsed(),
        )

    normal = route(snapshot_id, u, v, metric=metric, profile=profile,
                   statement_timeout_ms=timeout_ms)
    alt = route(snapshot_id, u, v, metric=metric, profile=profile,
                excluded_arcs=removed, statement_timeout_ms=timeout_ms)

    # A timeout must never be reported as "no detour exists".
    if alt.status not in ("OK", "DISCONNECTED"):
        return DirectionResult(
            direction=direction, status=alt.status, source_node=u, target_node=v,
            selected_link_length_m=link["length_m"], removed_arc_ids=removed,
            quality_flags=flags, error_detail=alt.detail, runtime_ms=elapsed(),
        )

    if link["speed_source"] != "nslr":
        flags.append("SPEED_ESTIMATED")
        if metric == "time":
            flags.append("TIME_ESTIMATED")
    if clipped:
        flags.append("CLIPPED_EXTRACT")

    if alt.status == "DISCONNECTED":
        if clipped:
            flags.append("DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT")

        isolation = _isolation(snapshot_id, u, v, removed, profile)
        if isolation.exact:
            if isolation.pocket_link_count <= 3:
                flags.append("ISOLATES_CUL_DE_SAC")
            elif isolation.pocket_link_count >= 100:
                flags.append("ISOLATES_SIGNIFICANT_AREA")

        corridor = None
        if want_corridor:
            corridor = _corridor(snapshot_id, u, v, removed, metric, profile,
                                 link["length_m"], timeout_ms)
            if corridor and corridor.status == "OK":
                flags.append("ENDPOINT_MEASURE_UNDEFINED_CORRIDOR_USED")
            if corridor and corridor.status != "OK" and not corridor.exit_reachable:
                flags.append("SOLE_ACCESS")

        return DirectionResult(
            direction=direction, status="DISCONNECTED", source_node=u,
            target_node=v, selected_link_length_m=link["length_m"],
            normal_path_distance_m=normal.distance_m if normal.status == "OK" else None,
            normal_path_time_s=normal.time_s if normal.status == "OK" else None,
            removed_arc_ids=removed, corridor=corridor, isolation=isolation,
            quality_flags=flags, error_detail=alt.detail, runtime_ms=elapsed(),
        )

    # Did the replacement route lean on buffer links outside the analysis area?
    link_ids = [
        r["link_id"] for r in db.query(
            "SELECT DISTINCT link_id FROM arcs WHERE snapshot_id=%s "
            "AND arc_id = ANY(%s)", (snapshot_id, alt.arc_ids))
    ]
    outside = db.query_one(
        "SELECT count(*) AS n FROM links WHERE snapshot_id=%s "
        "AND link_id = ANY(%s) AND NOT in_analysis_area",
        (snapshot_id, link_ids or [-1]),
    )
    if outside and outside["n"]:
        flags.append("ROUTE_USES_BUFFER")

    alt_dist = alt.distance_m or 0.0
    norm_dist = normal.distance_m if normal.status == "OK" else None
    length = link["length_m"]

    return DirectionResult(
        direction=direction, status="OK", source_node=u, target_node=v,
        selected_link_length_m=length,
        normal_path_distance_m=norm_dist,
        alternative_distance_m=alt_dist,
        added_distance_vs_link_m=alt_dist - length,
        network_penalty_m=None if norm_dist is None else alt_dist - norm_dist,
        detour_ratio_vs_link=(alt_dist / length) if length > 0 else None,
        normal_path_time_s=normal.time_s if normal.status == "OK" else None,
        alternative_time_s=alt.time_s,
        added_time_s=(alt.time_s - normal.time_s)
        if alt.time_s is not None and normal.status == "OK"
        and normal.time_s is not None else None,
        removed_arc_ids=removed,
        route_arc_ids=alt.arc_ids,
        route_link_ids=link_ids,
        quality_flags=flags,
        runtime_ms=elapsed(),
        used_expanded_graph=alt.used_expanded_graph,
    )


# --------------------------------------------------------------- isolation
_ISOLATION_SQL = """
WITH RECURSIVE reach(node_id) AS (
    SELECT %(start)s::bigint
  UNION
    SELECT CASE WHEN %(backward)s THEN a.source ELSE a.target END
    FROM reach r
    JOIN arcs a
      ON a.snapshot_id = %(snap)s
     AND (CASE WHEN %(backward)s THEN a.target ELSE a.source END) = r.node_id
     AND a.{mode}
     AND a.arc_id <> ALL (%(excluded)s)
)
SELECT node_id FROM reach LIMIT %(limit)s
"""


def _walk(snapshot_id: str, start: int, excluded: Sequence[int], profile: Profile,
          backward: bool, max_nodes: int) -> tuple[set[int], bool]:
    """Bounded reachability. `complete` is False when the bound was hit."""
    mode = _MODE_COLUMN[profile]
    rows = db.query(
        _ISOLATION_SQL.format(mode=mode),
        {"start": start, "backward": backward, "snap": snapshot_id,
         "excluded": list(excluded) or [-1], "limit": max_nodes + 1},
    )
    nodes = {r["node_id"] for r in rows}
    return nodes, len(nodes) <= max_nodes


def _isolation(snapshot_id: str, u: int, v: int, excluded: Sequence[int],
               profile: Profile, max_nodes: int = 5_000) -> Isolation:
    """Which side of the closure is stranded, and how big is it?

    It depends on the direction under test, and getting this wrong makes the
    measure meaningless. Closing the mouth of a cul-de-sac strands the far end
    in BOTH directions - but in the reverse direction the far end is the
    ORIGIN, so measuring what can reach the destination would return the whole
    network. Both sides are probed and the one that terminates within the bound
    is the pocket.
    """
    down, down_ok = _walk(snapshot_id, v, excluded, profile, True, max_nodes)
    up, up_ok = _walk(snapshot_id, u, excluded, profile, False, max_nodes)

    candidates: list[tuple[str, set[int]]] = []
    if down_ok:
        candidates.append(("downstream", down))
    if up_ok:
        candidates.append(("upstream", up))
    if not candidates:
        return Isolation("none", 0, 0, 0.0, bounded=True, exact=False)

    side, nodes = min(candidates, key=lambda c: len(c[1]))
    stats = db.query_one(
        f"""
        SELECT count(DISTINCT a.link_id) AS links,
               coalesce(sum(DISTINCT_len), 0) AS length_m
        FROM (
          SELECT DISTINCT a.link_id, l.length_m AS DISTINCT_len
          FROM arcs a
          JOIN links l ON l.snapshot_id = a.snapshot_id AND l.link_id = a.link_id
          WHERE a.snapshot_id = %s AND a.source = ANY(%s) AND a.target = ANY(%s)
            AND a.arc_id <> ALL (%s)
        ) a
        """,
        (snapshot_id, list(nodes), list(nodes), list(excluded) or [-1]),
    )
    return Isolation(
        side=side,
        pocket_node_count=len(nodes),
        pocket_link_count=int(stats["links"]) if stats else 0,
        pocket_length_m=float(stats["length_m"]) if stats else 0.0,
        bounded=False,
        exact=True,
    )


# ---------------------------------------------------------------- corridor
def _corridor(snapshot_id: str, u: int, v: int, excluded: Sequence[int],
              metric: Metric, profile: Profile, link_length_m: float,
              timeout_ms: int, max_hops: int = 40,
              max_walk_m: float = 25_000.0) -> Corridor:
    """Through-trip replacement path.

    Walks outward from the closure along the corridor, taking the straightest
    continuation where there is a choice, and expands until a replacement path
    exists between the two ends. On a divided highway that is what finds the
    nearest crossover: stopping at the first junction is not enough, because a
    node can offer a choice of where to go and still no way back onto the
    opposing carriageway.
    """
    mode = _MODE_COLUMN[profile]
    excl = list(excluded) or [-1]

    exit_reachable = bool(db.query_one(
        f"SELECT 1 AS ok FROM arcs WHERE snapshot_id=%s AND target=%s AND {mode} "
        f"AND arc_id <> ALL(%s) LIMIT 1", (snapshot_id, v, excl)))

    up = _walk_corridor(snapshot_id, u, excl, mode, upstream=True,
                        max_hops=max_hops, max_dist=max_walk_m)
    down = _walk_corridor(snapshot_id, v, excl, mode, upstream=False,
                          max_hops=max_hops, max_dist=max_walk_m)

    # Probe hop distances geometrically rather than one at a time: trying every
    # hop on a long divided-highway corridor costs a shortest-path call each.
    max_k = max(len(up), len(down))
    probes: list[int] = []
    k, step = 0, 1
    while k < max_k:
        probes.append(k)
        step = max(1, int(step * 1.6))
        k += step
    if probes and probes[-1] != max_k - 1:
        probes.append(max_k - 1)

    # One multi-target pgRouting call decides which probe distance works,
    # instead of one call per probe. Each call reloads the whole edge set
    # (39.9 ms on the pilot), so this is the difference between roughly one
    # edge-set load and ten.
    candidates = [
        (k, up[min(k, len(up) - 1)], down[min(k, len(down) - 1)]) for k in probes
    ]
    candidates = [(k, e, x) for k, e, x in candidates if e[0] != x[0]]
    if not candidates:
        return Corridor("DISCONNECTED", up[-1][0], down[-1][0], len(up) - 1,
                        len(down) - 1, None, None, None, None, None, None,
                        True, exit_reachable,
                        "corridor endpoints collapse to the same node")

    costs = route_many(
        snapshot_id,
        sorted({e[0] for _, e, _ in candidates}),
        sorted({x[0] for _, _, x in candidates}),
        metric=metric, profile=profile, excluded_arcs=excluded,
        statement_timeout_ms=timeout_ms,
    )

    last_detail = "no corridor endpoints could be reached"
    for k, entry, exit_ in candidates:
        if (entry[0], exit_[0]) not in costs:
            last_detail = "search space exhausted with no route to target"
            continue
        # Re-run the winning pair only, to recover the full path metrics.
        alt = route(snapshot_id, entry[0], exit_[0], metric=metric,
                    profile=profile, excluded_arcs=excluded,
                    statement_timeout_ms=timeout_ms)
        if alt.status in ("UNRESOLVED_TIMEOUT", "INVALID_GRAPH"):
            return Corridor(alt.status, entry[0], exit_[0], min(k, len(up) - 1),
                            min(k, len(down) - 1), None, None, None, None, None,
                            None, False, exit_reachable, alt.detail)
        if alt.status != "OK":
            last_detail = alt.detail or last_detail
            continue
        normal = route(snapshot_id, entry[0], exit_[0], metric=metric,
                       profile=profile, statement_timeout_ms=timeout_ms)
        nd = normal.distance_m if normal.status == "OK" else None
        return Corridor(
            "OK", entry[0], exit_[0], min(k, len(up) - 1), min(k, len(down) - 1),
            nd, alt.distance_m,
            None if nd is None else (alt.distance_m or 0) - nd,
            normal.time_s if normal.status == "OK" else None,
            alt.time_s,
            (alt.time_s - normal.time_s)
            if alt.time_s is not None and normal.status == "OK"
            and normal.time_s is not None else None,
            False, exit_reachable, None,
        )

    return Corridor("DISCONNECTED", up[-1][0], down[-1][0], len(up) - 1,
                    len(down) - 1, None, None, None, None, None, None,
                    True, exit_reachable, last_detail)


def _walk_corridor(snapshot_id: str, start: int, excluded: Sequence[int],
                   mode: str, *, upstream: bool, max_hops: int,
                   max_dist: float) -> list[tuple[int, float]]:
    """Sequence of (node, cumulative distance) walking away from `start`."""
    steps = [(start, 0.0)]
    seen = {start}
    cur, dist = start, 0.0
    heading: tuple[float, float] | None = None

    for _ in range(max_hops):
        join = "a.target = %s" if upstream else "a.source = %s"
        next_col = "a.source" if upstream else "a.target"
        rows = db.query(
            f"""
            SELECT a.arc_id, {next_col} AS next_node, a.cost_distance_m,
                   ST_X(ns.geom_2193) AS sx, ST_Y(ns.geom_2193) AS sy,
                   ST_X(nt.geom_2193) AS tx, ST_Y(nt.geom_2193) AS ty
            FROM arcs a
            JOIN nodes ns ON ns.snapshot_id=a.snapshot_id AND ns.node_id=a.source
            JOIN nodes nt ON nt.snapshot_id=a.snapshot_id AND nt.node_id=a.target
            WHERE a.snapshot_id=%s AND {join} AND a.{mode}
              AND a.arc_id <> ALL(%s)
            """,
            (snapshot_id, cur, list(excluded) or [-1]),
        )
        candidates = [r for r in rows if r["next_node"] not in seen]
        if not candidates:
            break

        best = candidates[0]
        if heading is not None and len(candidates) > 1:
            def score(r: dict) -> float:
                dx, dy = r["tx"] - r["sx"], r["ty"] - r["sy"]
                m = (dx * dx + dy * dy) ** 0.5 or 1.0
                hx, hy = dx / m, dy / m
                s = hx * heading[0] + hy * heading[1]
                return -s if upstream else s
            best = max(candidates, key=score)

        dist += best["cost_distance_m"] or 0.0
        if dist > max_dist:
            break
        dx, dy = best["tx"] - best["sx"], best["ty"] - best["sy"]
        m = (dx * dx + dy * dy) ** 0.5 or 1.0
        heading = (dx / m, dy / m)
        cur = best["next_node"]
        seen.add(cur)
        steps.append((cur, dist))

    return steps
