"""Replacement paths: what a movement has to do instead.

Phase 6 established which trips the closure interrupts. This computes, for each
of them, the cheapest way to make the same trip with the closure removed.

THE CANONICAL ANSWER
--------------------
Minimum represented-network DISTANCE, between the same two outside nodes the
intact movement joined. Distance and not time, because AMDS publishes no speed
attribute: every time figure in this system is derived from an estimate, and a
headline number should not rest on one. Time is still reported - measured ALONG
the distance-minimal path, and flagged as such, so nobody reads it as a
time-minimal route.

"Represented" is doing real work in that sentence. The answer is the shortest
path in the graph this system has built from AMDS. It is not the shortest path
on the ground. Roads AMDS does not carry, private accesses, and unbuilt links
are all invisible to it, and a number described as "the detour" would claim
otherwise.

ONE LOAD, NOT ONE PER PAIR
--------------------------
Every pgr_dijkstra call reloads the whole edge set - 39.9 ms on the 69,948-arc
pilot, and the dominant cost of anything that loops. So all movements for one
closure are routed in a single multi-source/multi-target call. A closure with
six entry ports and six exit ports costs one edge-set load, not thirty-six.

THE TIMEOUT CONTRACT
--------------------
A search that did not finish is UNRESOLVED. It is never DISCONNECTED, and it is
never a finding about a road. This is a stop-condition contract, and it is
enforced structurally rather than by convention: `route_many_paths` returns a
search STATUS alongside its paths, and this module refuses to read an absent
pair as "no route" unless that status is OK. The older `routing.route_many`
returns a bare dict that cannot express the difference, which is how V1's
corridor search turns a statement timeout into `Corridor("DISCONNECTED", ...)`.
Nothing here uses it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

from . import db, routegeom
from .movements import Movement, MovementSet
from .routegeom import RouteGeometry
from .routing import Metric, Profile, Status, route_many_paths

#: Bump when the replacement RULE changes: a different canonical metric, a
#: different endpoint pair, a different exclusion set.
REPLACEMENT_MODEL_VERSION = "1.0.0"

#: Statuses that mean the search did not conclude. Kept as one frozen set so
#: no call site can invent a locally different idea of "unresolved".
UNRESOLVED_STATUSES = frozenset({
    "UNRESOLVED_TIMEOUT", "API_ERROR", "INVALID_GRAPH", "SOURCE_DATA_ERROR",
    "UNSUPPORTED_PROFILE",
})


@dataclass
class ReplacementPath:
    """One movement's replacement, or the reason there is not one."""

    movement_id: str
    entry_port_id: str
    exit_port_id: str
    from_node: int
    to_node: int

    #: OK | DISCONNECTED | UNRESOLVED_TIMEOUT | API_ERROR | ...
    status: Status
    detail: str = ""

    intact_distance_m: float | None = None
    intact_time_s: float | None = None
    replacement_distance_m: float | None = None
    replacement_time_s: float | None = None

    #: What the network now costs this trip that it did not before:
    #: replacement minus intact. The number a reader means by "the detour".
    network_penalty_m: float | None = None
    #: How much further the whole replacement trip is than the closed segment
    #: alone. Kept separate from the penalty because they answer different
    #: questions and V1 reported only this one, so the shadow comparison needs
    #: both to compare like with like.
    added_vs_segment_m: float | None = None
    #: replacement / intact. Dimensionless, so map-projection scale distortion
    #: cancels out of it.
    ratio: float | None = None
    added_time_s: float | None = None

    arc_ids: list[int] = field(default_factory=list)
    link_ids: list[int] = field(default_factory=list)
    geometry: RouteGeometry | None = None

    #: TRUE would be a stop condition: a replacement path that uses the road it
    #: is replacing. Carried on every row so the check is visible in output,
    #: not only in a test.
    traverses_own_closure: bool = False

    topology_confidence: str = "high"
    quality_flags: list[str] = field(default_factory=list)
    runtime_ms: int = 0

    @property
    def resolved(self) -> bool:
        return self.status not in UNRESOLVED_STATUSES


@dataclass
class ReplacementSet:
    snapshot_id: str
    closure_fingerprint: str
    selected_link_id: int
    profile: Profile
    metric: Metric

    paths: list[ReplacementPath] = field(default_factory=list)

    #: The SEARCH status. Not a finding about any movement.
    status: Status = "OK"
    detail: str = ""

    #: Milliseconds by stage, so a performance claim can be attributed.
    stage_ms: dict[str, int] = field(default_factory=dict)
    runtime_ms: int = 0
    model_version: str = REPLACEMENT_MODEL_VERSION
    algorithm: str = "pgr_dijkstra multi-source/multi-target"

    @property
    def resolved(self) -> bool:
        return self.status not in UNRESOLVED_STATUSES

    @property
    def best(self) -> ReplacementPath | None:
        """Cheapest resolved replacement, ties broken on movement id.

        Never on row order and never on which pair the planner returned first.
        """
        ok = [p for p in self.paths if p.status == "OK"
              and p.replacement_distance_m is not None]
        if not ok:
            return None
        return min(ok, key=lambda p: (p.replacement_distance_m, p.movement_id))


