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
    "TURN_RESTRICTION_UNSUPPORTED",
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


class UnknownArc(LookupError):
    """An arc in a returned path is in neither `arcs` nor the overlay.

    This is a programming error, never a fact about the network, and it is
    raised rather than skipped because skipping it is silent and wrong: the
    arc's length simply vanishes from the total, and the caller reports a
    detour shorter than the one it computed.
    """


@dataclass(frozen=True)
class VirtualArc:
    """One directed piece of a real arc, existing only for this request.

    A partial closure needs to cut a link somewhere other than its ends, and
    the national snapshot must not be edited to express that. So the pieces
    live here: negative ids, assembled per request, unioned into the edge query
    and discarded when it returns.
    """

    arc_id: int
    source: int
    target: int
    cost_distance_m: float
    #: None where the parent arc has no valid time cost. Such an arc is dropped
    #: from a time-metric search, exactly as `cost_time_s IS NOT NULL` drops
    #: its parent.
    cost_time_s: float | None
    #: The REAL link this piece belongs to. Turn restrictions are sequences of
    #: link ids, so a piece must answer to its parent or splitting a link would
    #: quietly unban a manoeuvre across it.
    link_id: int
    parent_arc_id: int
    #: The extent of this piece in the PARENT LINK's own 0..1 parameter, stated
    #: in the link's coordinate order regardless of which way the piece runs.
    #:
    #: Carried so a replacement path that traverses a piece can be DRAWN. A
    #: route is not fully reported by its length: a map that omits the two
    #: partial links at the ends of an outage shows a detour starting in mid
    #: air, and a reader cannot check a line that is not there.
    from_fraction: float = 0.0
    to_fraction: float = 1.0


@dataclass(frozen=True)
class VirtualOverlay:
    """Request-local edges, plus what the rest of the engine needs to read them.

    `component_anchor` exists because a virtual node is not in `nodes`, and the
    cheap "different weak components can never connect" pre-check reads
    `nodes`. Without an anchor that check finds no row, concludes the endpoints
    are unconnected, and returns DISCONNECTED - which this engine states as a
    finding about the road network. A handle placed mid-link would therefore
    report the road as severed whenever it was perfectly routable.

    The anchor is any real node on the virtual node's host link: a split point
    is joined to both of its host's endpoints by construction, so it is in
    their weak component whatever else is closed.
    """

    arcs: tuple[VirtualArc, ...] = ()
    #: virtual node id -> a real node id sharing its weak component.
    component_anchor: dict[int, int] = field(default_factory=dict)

    def by_id(self) -> dict[int, VirtualArc]:
        return {a.arc_id: a for a in self.arcs}

    def usable(self, metric: Metric) -> tuple[VirtualArc, ...]:
        """The pieces a search on `metric` may traverse."""
        if metric == "time":
            return tuple(a for a in self.arcs if a.cost_time_s is not None)
        return self.arcs

    def anchor(self, node: int) -> int:
        """The real node whose component `node` shares. Identity for real nodes."""
        return self.component_anchor.get(node, node)

    @property
    def empty(self) -> bool:
        return not self.arcs


#: Stands in for "no overlay", so callers and defaults never carry None.
NO_OVERLAY = VirtualOverlay()

#: pgRouting marks the final row of every path with `edge = -1`, and this
#: module filters that marker out before summing a route.
#:
#: A virtual arc issued the id -1 is therefore deleted from its own path, as
#: though it were the sentinel. It is not an error anywhere: the search
#: succeeds, the arc list comes back one leg short, and the total is quietly
#: too small. That is exactly how a 1100 m replacement path first measured
#: 1000 m here - the last 100 m of it was the arc numbered -1.
#:
#: So -1 is reserved and never issued to anything. `vsplit` starts its
#: numbering below it.
RESERVED_EDGE_SENTINEL = -1


def _mode_column(profile: Profile) -> str:
    col = _MODE_COLUMN.get(profile)
    if col is None:
        raise ValueError(f"unsupported vehicle profile {profile!r}")
    return col


def _cost_column(metric: Metric) -> str:
    return "cost_time_s" if metric == "time" else "cost_distance_m"


