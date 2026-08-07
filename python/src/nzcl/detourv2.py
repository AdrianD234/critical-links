"""Closure-impact analysis, V2.

Three things are kept apart here that V1 ran together, and keeping them apart
is most of the point of this module.

  1. WHAT IS CLOSED is a named scope, resolved in `nzcl.closure`, defaulting to
     the exact segment the user selected.

  2. WHETHER A ROUTE EXISTS is a directed question, answered on `arcs` by
     pgRouting. It can fail for reasons that have nothing to do with a road
     losing access - most commonly a one-way carriageway whose downstream
     endpoint is an interior node of the one-way system.

  3. WHETHER ANYTHING IS PHYSICALLY CUT OFF is an undirected question, answered
     on Gu in `nzcl.physical`, exactly.

V1 let (2) produce the headline for (3). That is how a road with a 26.6 km
replacement path came to be reported as cutting off 13.64 km.

The wording gate
----------------
`Road cut off` is emitted if and only if the partition is exact, at least one
link that is not itself part of the closure ends up separated from its own
component's retained side, AND the choice of which side that is was decisive. A
closure that merely detaches itself has cut nothing off, and a split with no
anchor on either side has not established which half lost anything.

The complete vocabulary:

    Road cut off                    a decisive separation
    Network split into two          the graph split, but no side carries a
      represented components        decisive anchor, so naming one is not
                                    supported by the data
    Through route found             a replacement path exists
    No endpoint route               no directed path between the closed link's
                                    own endpoints, nothing separated
    Directional access loss         one direction routes and the other does not
    No isolation in the represented nothing separated. Says "the represented
      physical-access graph         graph" because Gu is inferred topology, and
                                    a claim about roads would be stronger than
                                    the data supports
    Partial analysis                some requested directions did not resolve
    Analysis unresolved             none resolved - never a finding about a road

There is no code path that produces any other headline, and none of these
strings is assembled at the call site.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal, Sequence

from . import closure as closure_mod
from . import db, physical
from .closure import Closure, Scope
from .physical import IsolationResult
from .routing import Metric, Profile, route

#: Deliberately not 3.0.0. Nothing in this PR is a stable V2.
ALGORITHM = "closure-impact-v2"
ALGORITHM_VERSION = "3.0.0-dev"

Direction = Literal["forward", "reverse"]

#: The complete set of headlines this engine may produce.
#:
#: "No physical isolation" became "No isolation in the represented
#: physical-access graph". The shorter phrase claims something about roads; the
#: longer one claims something about the graph, and only the second is true.
#: Gu is inferred topology and the difference is exactly where a reader would
#: be misled.
#:
#: "Network split into two represented components" is for the case where a
#: closure DOES separate the graph but no side carries a decisive anchor.
#: Naming one of them "cut off" would assert a direction the data does not
#: support.
#:
#: "Partial analysis" is for a request where some directions resolved and
#: others did not. Reporting only the successful half as though it were the
#: whole answer is how a timeout becomes a finding.
HEADLINES = (
    "Road cut off",
    "Network split into two represented components",
    "Through route found",
    "No endpoint route",
    "Directional access loss",
    "No isolation in the represented physical-access graph",
    "Partial analysis",
    "Analysis unresolved",
)

#: Statuses that mean the search did not reach a conclusion. Never a finding
#: about the road.
UNRESOLVED_STATUSES = frozenset({
    "UNRESOLVED_TIMEOUT", "API_ERROR", "INVALID_GRAPH", "SOURCE_DATA_ERROR",
    "UNSUPPORTED_PROFILE",
})


@dataclass
class DirectedAccess:
    """The directed question, kept strictly separate from isolation.

    Membership of a strongly connected component is what "can I drive there and
    back" means on `arcs`. A global SCC labelling of a 731,286-arc graph per
    request is not affordable and would not answer anything extra: the question
    is about ONE pair of nodes, and running the search both ways decides that
    pair's mutual reachability exactly. `same_scc_after_closure` is therefore a
    pairwise result, not a lookup into a precomputed SCC table, and is labelled
    as such rather than implying a partition that was never computed.
    """

    forward_status: str
    reverse_status: str
    forward_distance_m: float | None
    reverse_distance_m: float | None
    #: None when only one direction was computed - which is the normal case on
    #: a one-way link, where there is no reverse traversal to ask about.
    #: Reporting False there would assert a return path was tested and failed,
    #: when it was never tested at all.
    same_scc_after_closure: bool | None
    #: True when one direction routes and the other does not.
    asymmetric: bool
    detail: str


@dataclass
class DirectionResult:
    direction: Direction
    status: str
    source_node: int
    target_node: int
    selected_segment_length_m: float
    normal_path_distance_m: float | None = None
    alternative_distance_m: float | None = None
    network_penalty_m: float | None = None
    added_vs_segment_m: float | None = None
    detour_ratio_vs_segment: float | None = None
    normal_path_time_s: float | None = None
    alternative_time_s: float | None = None
    added_time_s: float | None = None
    route_arc_ids: list[int] = field(default_factory=list)
    headline: str = ""
    quality_flags: list[str] = field(default_factory=list)
    error_detail: str | None = None
    runtime_ms: int = 0


@dataclass
class AnalysisResult:
    snapshot_id: str
    link_id: int
    scope: Scope
    vehicle_profile: Profile
    metric: Metric
    closure: Closure
    isolation: IsolationResult
    directed_access: DirectedAccess
    forward: DirectionResult | None
    reverse: DirectionResult | None
    headline: str
    isolation_statement: str
    algorithm: str = ALGORITHM
    algorithm_version: str = ALGORITHM_VERSION
    derivation_version: str = physical.DERIVATION_VERSION
    runtime_ms: int = 0
    cached: bool = False
    calculated_at_utc: str = ""


# --------------------------------------------------------------------- run
def analyse(
    snapshot_id: str,
    link_id: int,
    *,
    scope: Scope = closure_mod.DEFAULT_SCOPE,
    direction: Literal["forward", "reverse", "both"] = "both",
    metric: Metric = "distance",
    profile: Profile = "car",
    statement_timeout_ms: int = 20_000,
    use_cache: bool = True,
) -> AnalysisResult:
    """Analyse one closure. Never mutates anything except the result cache."""
    t0 = time.perf_counter()

    scope_direction = direction if direction in ("forward", "reverse") else None
    if scope == "direction" and scope_direction is None:
        raise ValueError(
            "scope='direction' needs direction='forward' or 'reverse': "
            "a single directed traversal has to say which one")

    c = closure_mod.resolve(snapshot_id, link_id, scope=scope,
                            direction=scope_direction, profile=profile)

    if use_cache:
        # link_id is part of the lookup, not just of the payload. Every child
        # of one AMDS parent removes the same arcs and so shares
        # `c.fingerprint`; without the link id, selecting child #8 was served
        # child #12's segment, endpoints and metrics.
        hit = _cache_get(snapshot_id, c.fingerprint, link_id, metric, direction)
        if hit is not None:
            return hit

    link = db.query_one(
        "SELECT source_node, target_node, forward_allowed, reverse_allowed, "
        "       length_m, speed_source FROM links WHERE snapshot_id=%s AND link_id=%s",
        (snapshot_id, link_id))
    u0, v0 = int(link["source_node"]), int(link["target_node"])

    wanted: list[Direction] = (
        [direction] if direction in ("forward", "reverse")
        else [d for d, ok in (("forward", link["forward_allowed"]),
                              ("reverse", link["reverse_allowed"])) if ok]
    )

    results: dict[str, DirectionResult] = {}
    for d in wanted:
        results[d] = _run_direction(
            snapshot_id, link, d, metric, profile, c.removed_arc_ids,
            statement_timeout_ms)

    # --- (3) the undirected question, answered exactly ---------------------
    # For scope='direction' nothing is physically removed: one traversal is
    # withdrawn and the road is still there. Analysing Gu with an empty closure
    # is the correct statement of that, not an omission.
    physical_removed = [] if scope == "direction" else c.removed_link_ids
    iso = _isolation_cached(snapshot_id, profile, physical_removed,
                            c.closure_nodes, use_cache)

    # --- (2) the directed question, kept separate --------------------------
    access = _directed_access(results, u0, v0)

    headline, iso_statement = _classify(results, iso, access)
    for d in results.values():
        d.headline = _direction_headline(d, iso, access)

    from datetime import datetime, timezone
    out = AnalysisResult(
        snapshot_id=snapshot_id, link_id=link_id, scope=scope,
        vehicle_profile=profile, metric=metric, closure=c, isolation=iso,
        directed_access=access,
        forward=results.get("forward"), reverse=results.get("reverse"),
        headline=headline, isolation_statement=iso_statement,
        runtime_ms=int((time.perf_counter() - t0) * 1000),
        calculated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    if use_cache:
        _cache_put(out, metric, direction)
    return out


def _run_direction(snapshot_id, link, direction, metric, profile, removed_arcs,
                   timeout_ms) -> DirectionResult:
    t0 = time.perf_counter()
    u = int(link["source_node"]) if direction == "forward" else int(link["target_node"])
    v = int(link["target_node"]) if direction == "forward" else int(link["source_node"])
    length = float(link["length_m"])
    flags: list[str] = []

    def elapsed() -> int:
        return int((time.perf_counter() - t0) * 1000)

    if u == v:
        return DirectionResult(
            direction=direction, status="SOURCE_DATA_ERROR", source_node=u,
            target_node=v, selected_segment_length_m=length,
            quality_flags=["SELF_LOOP"], runtime_ms=elapsed(),
            error_detail="link starts and ends at the same node")

    normal = route(snapshot_id, u, v, metric=metric, profile=profile,
                   statement_timeout_ms=timeout_ms)
    alt = route(snapshot_id, u, v, metric=metric, profile=profile,
                excluded_arcs=removed_arcs, statement_timeout_ms=timeout_ms)

    if link["speed_source"] != "nslr":
        flags.append("SPEED_ESTIMATED")
        if metric == "time":
            flags.append("TIME_ESTIMATED")

    if alt.status not in ("OK", "DISCONNECTED"):
        return DirectionResult(
            direction=direction, status=alt.status, source_node=u, target_node=v,
            selected_segment_length_m=length, quality_flags=flags,
            error_detail=alt.detail, runtime_ms=elapsed())

    norm = normal.distance_m if normal.status == "OK" else None
    if alt.status == "DISCONNECTED":
        return DirectionResult(
            direction=direction, status="DISCONNECTED", source_node=u,
            target_node=v, selected_segment_length_m=length,
            normal_path_distance_m=norm,
            normal_path_time_s=normal.time_s if normal.status == "OK" else None,
            quality_flags=flags, error_detail=alt.detail, runtime_ms=elapsed())

    alt_d = alt.distance_m or 0.0
    return DirectionResult(
        direction=direction, status="OK", source_node=u, target_node=v,
        selected_segment_length_m=length,
        normal_path_distance_m=norm,
        alternative_distance_m=alt_d,
        network_penalty_m=None if norm is None else alt_d - norm,
        added_vs_segment_m=alt_d - length,
        detour_ratio_vs_segment=(alt_d / length) if length > 0 else None,
        normal_path_time_s=normal.time_s if normal.status == "OK" else None,
        alternative_time_s=alt.time_s,
        added_time_s=(alt.time_s - normal.time_s)
        if alt.time_s is not None and normal.status == "OK"
        and normal.time_s is not None else None,
        route_arc_ids=alt.arc_ids, quality_flags=flags, runtime_ms=elapsed())


def _directed_access(results: dict[str, DirectionResult], u: int, v: int
                     ) -> DirectedAccess:
    f = results.get("forward")
    r = results.get("reverse")
    fs = f.status if f else "NOT_REQUESTED"
    rs = r.status if r else "NOT_REQUESTED"

    # A one-way link has no reverse traversal, so only one direction is ever
    # computed for it. Treating the absent one as a failure would manufacture
    # an asymmetry out of nothing - and asymmetry is what decides whether the
    # headline says "Directional access loss".
    computed = [s for s in (fs, rs) if s != "NOT_REQUESTED"]
    # Only a search that REACHED A CONCLUSION says anything about reachability.
    # A timeout is not a "no". Previously two timed-out directions produced
    # same_scc=False, which asserts the endpoints were shown not to be mutually
    # reachable when in fact nothing was shown at all.
    conclusive = [s for s in computed if s in ("OK", "DISCONNECTED")]
    asym = len(conclusive) == 2 and set(conclusive) == {"OK", "DISCONNECTED"}
    same_scc = (all(s == "OK" for s in conclusive)
                if len(conclusive) == 2 else None)

    if asym:
        detail = ("one direction still routes between the segment's endpoints "
                  "and the other does not: a directional access loss, not a "
                  "road losing physical access")
    elif len(conclusive) < len(computed):
        detail = ("at least one direction did not resolve, so no conclusion "
                  "about mutual reachability was reached; the directions that "
                  "did resolve are reported beneath")
    elif conclusive and all(s == "DISCONNECTED" for s in conclusive):
        detail = (
            "no directed path remains between the segment's own endpoints"
            + (" (only one direction exists on this link)"
               if len(conclusive) == 1 else "")
            + "; whether anything is physically cut off is a separate question, "
              "answered on the undirected graph and reported separately")
    elif conclusive and all(s == "OK" for s in conclusive):
        detail = ("every direction that exists on this link still routes "
                  "between the segment's endpoints")
    else:
        detail = ("the directed search did not resolve; this is not a finding "
                  "about the road")
    return DirectedAccess(
        forward_status=fs, reverse_status=rs,
        forward_distance_m=f.alternative_distance_m if f else None,
        reverse_distance_m=r.alternative_distance_m if r else None,
        same_scc_after_closure=same_scc, asymmetric=asym, detail=detail)


def _classify(results: dict[str, DirectionResult], iso: IsolationResult,
              access: DirectedAccess) -> tuple[str, str]:
    """The wording gate. The only place a headline is chosen.

    Order matters:

      * an unresolved search is reported as unresolved and never as a finding.
        ANY unresolved requested direction has to be visible at the top level -
        previously the headline only said so when EVERY direction failed, so
        forward=OK with reverse timing out read as "Through route found" while
        half the requested analysis had not happened;
      * isolation outranks routing, because a cut-off road is the stronger
        statement;
      * "Road cut off" needs an exact partition AND a separated non-closure
        link AND an unambiguous principal side. Without the third it is only
        true that the network split, and that is what it says instead.
    """
    statuses = {d.status for d in results.values()}
    unresolved = statuses & UNRESOLVED_STATUSES
    resolved = statuses - UNRESOLVED_STATUSES

    if unresolved and not resolved:
        return "Analysis unresolved", "Analysis unresolved"

    separated = (iso.calculation_exact and iso.physically_isolates
                 and iso.separated_link_count > 0)
    cut_off = separated and not iso.principal_side_ambiguous

    if not iso.calculation_exact:
        iso_statement = "Analysis unresolved"
    elif cut_off:
        iso_statement = "Road cut off"
    elif separated:
        iso_statement = "Network split into two represented components"
    else:
        iso_statement = "No isolation in the represented physical-access graph"

    # A partially failed request is reported as partial whatever the surviving
    # direction found, EXCEPT where isolation alone already settles the answer:
    # isolation is computed on Gu and does not depend on either route search,
    # so a timeout cannot undermine it.
    if unresolved and not separated:
        return "Partial analysis", iso_statement

    if cut_off:
        return "Road cut off", iso_statement
    if separated:
        return "Network split into two represented components", iso_statement
    if "OK" in resolved and "DISCONNECTED" not in resolved:
        return "Through route found", iso_statement
    if access.asymmetric:
        return "Directional access loss", iso_statement
    if "DISCONNECTED" in resolved:
        return "No endpoint route", iso_statement
    return "Through route found", iso_statement


def _direction_headline(d: DirectionResult, iso: IsolationResult,
                        access: DirectedAccess) -> str:
    if d.status not in ("OK", "DISCONNECTED"):
        return "Analysis unresolved"
    if d.status == "OK":
        return "Through route found"
    if (iso.calculation_exact and iso.physically_isolates
            and iso.separated_link_count > 0):
        return ("Network split into two represented components"
                if iso.principal_side_ambiguous else "Road cut off")
    if access.asymmetric:
        return "Directional access loss"
    return "No endpoint route"


# ------------------------------------------------------------------- cache
# Two caches, because there are two kinds of result. See the header of
# sql/migrations/007_physical_access.sql for the failure that made the
# distinction necessary.
#: Fields of IsolationResult that depend on WHERE the closure is, not on what
#: it removes. They must never enter a cache keyed only on the removed links.
#:
#: Audited against every field of IsolationResult: the other sixteen are a pure
#: function of (graph, removed link set) and are correctly shared. These two
#: are computed from the closure's NODES.
_LOCATION_SPECIFIC_ISOLATION_FIELDS = (
    "topology_confidence", "topology_confidence_reason")


def _isolation_cached(snapshot_id: str, profile: Profile,
                      removed_link_ids: list[int], closure_nodes: list[int],
                      use_cache: bool) -> IsolationResult:
    """The closure-invariant half, shared by every sibling - correctly.

    Keyed on the removed LINK set, which is exactly what Gu had taken out of
    it. Two children of one AMDS parent under `source_feature` scope really do
    have the same PARTITION, and this is where that sharing belongs.

    Topology confidence does NOT belong here and used to ride along with it.
    It is computed from the closure's nodes, so it is a property of where the
    closure is, not of what it removes. Under `scope='direction'` nothing is
    removed from Gu at all, so every direction-scope closure in the country
    shares one fingerprint - and the first one to run wrote its confidence for
    all of them. A closure beside three unresolved near misses could serve
    `medium`; a clean one could serve `low` with a reason describing near
    misses hundreds of kilometres away.

    So the partition is cached and the confidence is recomputed on EVERY call,
    hit or miss, from the current closure's nodes. This is the second cache bug
    of the same shape: the key was right for the object it was designed for,
    and something location-specific got stored alongside it.
    """
    fp = closure_mod.isolation_fingerprint(
        snapshot_id, profile, physical.DERIVATION_VERSION, removed_link_ids)

    iso: IsolationResult | None = None
    ms = 0
    if use_cache:
        row = db.query_one(
            "SELECT result FROM closure_isolation_v2 "
            " WHERE snapshot_id=%s AND isolation_fingerprint=%s "
            "   AND derivation_version=%s",
            (snapshot_id, fp, physical.DERIVATION_VERSION))
        if row is not None:
            # A fresh object per call, built from JSON, so mutating it below
            # cannot reach another request.
            iso = _iso_from(row["result"])

    if iso is None:
        t0 = time.perf_counter()
        g = physical.get(snapshot_id, profile)
        iso = physical.analyse_closure(g, removed_link_ids)
        ms = int((time.perf_counter() - t0) * 1000)

        if use_cache:
            payload = asdict(iso)
            for f in _LOCATION_SPECIFIC_ISOLATION_FIELDS:
                payload.pop(f, None)
            db.execute(
                "INSERT INTO closure_isolation_v2 (snapshot_id, "
                "  isolation_fingerprint, vehicle_profile, derivation_version, "
                "  result, runtime_ms) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (snapshot_id, isolation_fingerprint, "
                "             derivation_version) "
                "DO UPDATE SET result = EXCLUDED.result, "
                "  runtime_ms = EXCLUDED.runtime_ms, computed_at_utc = now()",
                (snapshot_id, fp, profile, physical.DERIVATION_VERSION,
                 json.dumps(payload), ms))

    # ALWAYS, hit or miss, from THIS closure's nodes.
    conf, why = physical.topology_confidence(snapshot_id, closure_nodes)
    iso.topology_confidence = conf
    iso.topology_confidence_reason = why
    return iso


def _cache_get(snapshot_id: str, fp: str, link_id: int, metric: str,
               direction: str) -> AnalysisResult | None:
    row = db.query_one(
        "SELECT result FROM closure_analysis_v2 "
        " WHERE snapshot_id=%s AND closure_fingerprint=%s AND link_id=%s "
        "   AND algorithm_version=%s AND metric=%s AND direction=%s",
        (snapshot_id, fp, link_id, ALGORITHM_VERSION, metric, direction))
    if row is None:
        return None
    return _from_payload(row["result"])


def _cache_put(out: AnalysisResult, metric: str, direction: str) -> None:
    db.execute(
        "INSERT INTO closure_analysis_v2 (snapshot_id, closure_fingerprint, "
        "  link_id, closure_scope, direction, vehicle_profile, metric, "
        "  algorithm, algorithm_version, derivation_version, result, runtime_ms) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (snapshot_id, closure_fingerprint, algorithm_version, "
        "             metric, direction, link_id) "
        "DO UPDATE SET result = EXCLUDED.result, runtime_ms = EXCLUDED.runtime_ms, "
        "  computed_at_utc = now()",
        (out.snapshot_id, out.closure.fingerprint, out.link_id, out.scope,
         direction, out.vehicle_profile, metric, ALGORITHM, ALGORITHM_VERSION,
         out.derivation_version, json.dumps(to_payload(out)), out.runtime_ms))


def to_payload(out: AnalysisResult) -> dict:
    d = asdict(out)
    d["cached"] = False
    return d


def _from_payload(payload) -> AnalysisResult:
    d = dict(payload) if not isinstance(payload, str) else json.loads(payload)
    out = AnalysisResult(
        snapshot_id=d["snapshot_id"], link_id=d["link_id"], scope=d["scope"],
        vehicle_profile=d["vehicle_profile"], metric=d["metric"],
        closure=Closure(**d["closure"]),
        isolation=_iso_from(d["isolation"]),
        directed_access=DirectedAccess(**d["directed_access"]),
        forward=DirectionResult(**d["forward"]) if d.get("forward") else None,
        reverse=DirectionResult(**d["reverse"]) if d.get("reverse") else None,
        headline=d["headline"], isolation_statement=d["isolation_statement"],
        algorithm=d["algorithm"], algorithm_version=d["algorithm_version"],
        derivation_version=d["derivation_version"],
        runtime_ms=d["runtime_ms"], calculated_at_utc=d["calculated_at_utc"])
    out.cached = True
    return out


def _iso_from(d) -> IsolationResult:
    d = dict(d) if not isinstance(d, str) else json.loads(d)
    comps = [physical.ResultingComponent(**c) for c in d.get("components", [])]
    return IsolationResult(
        calculation_exact=d["calculation_exact"],
        physically_isolates=d["physically_isolates"],
        method=d["method"], components=comps,
        separated_link_ids=d.get("separated_link_ids", []),
        separated_link_count=d.get("separated_link_count", 0),
        separated_length_m=d.get("separated_length_m", 0.0),
        separated_truncated=d.get("separated_truncated", False),
        origin_component_ids=d.get("origin_component_ids", []),
        closure_is_bridge=d.get("closure_is_bridge", False),
        graph_exact=d.get("graph_exact", False),
        topology_confidence=d.get("topology_confidence",
                                  physical.TOPOLOGY_CONFIDENCE_CEILING),
        topology_confidence_reason=d.get("topology_confidence_reason", ""),
        partition_exact=d.get("partition_exact", True),
        principal_side_rule=d.get("principal_side_rule", ""),
        principal_side_confidence=d.get("principal_side_confidence", "high"),
        principal_side_ambiguous=d.get("principal_side_ambiguous", False),
        nodes_examined=d.get("nodes_examined", 0),
        edges_examined=d.get("edges_examined", 0),
        detail=d.get("detail", ""))


def invalidate_cache(snapshot_id: str) -> int:
    """Drop BOTH caches for a snapshot. Used by tests and by re-derivation."""
    with db.direct_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM closure_analysis_v2 WHERE snapshot_id=%s "
                        "AND algorithm_version=%s",
                        (snapshot_id, ALGORITHM_VERSION))
            n = cur.rowcount
            cur.execute("DELETE FROM closure_isolation_v2 WHERE snapshot_id=%s "
                        "AND derivation_version=%s",
                        (snapshot_id, physical.DERIVATION_VERSION))
            return n + cur.rowcount


# --------------------------------------------------------------- API shape
def _round(v, dp: int):
    return None if v is None else round(v, dp)


def direction_dict(d: DirectionResult | None) -> dict | None:
    if d is None:
        return None
    return {
        "direction": d.direction,
        "status": d.status,
        "headline": d.headline,
        "sourceNode": d.source_node,
        "targetNode": d.target_node,
        "selectedSegmentLengthM": _round(d.selected_segment_length_m, 1),
        "normalPathDistanceM": _round(d.normal_path_distance_m, 1),
        "alternativeDistanceM": _round(d.alternative_distance_m, 1),
        "networkPenaltyM": _round(d.network_penalty_m, 1),
        "addedVsSegmentM": _round(d.added_vs_segment_m, 1),
        "detourRatioVsSegment": _round(d.detour_ratio_vs_segment, 3),
        "normalPathTimeS": _round(d.normal_path_time_s, 1),
        "alternativeTimeS": _round(d.alternative_time_s, 1),
        "addedTimeS": _round(d.added_time_s, 1),
        "routeArcIds": d.route_arc_ids,
        "qualityFlags": d.quality_flags,
        "errorDetail": d.error_detail,
        "runtimeMs": d.runtime_ms,
    }


def isolation_dict(iso: IsolationResult, *, max_components: int = 50) -> dict:
    comps = iso.components[:max_components]
    return {
        # Two claims, never merged. `calculationExact` is about the algorithm;
        # `graphExact` is about whether Gu models the real road network, and it
        # is always false. A single `exact` could only be read as the second.
        "calculationExact": iso.calculation_exact,
        "graphExact": iso.graph_exact,
        "partitionExact": iso.partition_exact,
        "topologyConfidence": iso.topology_confidence,
        "topologyConfidenceReason": iso.topology_confidence_reason,
        # Which side is "cut off" is a policy, not a theorem. A bridge yields
        # two components and mathematics alone does not privilege either.
        "principalSideRule": iso.principal_side_rule,
        "principalSideConfidence": iso.principal_side_confidence,
        "principalSideAmbiguous": iso.principal_side_ambiguous,
        "physicallyIsolates": iso.physically_isolates,
        "method": iso.method,
        "closureIsBridge": iso.closure_is_bridge,
        "separatedLinkCount": iso.separated_link_count,
        "separatedLengthM": _round(iso.separated_length_m, 1),
        "separatedLinkIds": iso.separated_link_ids,
        "separatedTruncated": iso.separated_truncated,
        "componentCount": len(iso.components),
        "componentsTruncated": len(iso.components) > len(comps),
        # Diagnostics, so a complexity claim can be checked against a counter
        # rather than inferred from wall-clock time.
        "nodesExamined": iso.nodes_examined,
        "edgesExamined": iso.edges_examined,
        "components": [
            {
                # Which PRE-CLOSURE component this part came out of. Parts of
                # two already-disconnected components are not alternatives.
                "originComponentId": c.origin_component_id,
                "nodeCount": c.node_count,
                "linkCount": c.link_count,
                "roadLengthM": _round(c.road_length_m, 1),
                "stateHighwayLinkCount": c.state_highway_link_count,
                "retainsPrincipalConnection": c.retains_principal_connection,
                # The principal side is the rest of the country. Listing it
                # would be a quarter of a million ids that nothing draws.
                "linkIds": c.link_ids if not c.retains_principal_connection else [],
            }
            for c in comps
        ],
        "detail": iso.detail,
    }


def as_dict(out: AnalysisResult) -> dict:
    return {
        "snapshotId": out.snapshot_id,
        "linkId": out.link_id,
        "algorithm": out.algorithm,
        "algorithmVersion": out.algorithm_version,
        "derivationVersion": out.derivation_version,
        "engine": "v2",
        "stability": "development preview - not a stable 3.0.0",
        "request": {
            "scope": out.scope,
            "metric": out.metric,
            "vehicle": out.vehicle_profile,
        },
        "headline": out.headline,
        "isolationStatement": out.isolation_statement,
        "closure": closure_mod.as_dict(out.closure),
        "isolation": isolation_dict(out.isolation),
        "directedAccess": asdict(out.directed_access),
        "forward": direction_dict(out.forward),
        "reverse": direction_dict(out.reverse),
        "runtimeMs": out.runtime_ms,
        "cached": out.cached,
        "calculatedAtUtc": out.calculated_at_utc,
    }