def compute(
    movement_set: MovementSet,
    removed_arc_ids: Sequence[int],
    declared_arc_ids: Sequence[int],
    selected_segment_length_m: float,
    *,
    profile: Profile = "car",
    statement_timeout_ms: int = 20_000,
    with_geometry: bool = False,
    topology_confidence: str = "high",
) -> ReplacementSet:
    """Replacement paths for every INCLUDED movement, from one edge-set load.

    `declared_arc_ids` is the closure as the request declared it. It is checked
    against `removed_arc_ids` rather than assumed equal, because "a closure
    removes an arc outside the declared closure" is a stop condition and an
    assertion inside the engine is cheaper than finding it in a shadow sample.
    """
    t0 = time.perf_counter()
    snap = movement_set.snapshot_id
    removed = frozenset(int(a) for a in removed_arc_ids)
    declared = frozenset(int(a) for a in declared_arc_ids)

    out = ReplacementSet(
        snapshot_id=snap, closure_fingerprint=movement_set.closure_fingerprint,
        selected_link_id=movement_set.selected_link_id, profile=profile,
        metric="distance")

    undeclared = sorted(removed - declared)
    if undeclared:
        out.status = "INVALID_GRAPH"
        out.detail = (
            f"the closure removes {len(undeclared)} arc(s) that the request did "
            f"not declare: {undeclared[:10]}. Refusing to route rather than "
            "report a replacement for a closure nobody asked for.")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    included = movement_set.included

    # An unresolved movement search cannot be rescued by a resolved routing
    # one: nothing is known about which pairs were movements, so nothing can be
    # known about their replacements.
    if not movement_set.resolved:
        out.status = movement_set.status  # type: ignore[assignment]
        out.detail = ("the intact movement search did not resolve, so no "
                      "replacement can be attributed to a movement")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    if not included:
        out.detail = ("no intact through movement was identified, so there is "
                      "nothing to find a replacement for")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    # The SAME two nodes the intact crossing joined. Routing between the ports'
    # outside nodes instead would compare a constrained intact leg against an
    # unconstrained replacement one - see the header of `movements.py` for the
    # square block where that produced a detour of minus 200 metres.
    sources = sorted({m.from_node for m in included})
    targets = sorted({m.to_node for m in included})

    t_route = time.perf_counter()
    routed = route_many_paths(
        snap, sources, targets, metric="distance", profile=profile,
        excluded_arcs=sorted(removed), statement_timeout_ms=statement_timeout_ms)
    out.stage_ms["replacement_route"] = int((time.perf_counter() - t_route) * 1000)

    if not routed.resolved:
        # THE CONTRACT. Every movement is unresolved; not one becomes
        # DISCONNECTED. A reader is told the search stopped, not that a road
        # lost its access.
        out.status = routed.status
        out.detail = routed.detail or "the replacement search did not resolve"
        out.paths = [
            _unresolved(m, routed.status, out.detail, selected_segment_length_m,
                        topology_confidence)
            for m in included
        ]
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    t_meta = time.perf_counter()
    arc_meta = _arc_meta(snap, {a for arcs in routed.paths.values() for a in arcs})
    out.stage_ms["arc_metadata"] = int((time.perf_counter() - t_meta) * 1000)

    geometry_ms = 0
    paths: list[ReplacementPath] = []
    for m in included:
        p, gms = _one(snap, m, routed, removed, arc_meta,
                      selected_segment_length_m, with_geometry,
                      topology_confidence)
        geometry_ms += gms
        paths.append(p)

    if with_geometry:
        out.stage_ms["geometry"] = geometry_ms

    # Intrinsic order. Sorting by cost would make the list depend on ties the
    # planner broke, which is the bug class PR 1 found in a BFS.
    paths.sort(key=lambda p: (p.entry_port_id, p.exit_port_id))
    out.paths = paths

    # --- ONE broken path poisons the WHOLE request ------------------------
    # A route that traverses the closure it is replacing means the exclusion
    # did not take. That is a failure of the routing contract, not of one pair:
    # every other path in this set came out of the same edge query under the
    # same exclusion, so none of them can be trusted either.
    #
    # Marking only the offending path INVALID_GRAPH and leaving the set OK let
    # a different movement become the principal result and carry an ordinary
    # headline - on a request where the engine had just caught itself routing
    # through a closed road.
    broken = [p for p in paths
              if p.traverses_own_closure or p.status == "INVALID_GRAPH"]
    if broken:
        out.status = "INVALID_GRAPH"
        out.detail = (
            f"{len(broken)} of {len(paths)} replacement path(s) traverse the "
            "closure they are replacing, so the routing exclusion did not take. "
            "Every path in this set came from the same query under the same "
            "exclusion, so no figure from it is reported.")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        out.stage_ms["total"] = out.runtime_ms
        return out

    ok = sum(1 for p in paths if p.status == "OK")
    disc = sum(1 for p in paths if p.status == "DISCONNECTED")
    out.detail = (f"{ok} of {len(paths)} movement(s) have a represented "
                  f"replacement path; {disc} have none")
    out.runtime_ms = int((time.perf_counter() - t0) * 1000)
    out.stage_ms["total"] = out.runtime_ms
    return out