def _edge_sql(snapshot_id: str, profile: Profile, metric: Metric,
              excluded: Sequence[int],
              overlay: VirtualOverlay = NO_OVERLAY) -> str:
    """The edge query pgRouting consumes.

    Literals are inlined because pgRouting takes the inner query as text, so it
    cannot carry bind parameters. Every value interpolated here is either
    server-derived (snapshot id from the database) or an integer arc id; the
    snapshot id is escaped and arc ids are cast through int().

    An overlay is appended as a literal VALUES list rather than written to a
    table. That is what makes a partial closure request-local: two concurrent
    requests splitting the same link cannot see each other's pieces, because
    neither piece was ever stored.
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
    base = (
        f"SELECT arc_id AS id, source, target, {cost} AS cost, "
        f"-1::double precision AS reverse_cost FROM arcs "
        f"WHERE {' AND '.join(clauses)}"
    )

    extra = overlay.usable(metric)
    if not extra:
        return base
    return f"{base} UNION ALL {_values_sql(extra, metric)}"


def _values_sql(arcs: Sequence[VirtualArc], metric: Metric) -> str:
    """Overlay pieces as a VALUES list shaped like the edge query's columns."""
    rows = []
    for a in arcs:
        c = a.cost_time_s if metric == "time" else a.cost_distance_m
        c = float(c)
        if c != c or c in (float("inf"), float("-inf")) or c < 0:
            raise ValueError(
                f"virtual arc {a.arc_id} has a non-finite or negative cost {c!r}")
        rows.append(
            f"({int(a.arc_id)}::bigint,{int(a.source)}::bigint,"
            f"{int(a.target)}::bigint,{c!r}::double precision,"
            f"-1::double precision)"
        )
    return (f"SELECT * FROM (VALUES {','.join(rows)}) "
            f"AS v(id, source, target, cost, reverse_cost)")


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


def _same_component(snapshot_id: str, u: int, v: int,
                    overlay: VirtualOverlay = NO_OVERLAY) -> bool:
    """Cheap definitive negative, resolved through the overlay first.

    A virtual node has no row in `nodes`, so asking this question about one
    directly returns NULL - which reads as "not the same component" and turns
    into DISCONNECTED, a stated finding about the road network. Anchoring maps
    each virtual node onto a real node of its host link, whose component it
    provably shares.
    """
    u, v = overlay.anchor(u), overlay.anchor(v)
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


