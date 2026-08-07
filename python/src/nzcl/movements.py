"""Intact through movements: the trips a closure actually interrupts.

WHY THIS EXISTS
---------------
`ports.py` says where a closure meets the open network. It does not say which
of those crossings belong together. A closure with four entry ports and four
exit ports offers sixteen ordered pairs, and only some of them are trips
anybody was making.

The temptation is to take all-pairs routing between boundary nodes and call
every reachable pair a movement. That is wrong in a specific and measurable
way: on a square, the pair (enter from the west, leave to the west) is
reachable, but the cheapest way to make that trip never touches the closed
edge, so closing the road costs it nothing. Reporting it as an interrupted
movement would manufacture a detour out of a trip that has none.

So a movement is not "a reachable pair". A movement is a pair whose INTACT
cheapest route TRAVERSES THE CLOSURE. That is the test, and it is the only test
that makes the replacement number mean what a reader thinks it means:

    "before, this trip went through here; now it has to go round"

WHERE THE TRIP IS MEASURED, AND WHY IT IS NOT THE OUTSIDE NODES
---------------------------------------------------------------
A movement is measured between the two ports' CLOSURE-SIDE nodes - the places
the crossing itself begins and ends. The port arcs are what make the pair a
port pair rather than an arbitrary node pair: they establish that traffic can
reach the entry from the open network, and discharge from the exit back into
it, in compatible directions. They are not part of the measured trip.

The first draft of this module measured between the ports' OUTSIDE nodes and it
was wrong in a way worth recording, because it looked more thorough. On a
square block with the south side closed, the outside nodes of the two crossings
are the block's other two corners, and the cheapest intact route between THOSE
is straight along the north side - so the intact leg was 300 m (round three
sides), the replacement 100 m (the north side alone), and the "detour" came out
at MINUS 200 m. Two faults in one: the intact side was constrained to use the
closure while the replacement side was not, so the comparison was not
like-for-like; and the result contradicted the endpoint measure on precisely
the case where PR 2 promised not to change the answer.

Measured between the closure-side nodes the same square gives intact 100 m,
replacement 300 m, penalty +200 m - which is exactly what the endpoint measure
gives, because on a simple two-way segment the ports sit on the segment's own
endpoints. That is the compatibility property, and it holds by construction
rather than by coincidence.

THE ONE PLACE THE TEST IS AMBIGUOUS
-----------------------------------
"The cheapest intact route traverses the closure" is decided on a path, and
where two routes cost EXACTLY the same - one through the closure, one round it
- which route comes back is the router's choice among equals. So a pair sitting
on such a tie could be included or excluded depending on nothing that matters.

It is left ambiguous here rather than papered over, because the ambiguity is
harmless and detecting it properly costs a second search. A pair in that
position has an equal-cost alternative by definition, so its network penalty is
exactly zero: closing the road costs that trip nothing either way.
`replacement.py` detects the case where both costs are known and flags it
CLOSURE_NOT_NECESSARY_EQUAL_COST_ALTERNATIVE. The independent oracle asserts
only the strict case, for the same reason.

WHAT IS RETURNED
----------------
Every considered pair, included or not, with the reason. An excluded pair is
evidence, not noise - it is how a reader checks that the engine did not quietly
drop the movement they cared about, and it is how a shuffled-input test proves
the decision did not depend on row order.

Nothing here removes anything from the graph. Phase 6 is entirely a question
about the INTACT network; the closure is used only as a set of arc ids to test
route membership against. The replacement side is `replacement.py`.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db
from .ports import ClosureBoundary, Port
from .routing import Metric, Profile, route_many_paths

#: Bump when the MOVEMENT RULE changes - a different validity test, a different
#: bound. Movement ids embed it, so a movement identified under an older rule
#: cannot be mistaken for a current one.
MOVEMENT_MODEL_VERSION = "1.0.0"

#: How many ports on each side enter the pairing, at most. The product is the
#: hard ceiling on candidate pairs, and it is a ceiling rather than a hope: a
#: branching source-feature closure on a dense urban grid can present dozens of
#: crossings on each side, and |E| x |X| with no bound is how a request stops
#: being interactive.
#:
#: Ports are taken in the order `ports.derive` already fixed - outward distance
#: from the selected segment, then the port's STABLE key - so truncation keeps
#: the crossings NEAREST the segment the user actually clicked, and keeps the
#: same ones after a re-ingest that renumbers the graph.
MAX_PORTS_PER_SIDE = 20
MAX_CANDIDATE_PAIRS = MAX_PORTS_PER_SIDE * MAX_PORTS_PER_SIDE

#: Disconnected pieces of one closure that are analysed. A source feature split
#: across a gap is normally two; more than a handful means the scope is doing
#: something nobody asked for, and the bound is reported either way.
MAX_CLOSURE_COMPONENTS = 8

#: Omitted pairs listed individually. Counts are exact and unbounded-safe; the
#: list is a worked example, not a manifest. Returning every omitted pair made
#: the audit payload the full dropped cross-product - a bounded computation
#: with an unbounded report.
OMITTED_SAMPLE_LIMIT = 100

Confidence = Literal["high", "medium", "low"]

#: Why a candidate pair was included or excluded. One code per outcome, chosen
#: at exactly one place in `identify`.
REASON_CODES = (
    "THROUGH_MOVEMENT",            # included
    "U_TURN_AT_BOUNDARY",          # excluded: enters and leaves by the same link
    "U_TURN_IN_ROUTE",             # excluded: the route reverses across a port
    "NO_INTACT_ROUTE",             # excluded: unreachable in the intact network
    "DOES_NOT_TRAVERSE_CLOSURE",   # excluded: the trip never used the closure
    "SEARCH_UNRESOLVED",           # excluded: the search did not conclude
    "NOT_EVALUATED_TRUNCATED",     # excluded: beyond the candidate bound
)


@dataclass
class Movement:
    """One entry -> exit pair, with the reason it counts or does not.

    `included` and `reason_code` are the whole point. A caller that wants only
    the real movements filters on `included`; a caller auditing the engine
    reads the excluded ones and their reasons, which is where a wrong answer
    shows up first.
    """

    movement_id: str
    entry_port_id: str
    exit_port_id: str
    #: Where the trip is MEASURED: the crossing's own two ends.
    from_node: int
    to_node: int
    #: Where the traffic comes from and goes to. Context, not measurement -
    #: see the module header for why measuring between these was wrong.
    entry_node: int
    exit_node: int
    entry_arc_id: int
    exit_arc_id: int
    entry_link_id: int
    exit_link_id: int
    entry_direction: str
    exit_direction: str

    included: bool
    reason_code: str
    reason: str

    #: Publisher-assigned keys for the two crossings. Every ordering and every
    #: tie-break in this module uses these, never the port ids: a port id is a
    #: hash of `arc_id`, which the next ingest reassigns. See `stableid.py`.
    entry_stable_key: str = ""
    exit_stable_key: str = ""

    #: The intact crossing: from_node to to_node. The port arcs are NOT in it.
    intact_arc_ids: list[int] = field(default_factory=list)
    intact_link_ids: list[int] = field(default_factory=list)
    intact_distance_m: float | None = None
    intact_time_s: float | None = None
    #: Which arcs OF THE CLOSURE that intact trip used. Empty means the trip
    #: did not go through the closure, which is exactly why it is excluded.
    removed_arc_ids_used: list[int] = field(default_factory=list)
    #: True when the connecting route stayed inside the closure the whole way.
    #: False means it left and came back, which happens on a disjoint closure
    #: and is reported rather than hidden.
    stays_within_closure: bool = False

    evidence: list[str] = field(default_factory=list)
    confidence: Confidence = "high"

    @property
    def key(self) -> tuple[str, str]:
        """Ordering key. Ingest-invariant, so a shuffled reload keeps it."""
        return (self.entry_stable_key, self.exit_stable_key)


@dataclass
class MovementSet:
    """Every candidate pair considered for one closure, and how the search went."""

    snapshot_id: str
    closure_fingerprint: str
    selected_link_id: int
    profile: Profile
    metric: Metric

    movements: list[Movement] = field(default_factory=list)

    #: Search status, NOT a finding about any pair. When this is not "OK", an
    #: excluded pair means "not established", never "no such trip".
    status: str = "OK"
    detail: str = ""

    entry_ports_considered: int = 0
    exit_ports_considered: int = 0
    entry_ports_available: int = 0
    exit_ports_available: int = 0
    candidate_pairs: int = 0
    truncated: bool = False
    candidate_bound: int = MAX_CANDIDATE_PAIRS

    #: Disconnected pieces of the closure, and how many were analysed. Pairs
    #: are formed WITHIN a piece: a trip through the closure crosses one
    #: contiguous closed stretch.
    closure_components: int = 1
    components_considered: int = 1
    #: Exact counts for everything not evaluated. Counts, not rows - see
    #: `_omitted_sample`.
    omitted_pair_count: int = 0
    omitted_entry_ports: int = 0
    omitted_exit_ports: int = 0
    cross_component_pair_count: int = 0
    #: At most OMITTED_SAMPLE_LIMIT worked examples, deterministically chosen.
    omitted_pair_sample: list[dict] = field(default_factory=list)

    @property
    def exhaustive(self) -> bool:
        """True only when every candidate pair was actually evaluated.

        Anything else means a pair nobody looked at could hold the worst
        detour, the only disconnected movement, or the movement the reader
        cares about - so no headline derived from this may imply completeness.
        """
        return not self.truncated and self.omitted_pair_count == 0

    runtime_ms: int = 0
    route_runtime_ms: int = 0
    model_version: str = MOVEMENT_MODEL_VERSION

    @property
    def included(self) -> list[Movement]:
        return [m for m in self.movements if m.included]

    @property
    def resolved(self) -> bool:
        return self.status == "OK"


def movement_id(snapshot_id: str, closure_fingerprint: str, entry_port_id: str,
                exit_port_id: str) -> str:
    """Deterministic identity for a movement.

    A hash of identifiers only. Nothing the database chose to return first
    enters it, so the same closure yields the same movement ids on any machine,
    in any row order, after any VACUUM.
    """
    payload = "|".join((
        "movement", MOVEMENT_MODEL_VERSION, snapshot_id, closure_fingerprint,
        entry_port_id, exit_port_id,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def identify(
    boundary: ClosureBoundary,
    removed_arc_ids: Sequence[int],
    *,
    metric: Metric = "distance",
    profile: Profile = "car",
    statement_timeout_ms: int = 20_000,
    max_ports_per_side: int = MAX_PORTS_PER_SIDE,
) -> MovementSet:
    """Which entry -> exit pairs genuinely traverse this closure, and why.

    `removed_arc_ids` rather than removed links, deliberately. Under
    `scope='direction'` the link stays and only one traversal is withdrawn, so
    "did this trip use the closure" is a question about ARCS. Testing link
    membership there would report a movement as interrupted when the direction
    it actually used is untouched.
    """
    t0 = time.perf_counter()
    snap = boundary.snapshot_id
    removed = frozenset(int(a) for a in removed_arc_ids)

    out = MovementSet(
        snapshot_id=snap, closure_fingerprint=boundary.closure_fingerprint,
        selected_link_id=boundary.selected_link_id, profile=profile,
        metric=metric,
        entry_ports_available=len(boundary.entry_ports),
        exit_ports_available=len(boundary.exit_ports),
        candidate_bound=max_ports_per_side * max_ports_per_side,
    )

    # --- one group per PIECE of the closure -------------------------------
    # A trip through the closure crosses ONE contiguous closed stretch. Pairing
    # an entry on one disjoint piece with an exit on another describes a trip
    # that leaves the closure and meets it again somewhere else, which is two
    # interruptions and not one movement.
    #
    # Pairing across pieces was also how a 50 km-distant piece could take the
    # candidate allowance from the segment the user clicked - see the
    # `distance_from_selected_m` docstring in `ports.py`.
    groups: list[tuple[int, list[Port], list[Port]]] = []
    component_ids = sorted({p.closure_component_id
                            for p in boundary.entry_ports + boundary.exit_ports})
    out.closure_components = len(component_ids)
    kept_entries: list[Port] = []
    kept_exits: list[Port] = []
    omitted_entries = omitted_exits = 0

    for comp in component_ids[:MAX_CLOSURE_COMPONENTS]:
        all_e = [p for p in boundary.entry_ports if p.closure_component_id == comp]
        all_x = [p for p in boundary.exit_ports if p.closure_component_id == comp]
        e_c, x_c = all_e[:max_ports_per_side], all_x[:max_ports_per_side]
        omitted_entries += len(all_e) - len(e_c)
        omitted_exits += len(all_x) - len(x_c)
        if e_c and x_c:
            groups.append((comp, e_c, x_c))
        kept_entries += e_c
        kept_exits += x_c

    for comp in component_ids[MAX_CLOSURE_COMPONENTS:]:
        omitted_entries += sum(1 for p in boundary.entry_ports
                               if p.closure_component_id == comp)
        omitted_exits += sum(1 for p in boundary.exit_ports
                             if p.closure_component_id == comp)

    entries, exits = kept_entries, kept_exits
    out.entry_ports_considered = len(entries)
    out.exit_ports_considered = len(exits)
    out.components_considered = len(component_ids[:MAX_CLOSURE_COMPONENTS])
    out.omitted_entry_ports = omitted_entries
    out.omitted_exit_ports = omitted_exits

    # Every pair that is NOT evaluated, counted rather than enumerated. The
    # routing is bounded at max_ports_per_side squared per piece; the AUDIT
    # PAYLOAD was not, because the old `_truncation_rows` built the whole
    # dropped cross-product as JSON. A bounded computation with an unbounded
    # report is still unbounded.
    evaluated = sum(len(e) * len(x) for _c, e, x in groups)
    all_pairs = len(boundary.entry_ports) * len(boundary.exit_ports)
    out.omitted_pair_count = max(0, all_pairs - evaluated)
    out.cross_component_pair_count = sum(
        len([p for p in boundary.entry_ports if p.closure_component_id == a])
        * len([p for p in boundary.exit_ports if p.closure_component_id == b])
        for a in component_ids for b in component_ids if a != b)
    out.truncated = (omitted_entries > 0 or omitted_exits > 0
                     or len(component_ids) > MAX_CLOSURE_COMPONENTS)
    out.omitted_pair_sample = _omitted_sample(snap, boundary, groups)

    if not groups:
        out.detail = (
            f"{len(boundary.entry_ports)} entry and {len(boundary.exit_ports)} "
            "exit port(s): a through movement needs at least one of each on the "
            "same piece of the closure")
        out.candidate_pairs = 0
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    if not removed:
        out.detail = ("the closure removes no arcs, so no trip can traverse it")
        out.movements = _all_excluded(
            snap, boundary, groups, "DOES_NOT_TRAVERSE_CLOSURE",
            "the closure removes no arcs")
        out.candidate_pairs = len(out.movements)
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    # ---- one edge-set load for every pair --------------------------------
    # The connecting route runs closure-node to closure-node on the INTACT
    # graph. Routing outside-node to outside-node instead would let the search
    # skip the port arcs entirely and answer a different question.
    sources = sorted({p.closure_node for p in entries})
    targets = sorted({p.closure_node for p in exits})
    routed = route_many_paths(
        snap, sources, targets, metric=metric, profile=profile,
        statement_timeout_ms=statement_timeout_ms)
    out.route_runtime_ms = routed.runtime_ms

    if not routed.resolved:
        # A search that did not conclude says nothing about any pair. Every
        # candidate is reported as unresolved, and NONE as "no such movement".
        out.status = routed.status
        out.detail = routed.detail or "the intact multi-target search did not resolve"
        out.movements = _all_excluded(
            snap, boundary, groups, "SEARCH_UNRESOLVED", out.detail)
        out.candidate_pairs = len(out.movements)
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    arc_meta = _arc_costs(snap, _arcs_to_describe(routed.paths, entries, exits))

    movements: list[Movement] = []
    for _comp, e_c, x_c in groups:
        for e in e_c:
            for x in x_c:
                movements.append(_evaluate(snap, boundary, e, x, routed, removed,
                                           arc_meta, metric))

    # Sorted on intrinsic keys only. Not on cost - two movements can cost the
    # same, and a cost-first order would then depend on which the planner
    # emitted first, which is precisely the class of bug PR 1 found.
    movements.sort(key=lambda m: m.key)

    out.movements = movements
    out.candidate_pairs = len(movements)
    kept = sum(1 for m in movements if m.included)

    # A closure whose every crossing is at ONE node is a stub - a cul-de-sac,
    # a spur, a service road. There was never a trip THROUGH it, only trips to
    # and from it, and saying "0 of 4 candidate pairs traverse the closure"
    # describes the arithmetic rather than the road. 78 of the first 500
    # sampled links are this shape, so it is worth its own sentence.
    crossing_nodes = {p.closure_node for p in entries + exits}
    if kept == 0 and len(crossing_nodes) == 1:
        out.detail = (
            "every way in and out of this closure is at the same place, so "
            "there was no trip through it to interrupt - only trips to and "
            "from it")
    else:
        out.detail = (
            f"{kept} of {out.candidate_pairs} candidate pair(s) traverse the "
            f"closure in the intact network"
            + (f"; {len(dropped_entries)} entry and {len(dropped_exits)} exit "
               f"port(s) were beyond the candidate bound" if out.truncated
               else ""))
    out.runtime_ms = int((time.perf_counter() - t0) * 1000)
    return out


# ------------------------------------------------------------------ internals
def _evaluate(snap: str, b: ClosureBoundary, e: Port, x: Port,
              routed, removed: frozenset[int], arc_meta: dict[int, dict],
              metric: Metric) -> Movement:
    """One candidate pair, decided once, with the reason recorded."""
    m = _blank(snap, b, e, x)

    # --- the immediate U-turn artefact ---------------------------------
    # Entering by a link and leaving by that same link at the same node is not
    # a trip through the closure; it is a vehicle turning round at the cordon.
    # It would otherwise route (cost zero through the closure) and be counted.
    if (e.link_id == x.link_id and e.outside_node == x.outside_node
            and e.closure_node == x.closure_node):
        m.reason_code = "U_TURN_AT_BOUNDARY"
        m.reason = ("entry and exit are the two directions of the same boundary "
                    "link at the same node: a U-turn at the cordon, not a trip "
                    "through the closure")
        return m

    # --- the crossing that is not a crossing -----------------------------
    # Both ports meet the closure at the SAME node: a turning movement from one
    # boundary road onto another, passing the closure rather than through it.
    # Its crossing has zero length and uses no closed arc.
    #
    # Handled here explicitly because pgRouting returns NO ROW for a
    # source-equals-target pair, so falling through to the lookup below
    # reported it as NO_INTACT_ROUTE - "these two places cannot be reached from
    # one another" - about a pair that is the same place. Found by the
    # independent oracle, which computes a distance of zero and said so.
    if e.closure_node == x.closure_node:
        m.reason_code = "DOES_NOT_TRAVERSE_CLOSURE"
        m.reason = ("both ports meet the closure at the same node, so this is a "
                    "turn past the closure rather than a trip through it")
        return m

    key = (e.closure_node, x.closure_node)
    if key not in routed.costs:
        m.reason_code = "NO_INTACT_ROUTE"
        m.reason = ("no directed route exists between these two boundary "
                    "crossings even in the intact network, so there was no "
                    "through trip here to interrupt")
        return m

    arcs = list(routed.paths.get(key, []))

    # --- the route must reverse across neither port ----------------------
    # Leaving straight back out along the entry link, or arriving by reversing
    # along the exit link, is a vehicle turning round at the cordon.
    if arcs:
        first, last = arc_meta.get(arcs[0]), arc_meta.get(arcs[-1])
        if first is not None and int(first["link_id"]) == e.link_id \
                and int(first["target"]) == e.outside_node:
            m.reason_code = "U_TURN_IN_ROUTE"
            m.reason = ("the crossing leaves immediately back along the entry "
                        "link, which is a U-turn at the boundary")
            return m
        if last is not None and int(last["link_id"]) == x.link_id \
                and int(last["source"]) == x.outside_node:
            m.reason_code = "U_TURN_IN_ROUTE"
            m.reason = ("the crossing arrives by reversing along the exit link, "
                        "which is a U-turn at the boundary")
            return m

    used = sorted(a for a in arcs if a in removed)
    if not used:
        m.reason_code = "DOES_NOT_TRAVERSE_CLOSURE"
        m.reason = ("the cheapest intact route between these crossings does not "
                    "use any closed arc, so closing the road does not change "
                    "this trip")
        m.intact_arc_ids = arcs
        return m

    distance, time_s, links = _summarise(arcs, arc_meta)

    m.included = True
    m.reason_code = "THROUGH_MOVEMENT"
    m.reason = (
        f"the cheapest intact route from this entry to this exit traverses "
        f"{len(used)} closed arc(s), so it is a trip the closure interrupts")
    m.intact_arc_ids = arcs
    m.intact_link_ids = links
    m.intact_distance_m = distance
    m.intact_time_s = time_s
    m.removed_arc_ids_used = used
    m.stays_within_closure = all(a in removed for a in arcs)

    ev = ["INTACT_ROUTE_USES_CLOSURE"]
    if m.stays_within_closure:
        ev.append("WHOLLY_WITHIN_CLOSURE")
    else:
        ev.append("LEAVES_AND_REENTERS_CLOSURE")
    if len(used) == 1:
        ev.append("SINGLE_CLOSURE_ARC")
    if time_s is None:
        ev.append("TIME_UNAVAILABLE")
    m.evidence = ev
    # A route that leaves the closure and comes back is a weaker claim about
    # "a trip through here": part of it is already outside. Said out loud
    # rather than folded into the number.
    m.confidence = "high" if m.stays_within_closure else "medium"
    return m


def _blank(snap: str, b: ClosureBoundary, e: Port, x: Port) -> Movement:
    return Movement(
        movement_id=movement_id(snap, b.closure_fingerprint, e.port_id, x.port_id),
        entry_port_id=e.port_id, exit_port_id=x.port_id,
        from_node=e.closure_node, to_node=x.closure_node,
        entry_node=e.outside_node, exit_node=x.outside_node,
        entry_arc_id=e.arc_id, exit_arc_id=x.arc_id,
        entry_link_id=e.link_id, exit_link_id=x.link_id,
        entry_direction=e.direction, exit_direction=x.direction,
        entry_stable_key=e.stable_key, exit_stable_key=x.stable_key,
        included=False, reason_code="", reason="", confidence="high")


def _all_excluded(snap: str, b: ClosureBoundary, groups, code: str,
                  reason: str) -> list[Movement]:
    out = []
    for _comp, entries, exits in groups:
        for e in entries:
            for x in exits:
                m = _blank(snap, b, e, x)
                m.reason_code = code
                m.reason = reason
                m.confidence = "low" if code == "SEARCH_UNRESOLVED" else "high"
                out.append(m)
    out.sort(key=lambda m: m.key)
    return out


def _omitted_sample(snap: str, b: ClosureBoundary, groups
                    ) -> list[dict]:
    """A BOUNDED, deterministic sample of the pairs that were not evaluated.

    The previous version returned a row for every omitted pair, which on a
    dense urban closure is the whole dropped cross-product - a bounded
    computation with an unbounded report, which is still unbounded. Counts live
    on the result; this is the worked example a reader can spot-check, capped
    at `OMITTED_SAMPLE_LIMIT` and taken in stable-key order so it is the same
    sample on every run.
    """
    evaluated = {(e.port_id, x.port_id)
                 for _c, es, xs in groups for e in es for x in xs}
    considered_entries = {p.port_id for _c, es, _x in groups for p in es}
    considered_exits = {p.port_id for _c, _e, xs in groups for p in xs}

    out: list[dict] = []
    for e in b.entry_ports:
        for x in b.exit_ports:
            if (e.port_id, x.port_id) in evaluated:
                continue
            if e.closure_component_id != x.closure_component_id:
                why = ("the two ports are on different disconnected pieces of "
                       "the closure, so no single crossing joins them")
            elif (e.port_id not in considered_entries
                  or x.port_id not in considered_exits):
                why = (f"beyond the bound of {MAX_PORTS_PER_SIDE} port(s) per "
                       "side per piece of the closure")
            else:
                why = "not evaluated"
            out.append({
                "entryStableKey": e.stable_key,
                "exitStableKey": x.stable_key,
                "entryComponent": e.closure_component_id,
                "exitComponent": x.closure_component_id,
                "reason": why,
            })
            if len(out) >= OMITTED_SAMPLE_LIMIT * 4:
                break
        if len(out) >= OMITTED_SAMPLE_LIMIT * 4:
            break
    out.sort(key=lambda d: (d["entryStableKey"], d["exitStableKey"]))
    return out[:OMITTED_SAMPLE_LIMIT]


def _arcs_to_describe(paths, entries, exits) -> list[int]:
    wanted: set[int] = {p.arc_id for p in entries} | {p.arc_id for p in exits}
    for arcs in paths.values():
        wanted.update(arcs)
    return sorted(wanted)


def _arc_costs(snapshot_id: str, arc_ids: Sequence[int]) -> dict[int, dict]:
    if not arc_ids:
        return {}
    rows = db.query(
        "SELECT arc_id, link_id, source, target, direction, cost_distance_m, "
        "       cost_time_s FROM arcs WHERE snapshot_id=%s AND arc_id = ANY(%s)",
        (snapshot_id, sorted(int(a) for a in arc_ids)))
    return {int(r["arc_id"]): r for r in rows}


def _summarise(arc_ids: Sequence[int], meta: dict[int, dict]
               ) -> tuple[float, float | None, list[int]]:
    """Distance, time (None if any arc has none) and the ordered link path."""
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
def movement_dict(m: Movement) -> dict:
    return {
        "movementId": m.movement_id,
        "entryPortId": m.entry_port_id,
        "exitPortId": m.exit_port_id,
        "fromNode": m.from_node,
        "toNode": m.to_node,
        "entryNode": m.entry_node,
        "exitNode": m.exit_node,
        "entryArcId": m.entry_arc_id,
        "exitArcId": m.exit_arc_id,
        "entryLinkId": m.entry_link_id,
        "exitLinkId": m.exit_link_id,
        "entryDirection": m.entry_direction,
        "exitDirection": m.exit_direction,
        "entryStableKey": m.entry_stable_key,
        "exitStableKey": m.exit_stable_key,
        "included": m.included,
        "reasonCode": m.reason_code,
        "reason": m.reason,
        "intactArcIds": m.intact_arc_ids,
        "intactLinkIds": m.intact_link_ids,
        "intactDistanceM": None if m.intact_distance_m is None
        else round(m.intact_distance_m, 1),
        "intactTimeS": None if m.intact_time_s is None
        else round(m.intact_time_s, 1),
        "removedArcIdsUsed": m.removed_arc_ids_used,
        "staysWithinClosure": m.stays_within_closure,
        "evidence": m.evidence,
        "confidence": m.confidence,
    }


def as_dict(s: MovementSet) -> dict:
    return {
        "movementModelVersion": s.model_version,
        "status": s.status,
        "resolved": s.resolved,
        "detail": s.detail,
        "metric": s.metric,
        "vehicleProfile": s.profile,
        "entryPortsAvailable": s.entry_ports_available,
        "exitPortsAvailable": s.exit_ports_available,
        "entryPortsConsidered": s.entry_ports_considered,
        "exitPortsConsidered": s.exit_ports_considered,
        "candidatePairs": s.candidate_pairs,
        "candidateBound": s.candidate_bound,
        "truncated": s.truncated,
        "exhaustive": s.exhaustive,
        "closureComponents": s.closure_components,
        "componentsConsidered": s.components_considered,
        "omittedPairCount": s.omitted_pair_count,
        "omittedEntryPorts": s.omitted_entry_ports,
        "omittedExitPorts": s.omitted_exit_ports,
        "crossComponentPairCount": s.cross_component_pair_count,
        "omittedPairSampleLimit": OMITTED_SAMPLE_LIMIT,
        "omittedPairSample": s.omitted_pair_sample,
        "includedCount": len(s.included),
        "movements": [movement_dict(m) for m in s.movements],
        "runtimeMs": s.runtime_ms,
        "routeRuntimeMs": s.route_runtime_ms,
    }