# ------------------------------------------------------------------ internals
def _one(snap: str, m: Movement, routed, removed: frozenset[int],
         arc_meta: dict[int, dict], segment_length_m: float,
         with_geometry: bool, confidence: str) -> tuple[ReplacementPath, int]:
    t0 = time.perf_counter()
    p = ReplacementPath(
        movement_id=m.movement_id, entry_port_id=m.entry_port_id,
        exit_port_id=m.exit_port_id, from_node=m.from_node,
        to_node=m.to_node, status="OK",
        intact_distance_m=m.intact_distance_m, intact_time_s=m.intact_time_s,
        topology_confidence=confidence)

    if m.from_node == m.to_node:
        # The trip now begins and ends in the same place. That is a real answer
        # and a degenerate one, and it is said rather than smoothed over: a
        # penalty computed from it would be a large negative number.
        p.replacement_distance_m = 0.0
        p.replacement_time_s = 0.0
        p.quality_flags.append("DEGENERATE_ENDPOINTS")
        p.detail = ("the entry and exit crossings are the same node, so the "
                    "replacement trip has zero length; no penalty is derived")
        return p, 0

    key = (m.from_node, m.to_node)
    if key not in routed.costs:
        # Safe ONLY because routed.resolved was checked before this loop.
        p.status = "DISCONNECTED"
        p.detail = ("with the closure removed there is no represented route "
                    "between these two boundary crossings")
        p.quality_flags.append("NO_REPRESENTED_REPLACEMENT")
        return p, 0

    arcs = list(routed.paths.get(key, []))
    self_traversal = sorted(a for a in arcs if a in removed)
    if self_traversal:
        # A stop condition, surfaced rather than swallowed. It should be
        # impossible - the arcs were excluded from the edge query - so if it
        # ever fires, the exclusion did not take and no number here is safe.
        p.traverses_own_closure = True
        p.status = "INVALID_GRAPH"
        p.detail = (f"the replacement path traverses {len(self_traversal)} arc(s) "
                    f"of its own closure ({self_traversal[:10]}): the exclusion "
                    "did not take, so no replacement figure is reported")
        p.quality_flags.append("ROUTE_TRAVERSES_CLOSURE")
        return p, 0

    distance, time_s, links = _summarise(arcs, arc_meta)
    p.replacement_distance_m = distance
    p.replacement_time_s = time_s
    p.arc_ids = arcs
    p.link_ids = links

    if m.intact_distance_m is not None:
        p.network_penalty_m = distance - m.intact_distance_m
        p.ratio = (distance / m.intact_distance_m) if m.intact_distance_m else None
    p.added_vs_segment_m = distance - segment_length_m
    if time_s is not None and m.intact_time_s is not None:
        p.added_time_s = time_s - m.intact_time_s

    # Time is measured along the DISTANCE-minimal path. Said out loud: a reader
    # who sees a time must not take it for a time-minimal route.
    p.quality_flags.append("TIME_ALONG_DISTANCE_MINIMAL_PATH")
    if time_s is None:
        p.quality_flags.append("TIME_UNAVAILABLE")
    if p.network_penalty_m is not None and p.network_penalty_m < 0:
        # Should not arise now that both legs are measured between the same two
        # nodes: the intact leg is the cheapest crossing and the replacement is
        # the cheapest crossing from a strictly smaller edge set, so it cannot
        # be cheaper. Flagged rather than assumed away.
        p.quality_flags.append("REPLACEMENT_SHORTER_THAN_INTACT")
    elif p.network_penalty_m is not None and p.network_penalty_m == 0:
        # An equal-cost route exists that does not use the closure, so the
        # closure was not NECESSARY for this crossing - the cheapest intact
        # path merely happened to use it.
        #
        # This is where the movement test has a genuine tie ambiguity: whether
        # `movements.identify` includes such a pair depends on which of several
        # equal-cost paths the router returned. Rather than pretend otherwise,
        # the tie is detected here, where both costs are known, and said out
        # loud. The penalty is zero either way, so nothing a reader acts on
        # turns on it.
        p.quality_flags.append("CLOSURE_NOT_NECESSARY_EQUAL_COST_ALTERNATIVE")
    if m.confidence != "high":
        p.quality_flags.append(f"MOVEMENT_CONFIDENCE_{m.confidence.upper()}")

    gms = 0
    if with_geometry:
        tg = time.perf_counter()
        p.geometry = routegeom.assemble(snap, arcs)
        gms = int((time.perf_counter() - tg) * 1000)
        p.quality_flags.extend(p.geometry.quality_flags)

    p.runtime_ms = int((time.perf_counter() - t0) * 1000)
    return p, gms