def _summarise(snapshot_id: str, arc_ids: list[int],
               overlay: VirtualOverlay = NO_OVERLAY) -> tuple[float, float | None,
                                                              list[int]]:
    """Total distance, total time (None if any arc lacks one), and link path.

    An arc that is in neither `arcs` nor the overlay RAISES. It used to be
    skipped, which is silent and produces a total shorter than the path the
    planner actually returned - a detour under-reported by however much road
    the unrecognised arcs carried, with nothing anywhere saying so. Real arc
    ids all come back from a query over `arcs`, so the only way to reach this
    is an overlay whose pieces were not passed through, and that must fail
    loudly the first time rather than quietly forever.

    Overlay pieces contribute their PARENT link id to the link path, because
    that path is what turn restrictions are matched against and a restriction
    names real links.
    """
    if not arc_ids:
        return 0.0, 0.0, []
    virtual = overlay.by_id()
    real_ids = [a for a in arc_ids if a not in virtual]
    rows = db.query(
        "SELECT arc_id, link_id, cost_distance_m, cost_time_s FROM arcs "
        "WHERE snapshot_id=%s AND arc_id = ANY(%s)",
        (snapshot_id, real_ids),
    ) if real_ids else []
    by_id = {r["arc_id"]: r for r in rows}

    distance = 0.0
    time_s: float | None = 0.0
    links: list[int] = []
    for a in arc_ids:
        piece = virtual.get(a)
        if piece is not None:
            d, t, link_id = piece.cost_distance_m, piece.cost_time_s, piece.link_id
        else:
            r = by_id.get(a)
            if r is None:
                raise UnknownArc(
                    f"arc {a} is in neither snapshot {snapshot_id!r} nor the "
                    f"virtual overlay; its length cannot be counted")
            d, t, link_id = r["cost_distance_m"], r["cost_time_s"], r["link_id"]

        distance += d
        if time_s is not None:
            time_s = None if t is None else time_s + t
        if not links or links[-1] != link_id:
            links.append(link_id)
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
    overlay: VirtualOverlay = NO_OVERLAY,
) -> RouteResult:
    """Shortest path from `source_node` to `target_node` with `excluded_arcs` closed.

    `overlay` adds request-local edges - the pieces a partial closure splits a
    link into. Either endpoint may be a virtual node from that overlay.
    """
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
    if not _same_component(snapshot_id, source_node, target_node, overlay):
        return RouteResult(
            "DISCONNECTED", None, None, None, runtime_ms=elapsed(),
            detail="endpoints lie in different weakly connected components",
        )

    plain = _run_dijkstra(snapshot_id, source_node, target_node, metric, profile,
                          excluded_arcs, statement_timeout_ms, overlay)
    if plain.status != "OK":
        plain.runtime_ms = elapsed()
        return plain

    restrictions = _restrictions(snapshot_id, profile)
    if restrictions:
        _, _, link_path = _summarise(snapshot_id, plain.arc_ids, overlay)
        if _violates(link_path, restrictions):
            if not overlay.empty:
                # The expanded graph is built from `arc_transitions`, which has
                # no rows for pieces invented this request, so the exact
                # re-route cannot see them. Returning the plain path would
                # report a route through a banned manoeuvre as though it were
                # permitted. This is a search that did not conclude, and it is
                # named as one - never as a finding about the road.
                return RouteResult(
                    "TURN_RESTRICTION_UNSUPPORTED", None, None, None,
                    runtime_ms=elapsed(),
                    detail=(
                        "the shortest path crosses a banned manoeuvre, and the "
                        "exact re-route is not available while a partial "
                        "closure is in force"),
                )
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
                  timeout_ms: int,
                  overlay: VirtualOverlay = NO_OVERLAY) -> RouteResult:
    sql = _edge_sql(snapshot_id, profile, metric, excluded, overlay)
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
    distance, time_s, _ = _summarise(snapshot_id, arc_ids, overlay)
    cost = time_s if metric == "time" else distance
    return RouteResult("OK", cost, distance, time_s, arc_ids=arc_ids)


@dataclass
class ManyCostResult:
    """Costs for every requested pair, and the status of the SEARCH.

    `status` is about the search, never about a pair. `costs` holds only the
    pairs that were reached, so an absent pair means "no path" if and only if
    the search resolved.

    That distinction is the entire reason this is not a bare mapping. It used
    to be one, returned empty on any exception, and a caller therefore could
    not tell a cancelled statement from a graph with no route. V1's corridor
    search read the empty mapping as "no route" and reported DISCONNECTED - a
    finding about the road network - for a query the database had killed.
    Recorded, with the timings and the screenshots, in docs/audits/v1-timeout/.
    """

    status: Status
    costs: dict[tuple[int, int], float] = field(default_factory=dict)
    detail: str | None = None

    @property
    def resolved(self) -> bool:
        """True when an absent pair may be read as 'no route'."""
        return self.status == "OK"


def route_many(
    snapshot_id: str,
    sources: Sequence[int],
    targets: Sequence[int],
    *,
    metric: Metric = "distance",
    profile: Profile = "car",
    excluded_arcs: Sequence[int] = (),
    statement_timeout_ms: int = 20_000,
) -> ManyCostResult:
    """Shortest-path cost for every (source, target) pair, in ONE call.

    Each pgr_dijkstra invocation reloads the whole edge set - measured at 39.9 ms
    for the 69,948-arc pilot, which is the dominant cost of any operation that
    would otherwise loop over pairs. pgRouting accepts array endpoints, so the
    corridor search pays that load once instead of once per probe.

    `costs` is agg_cost keyed by (start_vid, end_vid); pairs with no path are
    absent. Read it only when `resolved` - see `ManyCostResult`.
    """
    if not sources or not targets:
        return ManyCostResult("OK", detail="no source or no target was requested")
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
    except Exception as exc:  # noqa: BLE001
        # The same reading `_run_dijkstra` above has always applied to the
        # single-pair search: a timeout is NOT a finding about the network.
        if "statement timeout" in str(exc).lower() or "canceling" in str(exc).lower():
            return ManyCostResult(
                "UNRESOLVED_TIMEOUT",
                detail=f"statement timeout after {statement_timeout_ms} ms")
        return ManyCostResult("API_ERROR", detail=str(exc))
    return ManyCostResult("OK", costs={
        (int(r["start_vid"]), int(r["end_vid"])): float(r["cost"]) for r in rows
    })


