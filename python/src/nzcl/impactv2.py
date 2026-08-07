"""The boundary-movement analysis, assembled.

One request, in stages, each timed separately so a performance claim can be
attributed to the stage that earned it:

    closure resolution   nzcl.closure     what exactly is removed
    port generation      nzcl.ports       where the closure meets the open network
    intact movements     nzcl.movements   which trips actually went through here
    replacement paths    nzcl.replacement what each of those has to do instead
    corridor selection   nzcl.corridor    where a driver would divert and rejoin
    geometry             nzcl.routegeom   what can honestly be drawn
    isolation            nzcl.physical    what, if anything, is cut off

THREE ANSWERS, KEPT APART
-------------------------
The whole point of V2 is that these are different questions and V1 let one
produce the other's headline:

  * a REPLACEMENT is a routing result about a trip;
  * PHYSICAL ISOLATION is an undirected result about the graph;
  * DIRECTED ACCESS is about whether a particular traversal survives.

They are computed by different code on different graphs and reported in
different blocks. Nothing in this module merges them, and the isolation block
is taken from `detourv2`'s existing exact computation rather than recomputed,
so there is one implementation of the strongest claim the system makes.

THE PRINCIPAL MOVEMENT
----------------------
A closure usually interrupts more than one movement, and an interface has to
lead with one. It is chosen by impact - a movement with no replacement outranks
one with a long detour, which outranks a short one - and ties fall to the
movement's STABLE key, not to its id. On a symmetric two-way segment the two
directions are genuinely equivalent and something has to break the tie; what
matters is that the same one wins after a re-ingest renumbers the graph.

Every other movement is still returned. Leading with one is a presentation
decision, not a filter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from . import closure as closure_mod
from . import corridor as corridor_mod
from . import db, movements, physical, ports, replacement, routegeom
from .closure import Closure, Scope
from .movements import MovementSet
from .replacement import ReplacementPath, ReplacementSet
from .routing import Metric, Profile

#: Deliberately not 3.0.0, and deliberately distinct from `detourv2`'s. This is
#: a different measurement of the same closure, and a shared version string
#: would let a cached result of one be read as the other.
ALGORITHM = "closure-boundary-movement-v2"
ALGORITHM_VERSION = "3.1.0-dev"


@dataclass
class BoundaryImpact:
    snapshot_id: str
    link_id: int
    scope: Scope
    profile: Profile
    metric: Metric

    closure: Closure
    boundary: ports.ClosureBoundary
    movement_set: MovementSet
    replacements: ReplacementSet
    corridor: corridor_mod.CorridorResult | None = None

    principal: ReplacementPath | None = None
    principal_movement: movements.Movement | None = None

    #: Geometry, assembled only when asked for. Each is separately gapped.
    selected_geometry: routegeom.RouteGeometry | None = None
    closure_geometry: routegeom.RouteGeometry | None = None
    intact_geometry: routegeom.RouteGeometry | None = None
    replacement_geometry: routegeom.RouteGeometry | None = None

    isolation: physical.IsolationResult | None = None

    headline: str = ""
    stage_ms: dict[str, int] = field(default_factory=dict)
    runtime_ms: int = 0
    quality_flags: list[str] = field(default_factory=list)
    algorithm: str = ALGORITHM
    algorithm_version: str = ALGORITHM_VERSION


#: The complete set of headlines this module may produce. Assembled nowhere
#: else, so a reader can enumerate what the engine is capable of saying.
HEADLINES = (
    "Through movement has no represented replacement",
    "Through movement diverts",
    "No through movement identified",
    "Partial analysis",
    "Analysis unresolved",
)

#: Headlines that assert something about the ROAD. None of them may be emitted
#: when the candidate search was not exhaustive.
#:
#: Each reads as a statement about every movement the closure interrupts, and
#: an unevaluated pair could hold the worst detour, the only disconnected
#: movement, or the movement the reader actually cares about. The sample
#: recorded 10 truncated analyses that still carried one of these.
DEFINITIVE_HEADLINES = frozenset({
    "Through movement has no represented replacement",
    "Through movement diverts",
    "No through movement identified",
})


def analyse(
    snapshot_id: str,
    link_id: int,
    *,
    scope: Scope = closure_mod.DEFAULT_SCOPE,
    direction: Literal["forward", "reverse", None] = None,
    metric: Metric = "distance",
    profile: Profile = "car",
    with_geometry: bool = False,
    with_corridor: bool = True,
    with_isolation: bool = True,
    statement_timeout_ms: int = 20_000,
) -> BoundaryImpact:
    """Run every stage over one closure. Mutates nothing."""
    t0 = time.perf_counter()
    stage: dict[str, int] = {}

    def timed(name: str, fn):
        t = time.perf_counter()
        try:
            return fn()
        finally:
            stage[name] = int((time.perf_counter() - t) * 1000)

    c: Closure = timed("closure_resolution", lambda: closure_mod.resolve(
        snapshot_id, link_id, scope=scope, direction=direction, profile=profile))

    b = timed("port_generation", lambda: ports.derive(
        snapshot_id, c.removed_link_ids, link_id, c.fingerprint,
        profile=profile, shape=c.shape))

    ms = timed("intact_movements", lambda: movements.identify(
        b, c.removed_arc_ids, metric=metric, profile=profile,
        statement_timeout_ms=statement_timeout_ms))

    conf = "high"
    iso = None
    if with_isolation:
        def _iso():
            # scope='direction' withdraws a traversal; it removes no road, so
            # the undirected graph has nothing taken out of it. Analysing Gu
            # with an empty closure is the correct statement of that.
            removed = [] if scope == "direction" else c.removed_link_ids
            g = physical.get(snapshot_id, profile)
            r = physical.analyse_closure(g, removed)
            r.topology_confidence, r.topology_confidence_reason = \
                physical.topology_confidence(snapshot_id, c.closure_nodes)
            return r
        iso = timed("isolation", _iso)
        conf = iso.topology_confidence

    rs: ReplacementSet = timed("replacement_paths", lambda: replacement.compute(
        ms, c.removed_arc_ids, c.removed_arc_ids, c.selected_segment_length_m,
        profile=profile, statement_timeout_ms=statement_timeout_ms,
        with_geometry=False, topology_confidence=conf))

    out = BoundaryImpact(
        snapshot_id=snapshot_id, link_id=link_id, scope=scope, profile=profile,
        metric=metric, closure=c, boundary=b, movement_set=ms, replacements=rs,
        isolation=iso)

    by_id = {m.movement_id: m for m in ms.movements}
    out.principal = _principal(rs, by_id, _selected_road_name(snapshot_id, link_id),
                               {p.port_id: p for p in b.ports})
    if out.principal is not None:
        out.principal_movement = by_id.get(out.principal.movement_id)

    if with_corridor and out.principal_movement is not None:
        pm = out.principal_movement
        port_by_id = {p.port_id: p for p in b.ports}
        entry = port_by_id.get(pm.entry_port_id)
        exit_ = port_by_id.get(pm.exit_port_id)
        if entry is not None and exit_ is not None:
            # The principal movement's own intact arcs are handed in as the
            # WITNESS. Without them a candidate pair can have a perfectly good
            # post-closure route while the cheapest intact route between those
            # two nodes never used the closure - a diversion nobody needs.
            out.corridor = timed("corridor_expansion", lambda: corridor_mod.select(
                b, c.removed_link_ids, c.removed_arc_ids,
                entry_ports=[entry], exit_ports=[exit_], profile=profile,
                witness_arcs=pm.intact_arc_ids,
                statement_timeout_ms=statement_timeout_ms))

    if with_geometry:
        def _geom():
            # The selected segment and the closure are SETS of links, not
            # paths, so they are collected rather than assembled. Running them
            # through the route assembler made a fifteen-child source feature
            # report "fourteen gaps, the widest 406 m" - the distance between
            # links that were never adjacent - on 237 of the first 500 sampled
            # links. See the header of `routegeom.RouteGeometry`.
            out.selected_geometry = routegeom.collect(snapshot_id, [link_id])
            if c.removed_link_ids != [link_id]:
                out.closure_geometry = routegeom.collect(
                    snapshot_id, c.removed_link_ids)
            if out.principal_movement is not None:
                out.intact_geometry = routegeom.assemble(
                    snapshot_id, out.principal_movement.intact_arc_ids)
            if out.principal is not None and out.principal.arc_ids:
                out.replacement_geometry = routegeom.assemble(
                    snapshot_id, out.principal.arc_ids)
        timed("geometry_assembly", _geom)

    out.headline, out.quality_flags = _classify(ms, rs, out.principal,
                                                out.corridor)
    stage["total"] = int((time.perf_counter() - t0) * 1000)
    out.stage_ms = stage
    out.runtime_ms = stage["total"]
    return out


def _selected_road_name(snapshot_id: str, link_id: int) -> str:
    row = db.query_one(
        "SELECT coalesce(dn.display_name, l.road_name) AS name FROM links l "
        "  LEFT JOIN link_display_names dn "
        "    ON dn.snapshot_id = l.snapshot_id AND dn.link_id = l.link_id "
        " WHERE l.snapshot_id=%s AND l.link_id=%s", (snapshot_id, link_id))
    return ((row or {}).get("name") or "").strip().casefold()


def _principal(rs: ReplacementSet, by_id: dict[str, movements.Movement],
               selected_name: str, port_by_id: dict) -> ReplacementPath | None:
    """Worst impact first, then the movement along the road that was clicked.

    Ranked on status before distance because "there is no way round" is a
    stronger statement than "it is 8 km further", and an interface that led
    with the longer number would bury it.

    The road-name term matters more than it looks. Closing one segment of a
    through road produces several equally-penalised movements - straight
    through, and every turning movement that also crossed it - and they tie on
    distance exactly. Leading with a turning movement onto a side road, when
    the user clicked the through road, describes a real trip but not the one
    they asked about. Matching the selected segment's own name settles it on
    evidence rather than on a hash.

    Ties beyond that fall to the movement's STABLE key. On a symmetric two-way
    segment the two directions are genuinely equivalent, so something has to
    decide; what matters is that the same one wins after a re-ingest.
    """
    if not rs.paths:
        return None

    def rank(p: ReplacementPath):
        status_rank = {"DISCONNECTED": 0, "OK": 1}.get(p.status, 2)
        m = by_id.get(p.movement_id)
        name_matches = 0
        if selected_name:
            for pid in (p.entry_port_id, p.exit_port_id):
                port = port_by_id.get(pid)
                if port is not None and (port.road_name or "").strip().casefold() \
                        == selected_name:
                    name_matches += 1
        return (status_rank, -(p.network_penalty_m or 0.0), -name_matches,
                m.key if m else (p.entry_port_id, p.exit_port_id))

    return sorted(rs.paths, key=rank)[0]


def _classify(ms: MovementSet, rs: ReplacementSet,
              principal: ReplacementPath | None,
              corridor_result=None) -> tuple[str, list[str]]:
    """The only place a headline is chosen.

    Order, and why:

      1. an unresolved SEARCH is unresolved, never a finding about a road;
      2. an INVALID GRAPH poisons everything - a replacement that traversed
         its own closure means the exclusion did not take, so no figure from
         that request is safe, including the ones that look fine;
      3. a search that was TRUNCATED may report what it found but may not
         imply it found everything;
      4. only then may an ordinary headline be emitted.
    """
    flags: list[str] = []
    if ms.truncated or not ms.exhaustive:
        flags.append("MOVEMENT_CANDIDATES_TRUNCATED")
    if corridor_result is not None and corridor_result.truncated:
        flags.append("CORRIDOR_CANDIDATES_TRUNCATED")
    if corridor_result is not None and corridor_result.confidence == "low":
        flags.append("CORRIDOR_CONFIDENCE_LOW")

    if not ms.resolved:
        return "Analysis unresolved", flags + ["MOVEMENT_SEARCH_UNRESOLVED"]
    if rs.status == "INVALID_GRAPH":
        return "Analysis unresolved", flags + ["INVALID_GRAPH"]
    if not rs.resolved:
        return "Analysis unresolved", flags + ["REPLACEMENT_SEARCH_UNRESOLVED"]
    if principal is not None and not principal.resolved:
        return "Analysis unresolved", flags + ["PRINCIPAL_MOVEMENT_UNRESOLVED"]

    if principal is None:
        headline = "No through movement identified"
    elif principal.status == "DISCONNECTED":
        headline = "Through movement has no represented replacement"
    else:
        headline = "Through movement diverts"

    # A definitive sentence needs an exhaustive search behind it. The resolved
    # sub-results stay visible either way; what changes is that nothing claims
    # to have looked at everything.
    incomplete = ("MOVEMENT_CANDIDATES_TRUNCATED" in flags
                  or "CORRIDOR_CANDIDATES_TRUNCATED" in flags)
    if incomplete and headline in DEFINITIVE_HEADLINES:
        return "Partial analysis", flags + ["HEADLINE_WITHHELD_NOT_EXHAUSTIVE"]
    return headline, flags


# --------------------------------------------------------------- API shape
def as_dict(out: BoundaryImpact, *, include_all_movements: bool = True) -> dict:
    from . import detourv2

    body: dict = {
        "snapshotId": out.snapshot_id,
        "linkId": out.link_id,
        "engine": "v2-boundary",
        "algorithm": out.algorithm,
        "algorithmVersion": out.algorithm_version,
        "stability": "development preview - not a stable 3.0.0",
        "request": {"scope": out.scope, "metric": out.metric,
                    "vehicle": out.profile},
        "headline": out.headline,
        "qualityFlags": out.quality_flags,
        "closure": closure_mod.as_dict(out.closure),
        "boundary": ports.as_dict(out.boundary),
        "movements": movements.as_dict(out.movement_set),
        "replacements": replacement.as_dict(out.replacements),
        "stageMs": out.stage_ms,
        "runtimeMs": out.runtime_ms,
    }
    if not include_all_movements:
        body["movements"]["movements"] = [
            movements.movement_dict(m) for m in out.movement_set.included]

    body["principal"] = (
        None if out.principal is None else {
            **replacement.path_dict(out.principal),
            "movement": (movements.movement_dict(out.principal_movement)
                         if out.principal_movement else None),
        })
    body["corridor"] = (corridor_mod.as_dict(out.corridor)
                        if out.corridor is not None else None)

    # Isolation stays in its own block, described in its own words, and is
    # never folded into the routing result above it.
    body["isolation"] = (detourv2.isolation_dict(out.isolation)
                         if out.isolation is not None else None)

    geom = {}
    for name, g in (("selectedSegment", out.selected_geometry),
                    ("closure", out.closure_geometry),
                    ("intactMovement", out.intact_geometry),
                    ("replacement", out.replacement_geometry)):
        if g is not None:
            geom[name] = routegeom.as_dict(g)
    body["geometry"] = geom or None
    return body