def _unresolved(m: Movement, status: Status, detail: str,
                segment_length_m: float, confidence: str) -> ReplacementPath:
    return ReplacementPath(
        movement_id=m.movement_id, entry_port_id=m.entry_port_id,
        exit_port_id=m.exit_port_id, from_node=m.from_node, to_node=m.to_node,
        status=status, detail=detail,
        intact_distance_m=m.intact_distance_m, intact_time_s=m.intact_time_s,
        topology_confidence=confidence,
        quality_flags=["SEARCH_UNRESOLVED"])


def _arc_meta(snapshot_id: str, arc_ids) -> dict[int, dict]:
    ids = sorted(int(a) for a in arc_ids)
    if not ids:
        return {}
    rows = db.query(
        "SELECT arc_id, link_id, cost_distance_m, cost_time_s FROM arcs "
        " WHERE snapshot_id=%s AND arc_id = ANY(%s)", (snapshot_id, ids))
    return {int(r["arc_id"]): r for r in rows}


def _summarise(arc_ids: Sequence[int], meta: dict[int, dict]
               ) -> tuple[float, float | None, list[int]]:
    distance = 0.0
    time_s: float | None = 0.0
    links: list[int] = []
    for a in arc_ids:
        r = meta.get(int(a))
        if r is None:
            continue
        distance += float(r["cost_distance_m"])
        if time_s is not None:
            if r["cost_time_s"] is None:
                time_s = None
            else:
                time_s += float(r["cost_time_s"])
        lid = int(r["link_id"])
        if not links or links[-1] != lid:
            links.append(lid)
    return distance, time_s, links


# --------------------------------------------------------------- API shape
def _round(v, dp: int):
    return None if v is None else round(v, dp)


def path_dict(p: ReplacementPath) -> dict:
    d = {
        "movementId": p.movement_id,
        "entryPortId": p.entry_port_id,
        "exitPortId": p.exit_port_id,
        "fromNode": p.from_node,
        "toNode": p.to_node,
        "status": p.status,
        "resolved": p.resolved,
        "detail": p.detail,
        "intactDistanceM": _round(p.intact_distance_m, 1),
        "intactTimeS": _round(p.intact_time_s, 1),
        "replacementDistanceM": _round(p.replacement_distance_m, 1),
        "replacementTimeS": _round(p.replacement_time_s, 1),
        "networkPenaltyM": _round(p.network_penalty_m, 1),
        "addedVsSegmentM": _round(p.added_vs_segment_m, 1),
        "ratio": _round(p.ratio, 3),
        "addedTimeS": _round(p.added_time_s, 1),
        "arcIds": p.arc_ids,
        "linkIds": p.link_ids,
        "traversesOwnClosure": p.traverses_own_closure,
        "topologyConfidence": p.topology_confidence,
        "qualityFlags": p.quality_flags,
        "runtimeMs": p.runtime_ms,
    }
    if p.geometry is not None:
        d["route"] = routegeom.as_dict(p.geometry)
    return d


def as_dict(s: ReplacementSet) -> dict:
    best = s.best
    return {
        "replacementModelVersion": s.model_version,
        "algorithm": s.algorithm,
        "canonicalAnswer": "minimum represented-network distance",
        "status": s.status,
        "resolved": s.resolved,
        "detail": s.detail,
        "vehicleProfile": s.profile,
        "pathCount": len(s.paths),
        "resolvedCount": sum(1 for p in s.paths if p.status == "OK"),
        "disconnectedCount": sum(1 for p in s.paths if p.status == "DISCONNECTED"),
        "unresolvedCount": sum(1 for p in s.paths if not p.resolved),
        "bestMovementId": best.movement_id if best else None,
        "paths": [path_dict(p) for p in s.paths],
        "stageMs": s.stage_ms,
        "runtimeMs": s.runtime_ms,
    }