@dataclass
class ManyRouteResult:
    """Every requested pair's path, from ONE edge-set load.

    `status` is about the SEARCH, not about any pair. `paths` holds only the
    pairs that were reached; a pair absent from `paths` means "no path" if and
    only if `status == "OK"`.

    The status-beside-the-data shape exists because a bare mapping cannot tell
    a cancelled statement from a graph with no route, and a caller that reads
    an absent pair as "no route" turns a timeout into DISCONNECTED. V1's
    corridor search shipped that exact defect on `route_many`, which then
    returned a bare dict; the V1 timeout hotfix (docs/audits/v1-timeout/,
    merged from main) gave `route_many` its own `ManyCostResult` above. The
    two types stay separate because they answer different questions -
    `ManyCostResult` carries costs for ranking, this carries the ORDERED ARC
    PATHS the movement model interrogates - and V1 and V2 must be able to
    change shape independently while V1 is frozen.
    """

    status: Status
    paths: dict[tuple[int, int], list[int]] = field(default_factory=dict)
    costs: dict[tuple[int, int], float] = field(default_factory=dict)
    runtime_ms: int = 0
    detail: str | None = None

    @property
    def resolved(self) -> bool:
        """True when an absent pair may be read as 'no route'."""
        return self.status == "OK"


def route_many_paths(
    snapshot_id: str,
    sources: Sequence[int],
    targets: Sequence[int],
    *,
    metric: Metric = "distance",
    profile: Profile = "car",
    excluded_arcs: Sequence[int] = (),
    statement_timeout_ms: int = 20_000,
) -> ManyRouteResult:
    """Full arc paths for every (source, target) pair, in ONE pgRouting call.

    `route_many` returns costs only, which is enough to rank candidates but not
    enough to ask what a route TRAVERSED - and "does this route use a removed
    arc" is the question the movement model is built on. So this keeps the
    edges as well as the aggregate.

    Ordering is `start_vid, end_vid, seq`, so the arc sequence of each pair is
    the path order and not the order the planner happened to emit rows in.
    """
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    if not sources or not targets:
        return ManyRouteResult("OK", runtime_ms=elapsed(),
                               detail="no source or no target was requested")

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
                        "SELECT start_vid, end_vid, edge, agg_cost "
                        "FROM pgr_dijkstra(%s, %s::bigint[], %s::bigint[], "
                        "directed => true) ORDER BY start_vid, end_vid, seq",
                        (sql, sorted(set(int(s) for s in sources)),
                         sorted(set(int(t) for t in targets))),
                    )
                    rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        if "statement timeout" in str(exc).lower() or "canceling" in str(exc).lower():
            return ManyRouteResult(
                "UNRESOLVED_TIMEOUT", runtime_ms=elapsed(),
                detail=f"statement timeout after {statement_timeout_ms} ms")
        return ManyRouteResult("API_ERROR", runtime_ms=elapsed(), detail=str(exc))

    paths: dict[tuple[int, int], list[int]] = {}
    costs: dict[tuple[int, int], float] = {}
    for r in rows:
        key = (int(r["start_vid"]), int(r["end_vid"]))
        edge = r["edge"]
        costs[key] = float(r["agg_cost"])
        if edge is not None and int(edge) != -1:
            paths.setdefault(key, []).append(int(edge))
    # A pair whose source IS its target produces a single row with edge = -1.
    # It has a zero-length path, which is a real answer, so it stays in `costs`
    # and legitimately has no arcs.
    for key in costs:
        paths.setdefault(key, [])
    return ManyRouteResult("OK", paths=paths, costs=costs, runtime_ms=elapsed())


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
