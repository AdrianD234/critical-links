"""Deterministic corridor selection: where the diversion actually begins.

WHAT THIS REPLACES
------------------
V1 walks outward from the closed link by repeatedly taking the straightest
continuation, and whatever node it lands on becomes the corridor endpoint. Two
faults, and the second is the serious one.

It is GREEDY: the first step is committed before anything is known about where
the walk leads, so a slightly-straighter side road beats the road the corridor
is actually on, permanently, at hop one.

And it is ORDER-SENSITIVE: where two continuations tie on heading, the winner is
whichever row came back first. PR 1 found a defect of exactly this shape in a
BFS tie-break, so it is assumed to recur here and is tested for directly with
shuffled-input fixtures.

WHAT REPLACES IT
----------------
A bounded beam search. Several partial walks are carried outward at once, ranked
on continuity evidence, and NOTHING is committed until every surviving candidate
pair has been routed. The choice is then made by a stated lexicographic rule
over the whole candidate set, not by the order the walks were generated in.

THE CHOICE RULE
---------------
Among candidate (upstream, downstream) pairs, in order:

  1. a represented replacement route exists between them
  1b. both ends are junctions, where any such pair qualifies. A PREFERENCE, not
     a requirement: naming a point mid-road as "where the detour starts" is not
     something a reader can act on, but requiring junctions returned NO
     corridor on a fixture that plainly had one, because the junctions either
     side of the closure turned out to be the same node. The tier used is
     reported as `admissibilityLevel`.
  2. minimum outward distance from the closure - read as the FARTHER of the two
     sides, because that is how far from the closure a driver has to be sent at
     worst, and a rule that minimised the nearer side would happily push one
     end miles out
  3. minimum combined outward distance - the pair that disturbs least in total,
     which breaks ties in (2) without reopening them
  4. minimum replacement-path cost
  5. strongest road-continuity evidence
  6. stable-id tie-break

(6) is reached only when everything measurable is equal. "Stable" is meant
strictly: the id is hashed from the AMDS feature id and direction of every arc
walked, plus the candidate node's POSITION in metres - all things the publisher
chose. It is never "whichever the planner returned first", and it is never
`arc_id`, which the noding pass hands out in ingest order.

That distinction was found the hard way. The first draft hashed `arc_id`, which
is perfectly reproducible on one database and completely reassigned by the next
ingest. Shuffling the input of a nine-link fixture flipped the selected corridor
pair on three seeds out of eight, without one metre of road changing. See
`stableid.py`.

CONTINUITY EVIDENCE
-------------------
Route designation, canonical road name, state-highway status, road class /
model asset type, heading continuity, and degree-two continuity. Carried as a
ranked TUPLE rather than collapsed into one number, so "strongest evidence" is
a lexicographic comparison a reader can check, and so the interface can say
which evidence it was.

RAMM corridor identity is NOT used. It is permitted only where cleared for
internal use, and docs/LICENSING.md records it as not cleared - `copyrightText`
empty, not catalogued. It is also absent from this database entirely, and its
`roadCorridor` spans hundreds of kilometres, so every link on a state highway
would share one value and it would separate nothing. Excluded on all three
counts; publishing a field whose licence is uncertain is a stop condition.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import db, stableid
from .ports import ClosureBoundary, Port
from .routing import Profile, route_many_paths

#: Bump when the SEARCH or the CHOICE RULE changes. Candidate ids embed it.
CORRIDOR_MODEL_VERSION = "1.0.0"

Side = Literal["upstream", "downstream"]

# --- search bounds. Every one of them is reported in the output ------------
#: Partial walks carried forward per side per hop. Wide enough that a corridor
#: is not lost to one bad step, narrow enough that the search stays bounded.
BEAM_WIDTH = 6
#: Steps outward from the closure boundary.
MAX_HOPS = 12
#: How far outward the WALK may reach, in metres.
#:
#: This is an EXPANSION bound, not a cap on outward distance, and the
#: difference is not pedantry. The SEED candidate is the port's own outside
#: node, and its distance from the closure is the boundary arc's own length -
#: on a rural state highway one link can be over ten kilometres. The seed is
#: admitted whatever its length, because the first node outside the closure is
#: the first place a driver could act on, however far away it is. There is no
#: nearer answer available to give.
#:
#: It was documented as a five-kilometre bound, and the national sample then
#: recorded chosen corridor ports at 6,613 m, 7,863 m and 10,582 m. Every one
#: was a seed, at hop 0, inserted before a check that only ever runs on a step.
#: The behaviour is defensible; the claim was not. A candidate past this
#: distance is now flagged `beyond_search_bound`, and a corridor whose chosen
#: pair contains one reports `confidence: low`.
MAX_EXPANSION_OUTWARD_M = 5_000.0
#: Distinct decision ports kept per side, after the walk.
MAX_CANDIDATES_PER_SIDE = 20
#
# NOTE ON A FIX THAT WAS NOT THE FIX. The walk looked like the expensive part
# of a national request - 1,473 ms of 2,333 ms - so an early stop was added,
# ending the expansion once three junctions had been found. It made the stage
# SLOWER (1,583 ms), which is what sent someone to measure instead of reason.
#
# Profiling 12 national requests:
#
#     _annotate_degree    71 calls   10,888 ms   153.4 ms each
#     route_many_paths    10 calls    4,921 ms   492.1 ms each
#     _step_rows          51 calls      115 ms     2.3 ms each
#
# The walk was never the cost. The steps are 2.3 ms and it only ever reached
# two to four hops anyway. The cost was ONE degree lookup, and the early stop
# had added five more of them per request. See `_annotate_degree`.
#: Pairs routed. The product of the two caps above would be 400; this is the
#: ceiling actually enforced, and truncation is flagged rather than silent.
MAX_PAIRS = MAX_CANDIDATES_PER_SIDE * MAX_CANDIDATES_PER_SIDE

#: Degrees of turn still counted as "the same road continuing".
HEADING_TOLERANCE_DEG = 40.0


@dataclass(frozen=True)
class Continuity:
    """Why this step is, or is not, the same road carrying on.

    Every field is evidence a person could check on a map. None of them is a
    weight fitted to anything.
    """

    route_designation_match: bool = False
    road_name_match: bool = False
    state_highway_continues: bool = False
    road_class_match: bool = False
    degree_two: bool = False
    heading_continuous: bool = False
    turn_angle_deg: float | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        out = []
        if self.route_designation_match:
            out.append("ROUTE_DESIGNATION_CONTINUES")
        if self.road_name_match:
            out.append("ROAD_NAME_CONTINUES")
        if self.state_highway_continues:
            out.append("STATE_HIGHWAY_CONTINUES")
        if self.road_class_match:
            out.append("ROAD_CLASS_CONTINUES")
        if self.degree_two:
            out.append("DEGREE_TWO_NO_CHOICE")
        if self.heading_continuous:
            out.append("HEADING_CONTINUOUS")
        return tuple(out)

    @property
    def rank(self) -> tuple[int, ...]:
        """Strength, most decisive evidence first. Higher is stronger.

        Ordered by how much each one actually establishes. A shared route
        designation is a statement by the road-controlling authority that this
        is one route. A shared name is nearly as strong. Degree two means there
        was no choice to get wrong. Heading is last because a straight-through
        side road defeats it, which is the exact failure V1's walk has.
        """
        return (
            int(self.route_designation_match),
            int(self.road_name_match),
            int(self.degree_two),
            int(self.state_highway_continues),
            int(self.road_class_match),
            int(self.heading_continuous),
        )


@dataclass
class DecisionPort:
    """One candidate place for the diversion to begin or rejoin."""

    candidate_id: str
    side: Side
    node: int
    outward_distance_m: float
    hops: int
    #: Arcs from the boundary outward to this node, in walk order.
    arc_trail: list[int]
    origin_port_id: str
    origin_boundary_node: int
    #: The same trail, named in publisher terms. This is what `candidate_id`
    #: hashes and what every tie-break compares.
    stable_key: str = ""

    #: Summed continuity rank over the whole trail. A tuple, compared
    #: lexicographically; never flattened to a score.
    continuity_rank: tuple[int, ...] = ()
    evidence: list[str] = field(default_factory=list)
    road_name: str | None = None
    route_designation: str | None = None
    is_state_highway: bool = False
    node_degree: int = 0
    #: True when the node offers a genuine choice - three or more links on it.
    #: A degree-two node is somewhere you pass through, not somewhere you
    #: decide to divert.
    is_decision_point: bool = False
    #: True when this candidate sits further from the closure than the walk was
    #: allowed to travel. Only a SEED can be: its distance is the boundary
    #: arc's own length, which no bound controls. Reported rather than hidden,
    #: and it lowers the corridor's confidence.
    beyond_search_bound: bool = False

    included: bool = True
    reason_code: str = "CANDIDATE"
    reason: str = ""


@dataclass
class CandidatePair:
    """One (upstream, downstream) pair, its score, and why it was kept."""

    pair_id: str
    upstream_id: str
    downstream_id: str
    upstream_node: int
    downstream_node: int
    upstream_outward_m: float
    downstream_outward_m: float

    #: Rule 2: the farther of the two sides.
    max_outward_m: float = 0.0
    #: Rule 3.
    combined_outward_m: float = 0.0
    #: Rule 4. None when no represented route exists.
    replacement_cost_m: float | None = None
    #: Rule 5.
    continuity_rank: tuple[int, ...] = ()
    #: Both ends are junctions, so both are places a driver can act on. A
    #: PREFERENCE applied after rule 1 and before rule 2, with a stated
    #: fallback - see `_choose`.
    both_decision_points: bool = False

    replacement_arc_ids: list[int] = field(default_factory=list)
    valid: bool = False
    #: The intact trip behind this pair, once validated.
    witness: "Witness | None" = None
    reason_code: str = ""
    reason: str = ""

    @property
    def sort_key(self):
        """The lexicographic rule, expressed once, as a key.

        Written as a single key rather than a chain of comparisons so that
        there is exactly one place the priority order lives, and so that
        `sorted` cannot reorder equal elements by input order - every tie is
        resolved by a later term, and the last term is intrinsic.
        """
        return (
            0 if self.valid else 1,                     # 1. route exists
            round(self.max_outward_m, 3),               # 2. farther side
            round(self.combined_outward_m, 3),          # 3. combined
            (float("inf") if self.replacement_cost_m is None
             else round(self.replacement_cost_m, 3)),   # 4. replacement cost
            tuple(-r for r in self.continuity_rank),    # 5. strongest evidence
            self.upstream_id, self.downstream_id,       # 6. stable ids
        )


@dataclass
class Witness:
    """Proof that the chosen corridor pair describes a real intact trip.

    WHY THIS HAS TO EXIST. The search expands outward from the ports, then
    routes candidate decision-node pairs with the closure REMOVED. Every term
    in the choice rule is about the post-closure world. Nothing in it checks
    that the pre-closure world ever sent anybody between those two nodes
    THROUGH the closure.

    So a pair can have a perfectly good replacement route while the cheapest
    intact route between the same two decision nodes never touched the closure
    at all - in which case the "corridor" describes a diversion nobody needs to
    make. The witness is the intact trip, spelled out arc by arc:

        upstream trail -> entry port -> the intact crossing -> exit port
        -> downstream trail

    and it is only accepted if it is directionally continuous, starts and ends
    at exactly the chosen decision nodes, and genuinely uses a closed arc.
    """

    arc_ids: list[int] = field(default_factory=list)
    from_node: int = -1
    to_node: int = -1
    #: Each arc's target is the next arc's source, all the way along.
    continuous: bool = False
    #: Starts at the chosen upstream node and ends at the chosen downstream one.
    connects_chosen_nodes: bool = False
    #: Uses at least one arc of the DECLARED closure.
    traverses_closure: bool = False
    closure_arcs_used: list[int] = field(default_factory=list)
    #: Every arc resolved to a real row; nothing was assumed.
    all_arcs_resolved: bool = False
    detail: str = ""

    @property
    def valid(self) -> bool:
        return (self.continuous and self.connects_chosen_nodes
                and self.traverses_closure and self.all_arcs_resolved)


@dataclass
class CorridorResult:
    snapshot_id: str
    closure_fingerprint: str
    selected_link_id: int
    profile: Profile

    upstream: list[DecisionPort] = field(default_factory=list)
    downstream: list[DecisionPort] = field(default_factory=list)
    pairs: list[CandidatePair] = field(default_factory=list)
    chosen: CandidatePair | None = None
    explanation: str = ""
    #: decision_points | all_candidates - which tier the chosen pair came from.
    admissibility_level: str = ""
    #: "high" | "low". Low when the chosen pair contains a port further from
    #: the closure than the walk was allowed to travel, so the corridor is
    #: real but is not the near, recognisable place the rule aims for.
    confidence: str = "high"
    #: True when ANY candidate seed sat beyond the expansion bound.
    seed_beyond_search_bound: bool = False
    #: The intact trip the chosen pair is built on, and its validation. A pair
    #: whose witness does not validate is never chosen.
    witness: Witness | None = None
    #: Pairs rejected because their witness failed, with the reason.
    witness_rejections: list[dict] = field(default_factory=list)

    status: str = "OK"
    detail: str = ""
    #: A SEARCH BOUND was touched - the beam pruned, the hop limit ended the
    #: walk, the expansion bound stopped a step. On a real network this is
    #: nearly always true, because that is what a bounded search is; the
    #: bounds are declared in `searchBounds` and this says one of them acted.
    truncated: bool = False
    #: The sharper claim, and the one the HEADLINE gate uses: candidates were
    #: GENERATED and then never evaluated - ports beyond the per-side cap that
    #: were never paired, or pairs beyond the pair cap that were never routed.
    #: Only then could an unevaluated candidate have been the better corridor.
    #:
    #: The distinction has teeth: gating the headline on `truncated` made 382
    #: of 500 sampled national links read "Partial analysis", almost all of
    #: them from routine beam pruning. A warning on 77% of the network teaches
    #: people to ignore it - the geometry-gap lesson again.
    evaluation_truncated: bool = False
    truncation_detail: str = ""
    bounds: dict = field(default_factory=dict)
    stage_ms: dict[str, int] = field(default_factory=dict)
    runtime_ms: int = 0
    model_version: str = CORRIDOR_MODEL_VERSION

    @property
    def resolved(self) -> bool:
        return self.status == "OK"


def candidate_id(snapshot_id: str, closure_fingerprint: str, side: str,
                 trail_key: str) -> str:
    """Stable identity for a decision-port candidate.

    Built from `trail_key` - the node's POSITION plus the AMDS feature id
    and direction of every arc walked - and never from `node_id` or
    `arc_id`.

    Both halves matter. The trail is in there because the same node
    reached by two different roads is two different candidates carrying
    different continuity evidence, and collapsing them would silently
    discard one. The keys are publisher-assigned because this id is the
    LAST tie-break in the choice rule: hashed from `arc_id` it flipped the
    selected pair on three of eight shuffled-input seeds, with not one
    metre of road changing.
    """
    payload = "|".join((
        "corridor-candidate", CORRIDOR_MODEL_VERSION, snapshot_id,
        closure_fingerprint, side, trail_key,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def pair_id(upstream_id: str, downstream_id: str) -> str:
    return hashlib.sha256(
        f"corridor-pair|{CORRIDOR_MODEL_VERSION}|{upstream_id}|{downstream_id}"
        .encode("utf-8")).hexdigest()[:24]


# ------------------------------------------------------------------- search
def select(
    boundary: ClosureBoundary,
    removed_link_ids: Sequence[int],
    removed_arc_ids: Sequence[int],
    *,
    entry_ports: Sequence[Port] | None = None,
    exit_ports: Sequence[Port] | None = None,
    #: The intact movement's own arcs. Without them no candidate pair can be
    #: shown to describe a trip that ever went through the closure, so the
    #: witness check is skipped and `chosen` is not safe to act on.
    witness_arcs: Sequence[int] = (),
    profile: Profile = "car",
    beam_width: int = BEAM_WIDTH,
    max_hops: int = MAX_HOPS,
    max_expansion_outward_m: float = MAX_EXPANSION_OUTWARD_M,
    max_candidates_per_side: int = MAX_CANDIDATES_PER_SIDE,
    max_pairs: int = MAX_PAIRS,
    statement_timeout_ms: int = 20_000,
) -> CorridorResult:
    """Expand candidates on both sides, route every pair, then choose once.

    `entry_ports` and `exit_ports` normally name ONE port each - the two
    crossings of the movement being explained. Anchoring the search to a
    movement is what makes "upstream" and "downstream" mean anything: seeded
    from every port of a two-way segment, both ends of the closure appear on
    both sides, every measurable term ties, and the choice falls through to the
    identifier tie-break, which then flips whenever ids are reassigned. That is
    not a tie-break working; it is a question that was never asked properly.
    """
    t0 = time.perf_counter()
    snap = boundary.snapshot_id
    seeds_in = list(entry_ports if entry_ports is not None
                    else boundary.entry_ports)
    seeds_out = list(exit_ports if exit_ports is not None
                     else boundary.exit_ports)
    out = CorridorResult(
        snapshot_id=snap, closure_fingerprint=boundary.closure_fingerprint,
        selected_link_id=boundary.selected_link_id, profile=profile,
        bounds={
            "beamWidth": beam_width, "maxHops": max_hops,
            "maxExpansionOutwardM": max_expansion_outward_m,
            "seedMayExceedExpansionBound": True,
            "maxCandidatesPerSide": max_candidates_per_side,
            "maxPairs": max_pairs,
            "headingToleranceDeg": HEADING_TOLERANCE_DEG,
        })

    if not seeds_in or not seeds_out:
        out.detail = ("a corridor needs a way in and a way out; this search "
                      f"was seeded with {len(seeds_in)} entry and "
                      f"{len(seeds_out)} exit port(s)")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    t = time.perf_counter()
    up, up_trunc, up_eval = _expand(snap, boundary, seeds_in, "upstream",
                                    removed_link_ids, profile, beam_width,
                                    max_hops, max_expansion_outward_m,
                                    max_candidates_per_side)
    down, down_trunc, down_eval = _expand(snap, boundary, seeds_out,
                                          "downstream", removed_link_ids,
                                          profile, beam_width, max_hops,
                                          max_expansion_outward_m,
                                          max_candidates_per_side)
    out.truncated = up_trunc or down_trunc
    out.evaluation_truncated = up_eval or down_eval
    # A seed is admitted whatever its length; flag it rather than drop it.
    for p in up + down:
        p.beyond_search_bound = p.outward_distance_m > max_expansion_outward_m
    out.seed_beyond_search_bound = any(p.beyond_search_bound for p in up + down)
    out.stage_ms["candidate_expansion"] = int((time.perf_counter() - t) * 1000)
    out.upstream, out.downstream = up, down

    if not up or not down:
        out.detail = ("the outward walk found no candidate decision port on "
                      f"{'the upstream' if not up else 'the downstream'} side")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    # --- pair, bound, and record what the bound refused -------------------
    pairs: list[CandidatePair] = []
    for u in up:
        for d in down:
            pairs.append(CandidatePair(
                pair_id=pair_id(u.candidate_id, d.candidate_id),
                upstream_id=u.candidate_id, downstream_id=d.candidate_id,
                upstream_node=u.node, downstream_node=d.node,
                upstream_outward_m=u.outward_distance_m,
                downstream_outward_m=d.outward_distance_m,
                max_outward_m=max(u.outward_distance_m, d.outward_distance_m),
                combined_outward_m=u.outward_distance_m + d.outward_distance_m,
                both_decision_points=(u.is_decision_point and d.is_decision_point),
                continuity_rank=tuple(
                    a + b for a, b in zip(u.continuity_rank, d.continuity_rank))))

    # Ordered on the bounded terms BEFORE routing, so the pairs that survive a
    # truncation are the nearest ones and not the first ones enumerated.
    pairs.sort(key=lambda p: (round(p.max_outward_m, 3),
                              round(p.combined_outward_m, 3),
                              p.upstream_id, p.downstream_id))
    considered, dropped = pairs[:max_pairs], pairs[max_pairs:]
    for p in dropped:
        p.reason_code = "NOT_EVALUATED_TRUNCATED"
        p.reason = (f"beyond the pair bound of {max_pairs}; not routed and no "
                    "claim is made about it")
    if dropped:
        out.truncated = True
        # Pairs that exist and were never routed: the evaluation itself is
        # incomplete, not merely bounded.
        out.evaluation_truncated = True
        out.truncation_detail = (
            f"{len(dropped)} of {len(pairs)} candidate pair(s) were beyond the "
            f"bound of {max_pairs} and were not routed")

    # --- one edge-set load for every surviving pair ----------------------
    t = time.perf_counter()
    routed = route_many_paths(
        snap, sorted({p.upstream_node for p in considered}),
        sorted({p.downstream_node for p in considered}),
        metric="distance", profile=profile,
        excluded_arcs=sorted(int(a) for a in removed_arc_ids),
        statement_timeout_ms=statement_timeout_ms)
    out.stage_ms["pair_routing"] = int((time.perf_counter() - t) * 1000)

    if not routed.resolved:
        # No pair is declared invalid on the strength of a search that stopped.
        out.status = routed.status
        out.detail = routed.detail or "the corridor pair search did not resolve"
        for p in considered:
            p.reason_code = "SEARCH_UNRESOLVED"
            p.reason = out.detail
        out.pairs = sorted(considered + dropped,
                           key=lambda p: (p.upstream_id, p.downstream_id))
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    for p in considered:
        key = (p.upstream_node, p.downstream_node)
        if p.upstream_node == p.downstream_node:
            p.reason_code = "DEGENERATE_PAIR"
            p.reason = ("the two decision ports are the same node, so the pair "
                        "describes no movement across the closure")
            continue
        if key not in routed.costs:
            p.reason_code = "NO_REPRESENTED_REPLACEMENT"
            p.reason = ("with the closure removed there is no represented route "
                        "from this upstream port to this downstream port")
            continue
        arcs = list(routed.paths.get(key, []))
        p.replacement_arc_ids = arcs
        p.replacement_cost_m = float(routed.costs[key])
        p.valid = True
        p.reason_code = "VALID"
        p.reason = (f"a represented replacement route exists, "
                    f"{p.replacement_cost_m:,.0f} m over {len(arcs)} arc(s)")

    ordered = sorted(considered, key=lambda p: p.sort_key)
    out.pairs = sorted(considered + dropped,
                       key=lambda p: (p.upstream_id, p.downstream_id))

    valid = [p for p in ordered if p.valid]
    if not valid:
        out.detail = (f"none of {len(considered)} routed candidate pair(s) has a "
                      "represented replacement route")
        out.runtime_ms = int((time.perf_counter() - t0) * 1000)
        return out

    # --- the intact witness -----------------------------------------------
    # Every term in the choice rule is about the post-closure world. Nothing in
    # it checks that anybody ever travelled between these two nodes THROUGH the
    # closure in the first place. Pairs whose intact trip cannot be
    # demonstrated are struck out here, before the choice, so an unwitnessed
    # pair can never be selected.
    by_id = {c.candidate_id: c for c in up + down}
    if witness_arcs:
        witnessed: list[CandidatePair] = []
        for p in valid:
            u, d = by_id.get(p.upstream_id), by_id.get(p.downstream_id)
            if u is None or d is None:
                continue
            w = build_witness(snap, u, d, witness_arcs, removed_arc_ids)
            if w.valid:
                p.witness = w
                witnessed.append(p)
            else:
                p.valid = False
                p.reason_code = "NO_INTACT_WITNESS"
                p.reason = w.detail
                out.witness_rejections.append({
                    "pairId": p.pair_id,
                    "upstreamNode": p.upstream_node,
                    "downstreamNode": p.downstream_node,
                    "detail": w.detail,
                })
        valid = witnessed
        if not valid:
            out.detail = (
                "no candidate pair has a demonstrable intact trip through the "
                "closure, so none of them describes a diversion anybody needs "
                "to make")
            out.runtime_ms = int((time.perf_counter() - t0) * 1000)
            return out

    out.chosen, out.admissibility_level, note = _choose(valid)
    out.witness = out.chosen.witness

    chosen_ports = [by_id.get(out.chosen.upstream_id),
                    by_id.get(out.chosen.downstream_id)]
    far = [p for p in chosen_ports if p is not None and p.beyond_search_bound]
    if far:
        out.confidence = "low"
        note = ((note + " ") if note else "") + (
            f"The chosen corridor reaches {max(p.outward_distance_m for p in far):,.0f} m "
            f"from the closure, further than the {max_expansion_outward_m:,.0f} m "
            "the search expands. That is the first junction outside the closure "
            "on a long link, not a place the search wandered to - but it is not "
            "the near, recognisable point this rule aims for.")

    out.explanation = " ".join(
        [_explain(out.chosen, valid, up, down)] + ([note] if note else []))
    out.detail = (f"{len(valid)} of {len(considered)} routed candidate pair(s) "
                  "have a represented replacement route")
    out.runtime_ms = int((time.perf_counter() - t0) * 1000)
    out.stage_ms["total"] = out.runtime_ms
    return out


def build_witness(snapshot_id: str, upstream: DecisionPort,
                  downstream: DecisionPort, movement_arcs: Sequence[int],
                  declared_arc_ids: Sequence[int]) -> Witness:
    """Assemble and validate the intact trip behind one corridor pair.

    Arc ORDER matters and the two trails run in opposite senses. An upstream
    trail is recorded outward from the closure - port arc first, then each step
    further away - so travelling it takes the reverse. A downstream trail is
    already in travel order.
    """
    w = Witness(from_node=upstream.node, to_node=downstream.node)
    arcs = list(reversed(list(upstream.arc_trail))) + list(movement_arcs) \
        + list(downstream.arc_trail)
    w.arc_ids = arcs
    if not arcs:
        w.detail = "no arcs: there is no intact trip to witness"
        return w

    rows = db.query(
        "SELECT arc_id, source, target FROM arcs "
        " WHERE snapshot_id=%s AND arc_id = ANY(%s)",
        (snapshot_id, sorted({int(a) for a in arcs})))
    ends = {int(r["arc_id"]): (int(r["source"]), int(r["target"])) for r in rows}
    w.all_arcs_resolved = all(int(a) in ends for a in arcs)
    if not w.all_arcs_resolved:
        w.detail = "at least one arc of the witness is not in the graph"
        return w

    w.continuous = all(ends[int(a)][1] == ends[int(b)][0]
                       for a, b in zip(arcs, arcs[1:]))
    w.connects_chosen_nodes = (ends[int(arcs[0])][0] == upstream.node
                               and ends[int(arcs[-1])][1] == downstream.node)
    declared = {int(a) for a in declared_arc_ids}
    w.closure_arcs_used = sorted({int(a) for a in arcs if int(a) in declared})
    w.traverses_closure = bool(w.closure_arcs_used)

    if w.valid:
        w.detail = (
            f"an intact trip of {len(arcs)} arc(s) runs from node "
            f"{upstream.node} to node {downstream.node} and uses "
            f"{len(w.closure_arcs_used)} closed arc(s) on the way")
    else:
        why = []
        if not w.continuous:
            why.append("it is not directionally continuous")
        if not w.connects_chosen_nodes:
            why.append("it does not start and end at the chosen nodes")
        if not w.traverses_closure:
            why.append("it never uses a closed arc, so no trip between these "
                       "nodes was interrupted")
        w.detail = "rejected: " + "; ".join(why)
    return w


def _choose(valid: list[CandidatePair]) -> tuple[CandidatePair, str, str]:
    """A junction pair if one is available; otherwise the best pair there is.

    A DECISION port is somewhere a driver has a choice - three or more open
    links meet there. "The detour starts here" naming a point mid-road, where
    nobody can turn, is not something a reader can act on, so junction pairs
    are preferred.

    Preferred, not required. Requiring them was the first attempt and it
    returned NOTHING on a fixture with a perfectly good frontage-road
    detour: once the closure is removed, the two junctions either side of it
    were the same node, so the only admissible pair was degenerate. A rule that
    answers "no corridor" when a corridor plainly exists is worse than one that
    names a through point and says so.

    `valid` is already in the lexicographic order, so both tiers just take the
    first survivor and neither reopens the ranking.
    """
    junction_pairs = [p for p in valid if p.both_decision_points]
    if junction_pairs:
        return junction_pairs[0], "decision_points", ""
    return valid[0], "all_candidates", (
        "No pair of junctions either side of the closure had a replacement "
        "route between them, so the corridor is reported between through "
        "points: these are where the diversion runs, not necessarily places a "
        "driver can turn.")


def _explain(chosen: CandidatePair, valid: list[CandidatePair],
             up: list[DecisionPort], down: list[DecisionPort]) -> str:
    """Which rule decided it - the FIRST one that separated the winner.

    Read off the same key the sort used, so the sentence cannot drift away from
    the rule it describes.
    """
    by_id = {c.candidate_id: c for c in up + down}
    u = by_id.get(chosen.upstream_id)
    d = by_id.get(chosen.downstream_id)
    where = (f"{_name(u)} to {_name(d)}, "
             f"{chosen.upstream_outward_m:,.0f} m and "
             f"{chosen.downstream_outward_m:,.0f} m out from the closure")

    if len(valid) == 1:
        return (f"{where}. It is the only candidate pair with a represented "
                "replacement route.")

    key, other = chosen.sort_key, valid[1].sort_key
    labels = ("a represented replacement route exists",
              "its farther side is the nearest to the closure of any candidate",
              "it has the least combined outward distance",
              "it has the cheapest replacement route",
              "its road-continuity evidence is the strongest")
    for i, label in enumerate(labels):
        if key[i] != other[i]:
            return f"{where}. Chosen because {label}."
    return (f"{where}. Every measurable term tied with at least one other "
            "pair, so the choice fell to the stable identifier - which is a "
            "hash of identifiers, not a row order.")


def _name(p: DecisionPort | None) -> str:
    if p is None:
        return "an unidentified port"
    return p.road_name or p.route_designation or f"node {p.node}"


# ------------------------------------------------------------- beam search
def _expand(snapshot_id: str, boundary: ClosureBoundary, seeds: Sequence[Port],
            side: Side, removed_link_ids: Sequence[int], profile: Profile,
            beam_width: int, max_hops: int, max_expansion_outward_m: float,
            max_candidates: int) -> tuple[list[DecisionPort], bool]:
    """Walk outward from the boundary, carrying several candidates at once.

    `upstream` walks BACKWARDS along arcs from an entry port's outside node -
    the places traffic arrives from. `downstream` walks forwards from an exit
    port's outside node. Getting that the wrong way round on a one-way system
    would build a corridor out of roads nobody can use to reach the closure.
    """
    closed_links = sorted(int(x) for x in removed_link_ids)
    closure_nodes = set(boundary.closure_nodes)

    #: (node, outward_m, hops, trail, rank, seed_port, seed_node, link attrs)
    frontier: list[dict] = []
    seen_states: set[tuple[int, tuple[int, ...]]] = set()
    found: dict[str, DecisionPort] = {}

    # Outward distance is measured FROM THE CLOSURE, so the first candidate -
    # the port's own outside node - already sits one arc out. Starting the
    # clock at the outside node instead would report a decision port as being
    # zero metres from a closure it is a kilometre away from.
    seed_cost = _arc_lengths(snapshot_id, [p.arc_id for p in seeds])
    seed_akey = stableid.arc_keys(snapshot_id, [p.arc_id for p in seeds])
    seed_nkey = stableid.node_keys(snapshot_id, [p.outside_node for p in seeds])
    for p in seeds:
        akeys = (seed_akey.get(p.arc_id, str(p.arc_id)),)
        nkey = seed_nkey.get(p.outside_node, str(p.outside_node))
        st = {
            "node": p.outside_node, "outward": seed_cost.get(p.arc_id, 0.0),
            "hops": 0, "trail": (int(p.arc_id),), "akeys": akeys, "nkey": nkey,
            "rank": (0, 0, 0, 0, 0, 0), "codes": (),
            "port": p.port_id, "boundary_node": p.closure_node,
            "link": {"link_id": p.link_id, "road_name": p.road_name,
                     "route_designation": p.route_designation,
                     "rca_code": 1 if p.is_state_highway else None,
                     "model_asset_type": p.road_class},
            # Bearing of the port arc, pointing OUTWARD from the closure.
            "bearing": None,
        }
        frontier.append(st)
        # The seed node is itself a candidate. It is the FIRST place outside
        # the closure, so where it is a junction it is very often the right
        # answer, and a search that could only return nodes further out would
        # never be able to say so.
        tkey = stableid.trail_key(nkey, akeys)
        cid = candidate_id(snapshot_id, boundary.closure_fingerprint, side, tkey)
        found[cid] = DecisionPort(
            candidate_id=cid, side=side, node=p.outside_node,
            outward_distance_m=st["outward"], hops=0,
            arc_trail=[int(p.arc_id)], origin_port_id=p.port_id,
            origin_boundary_node=p.closure_node, stable_key=tkey,
            continuity_rank=st["rank"],
            evidence=[], road_name=p.road_name,
            route_designation=p.route_designation,
            is_state_highway=p.is_state_highway)

    # ONE candidate per node, and it is the best-ranked way of reaching it.
    #
    # Keying candidates on (node, trail) instead - which the first draft did -
    # lets the beam walk laps of a loop and file each lap as a fresh candidate.
    # On a nine-link fixture 800 m across it produced twenty candidates a side
    # reaching "1,500 m outward", which is not a place; it is the same corner
    # counted three times.
    reached: set[int] = {s["node"] for s in frontier}
    truncated = False
    for _ in range(max_hops):
        if not frontier:
            break
        rows = _step_rows(snapshot_id, [s["node"] for s in frontier], side,
                          closed_links, profile)
        nxt: list[dict] = []
        for st in frontier:
            branches = rows.get(st["node"], ())
            for r in branches:
                nxt_node = int(r["next_node"])
                # Never walk back into the closure: the corridor is the road
                # OUTSIDE it, and a candidate inside would be routed from a
                # node the closure has already taken out.
                if nxt_node in closure_nodes or nxt_node in reached:
                    continue
                trail = st["trail"] + (int(r["arc_id"]),)
                outward = st["outward"] + float(r["cost_distance_m"])
                if outward > max_expansion_outward_m:
                    truncated = True
                    continue
                cont = _continuity(st, r, side, len(branches))
                rank = tuple(a + b for a, b in zip(st["rank"], cont.rank))
                # Publisher-named step: the AMDS feature and direction walked,
                # and the position of the node arrived at. Both come from the
                # row already fetched, so this costs no extra query.
                nxt.append({
                    "node": nxt_node, "outward": outward, "hops": st["hops"] + 1,
                    "trail": trail, "rank": rank,
                    "akeys": st["akeys"] + (f"{r['amds_id']}|{r['direction']}",),
                    "nkey": _node_key_from_row(r, side),
                    "codes": st["codes"] + cont.codes,
                    "port": st["port"], "boundary_node": st["boundary_node"],
                    "link": r, "bearing": _bearing(r, side),
                })

        # THE BEAM. Strongest continuity first, then nearest, then the stable
        # trail key. Sorted BEFORE the per-node dedup, so which arrival at a
        # node wins is decided by the rule and not by which row arrived first.
        for s in nxt:
            s["tkey"] = stableid.trail_key(s["nkey"], s["akeys"])
        nxt.sort(key=lambda s: (tuple(-x for x in s["rank"]),
                                round(s["outward"], 3), s["tkey"]))
        deduped: list[dict] = []
        for s in nxt:
            if s["node"] in reached:
                continue
            reached.add(s["node"])
            deduped.append(s)
            cid = candidate_id(snapshot_id, boundary.closure_fingerprint, side,
                               s["tkey"])
            r = s["link"]
            found[cid] = DecisionPort(
                candidate_id=cid, side=side, node=s["node"],
                outward_distance_m=s["outward"], hops=s["hops"],
                arc_trail=list(s["trail"]), origin_port_id=s["port"],
                origin_boundary_node=s["boundary_node"],
                stable_key=s["tkey"],
                continuity_rank=s["rank"], evidence=sorted(set(s["codes"])),
                road_name=r.get("road_name"),
                route_designation=r.get("route_designation"),
                is_state_highway=(r.get("rca_code") == 1))

        if len(deduped) > beam_width:
            truncated = True
        frontier = deduped[:beam_width]

    ports = list(found.values())
    _annotate_degree(snapshot_id, ports, closed_links, profile)

    # Nearest first, then strongest evidence, then the stable trail key.
    # Deterministic throughout, and invariant under re-ingest.
    ports.sort(key=lambda p: (round(p.outward_distance_m, 3),
                              tuple(-x for x in p.continuity_rank),
                              p.stable_key))
    kept, rest = ports[:max_candidates], ports[max_candidates:]
    for p in rest:
        p.included = False
        p.reason_code = "NOT_EVALUATED_TRUNCATED"
        p.reason = (f"beyond the bound of {max_candidates} candidate(s) per "
                    "side; not paired and no claim is made about it")
    for p in kept:
        p.reason_code = "CANDIDATE"
        p.reason = (f"reached {p.outward_distance_m:,.0f} m and {p.hops} hop(s) "
                    f"outward; node degree {p.node_degree}"
                    + (" - a genuine choice of route exists here"
                       if p.is_decision_point else
                       " - a through point, not a place to decide"))
    # Two different facts, returned separately. `truncated` says a SEARCH
    # BOUND was touched - the beam pruned, the hop limit ended the walk, the
    # expansion bound stopped a step. Every bounded search does that on almost
    # every real closure, and it is declared in `searchBounds`. `rest` is the
    # sharper claim: candidates were GENERATED and then never evaluated, so an
    # unevaluated candidate could have been the better corridor.
    return kept, truncated, bool(rest)


def _arc_lengths(snapshot_id: str, arc_ids: Sequence[int]) -> dict[int, float]:
    ids = sorted({int(a) for a in arc_ids})
    if not ids:
        return {}
    rows = db.query(
        "SELECT arc_id, cost_distance_m FROM arcs "
        " WHERE snapshot_id=%s AND arc_id = ANY(%s)", (snapshot_id, ids))
    return {int(r["arc_id"]): float(r["cost_distance_m"]) for r in rows}


def _step_rows(snapshot_id: str, nodes: Sequence[int], side: Side,
               closed_links: Sequence[int], profile: Profile
               ) -> dict[int, list[dict]]:
    """Every continuing arc from each frontier node, in one query.

    Grouped in Python by the node the walk is standing on, and ordered by arc
    id, so the candidate set does not depend on the plan the database chose.
    """
    from .ports import _MODE_COLUMN

    mode = _MODE_COLUMN[profile]
    join_col, next_col = (("a.target", "a.source") if side == "upstream"
                          else ("a.source", "a.target"))
    rows = db.query(
        f"""
        SELECT a.arc_id, a.link_id, a.direction, a.cost_distance_m,
               {join_col} AS from_node, {next_col} AS next_node,
               l.amds_id, l.rca_code, l.model_asset_type,
               coalesce(dn.display_name, l.road_name) AS road_name,
               dn.route_designation,
               ST_X(ns.geom_2193) AS sx, ST_Y(ns.geom_2193) AS sy,
               ST_X(nt.geom_2193) AS tx, ST_Y(nt.geom_2193) AS ty
          FROM arcs a
          JOIN links l ON l.snapshot_id = a.snapshot_id AND l.link_id = a.link_id
     LEFT JOIN link_display_names dn
            ON dn.snapshot_id = l.snapshot_id AND dn.link_id = l.link_id
          JOIN nodes ns ON ns.snapshot_id = a.snapshot_id AND ns.node_id = a.source
          JOIN nodes nt ON nt.snapshot_id = a.snapshot_id AND nt.node_id = a.target
         WHERE a.snapshot_id = %s
           AND a.{mode}
           AND NOT (a.link_id = ANY(%s))
           AND {join_col} = ANY(%s)
         ORDER BY a.arc_id
        """,
        (snapshot_id, closed_links or [-1], sorted(set(int(n) for n in nodes))))
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(int(r["from_node"]), []).append(r)
    return grouped


def _continuity(state: dict, row: dict, side: Side,
                branch_count: int) -> Continuity:
    prev = state["link"]
    name_a = (prev.get("road_name") or "").strip().casefold()
    name_b = (row.get("road_name") or "").strip().casefold()
    des_a = (prev.get("route_designation") or "").strip().casefold()
    des_b = (row.get("route_designation") or "").strip().casefold()

    angle = None
    heading_ok = False
    if state["bearing"] is not None:
        angle = _turn_angle(state["bearing"], _bearing(row, side))
        heading_ok = angle <= HEADING_TOLERANCE_DEG

    return Continuity(
        route_designation_match=bool(des_a) and des_a == des_b,
        road_name_match=bool(name_a) and name_a == name_b,
        state_highway_continues=(prev.get("rca_code") == 1
                                 and row.get("rca_code") == 1),
        road_class_match=(prev.get("model_asset_type") is not None
                          and prev.get("model_asset_type")
                          == row.get("model_asset_type")),
        # Exactly one way onward means there was no choice to get wrong. That
        # is the strongest continuity evidence there is, and it is free.
        degree_two=(branch_count == 1),
        heading_continuous=heading_ok,
        turn_angle_deg=angle,
    )


def _node_key_from_row(row: dict, side: Side) -> str:
    """Position key for the node this step ARRIVES at.

    The step query already returns both endpoint coordinates, so the walk never
    pays a lookup for this. Which pair is the arrival depends on which way the
    walk runs: upstream steps land on the arc's source, downstream on its
    target.
    """
    x, y = ((row["sx"], row["sy"]) if side == "upstream"
            else (row["tx"], row["ty"]))
    dp = stableid.NODE_KEY_DP
    return f"{float(x):.{dp}f},{float(y):.{dp}f}"


def _bearing(row: dict, side: str) -> float | None:
    """Bearing of an arc in the direction the WALK travels, degrees."""
    try:
        sx, sy = float(row["sx"]), float(row["sy"])
        tx, ty = float(row["tx"]), float(row["ty"])
    except (KeyError, TypeError):
        return None
    if row.get("direction") == "reverse":
        sx, sy, tx, ty = tx, ty, sx, sy
    if side == "upstream":
        sx, sy, tx, ty = tx, ty, sx, sy
    if (tx - sx) == 0 and (ty - sy) == 0:
        return None
    return math.degrees(math.atan2(tx - sx, ty - sy)) % 360.0


def _turn_angle(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 180.0
    d = abs((b - a + 180.0) % 360.0 - 180.0)
    return d


def _annotate_degree(snapshot_id: str, ports: list[DecisionPort],
                     closed_links: Sequence[int], profile: Profile) -> None:
    """How many open links meet at each candidate node.

    Degree decides `is_decision_point`, which is what separates "a junction a
    driver would turn at" from "a point in the middle of a road". Counted over
    LINKS rather than arcs so a two-way road counts once - hence the DISTINCT.

    Asked of `arcs` and not of `links`, and as two indexed lookups UNIONed
    rather than one join with an OR. That is not stylistic. `links` carries no
    index on `source_node` or `target_node`, and

        (l.source_node = n.node_id OR l.target_node = n.node_id)

    cannot use one even if it did, so this query was a sequential scan of
    375,696 rows and cost 153 ms - the single largest cost in a corridor
    search, larger than the whole beam walk. `arcs` has btree indexes on
    (snapshot_id, source) and (snapshot_id, target), and splitting the
    disjunction into two ANY() lookups lets both be used.

    Every link the profile can use has at least one arc, and a two-way link
    appears once as a source and once as a target, so the DISTINCT count over
    arcs equals the link degree exactly.
    """
    if not ports:
        return
    from .ports import _MODE_COLUMN

    mode = _MODE_COLUMN[profile]
    nodes = sorted({p.node for p in ports})
    closed = list(closed_links) or [-1]
    rows = db.query(
        f"""
        SELECT node_id, count(DISTINCT link_id) AS degree FROM (
            SELECT source AS node_id, link_id FROM arcs
             WHERE snapshot_id = %s AND source = ANY(%s)
               AND {mode} AND NOT (link_id = ANY(%s))
            UNION ALL
            SELECT target AS node_id, link_id FROM arcs
             WHERE snapshot_id = %s AND target = ANY(%s)
               AND {mode} AND NOT (link_id = ANY(%s))
        ) q GROUP BY node_id ORDER BY node_id
        """,
        (snapshot_id, nodes, closed, snapshot_id, nodes, closed))
    degree = {int(r["node_id"]): int(r["degree"]) for r in rows}
    for p in ports:
        p.node_degree = degree.get(p.node, 0)
        p.is_decision_point = p.node_degree >= 3


# --------------------------------------------------------------- API shape
def port_dict(p: DecisionPort) -> dict:
    return {
        "candidateId": p.candidate_id,
        "side": p.side,
        "node": p.node,
        "outwardDistanceM": round(p.outward_distance_m, 1),
        "hops": p.hops,
        "arcTrail": p.arc_trail,
        "stableKey": p.stable_key,
        "originPortId": p.origin_port_id,
        "originBoundaryNode": p.origin_boundary_node,
        "continuityRank": list(p.continuity_rank),
        "evidence": p.evidence,
        "roadName": p.road_name,
        "routeDesignation": p.route_designation,
        "isStateHighway": p.is_state_highway,
        "nodeDegree": p.node_degree,
        "isDecisionPoint": p.is_decision_point,
        "beyondSearchBound": p.beyond_search_bound,
        "included": p.included,
        "reasonCode": p.reason_code,
        "reason": p.reason,
    }


def pair_dict(p: CandidatePair) -> dict:
    return {
        "pairId": p.pair_id,
        "upstreamId": p.upstream_id,
        "downstreamId": p.downstream_id,
        "upstreamNode": p.upstream_node,
        "downstreamNode": p.downstream_node,
        "upstreamOutwardM": round(p.upstream_outward_m, 1),
        "downstreamOutwardM": round(p.downstream_outward_m, 1),
        "maxOutwardM": round(p.max_outward_m, 1),
        "combinedOutwardM": round(p.combined_outward_m, 1),
        "replacementCostM": (None if p.replacement_cost_m is None
                             else round(p.replacement_cost_m, 1)),
        "continuityRank": list(p.continuity_rank),
        "bothDecisionPoints": p.both_decision_points,
        "replacementArcCount": len(p.replacement_arc_ids),
        "valid": p.valid,
        "reasonCode": p.reason_code,
        "reason": p.reason,
    }


def as_dict(c: CorridorResult) -> dict:
    return {
        "corridorModelVersion": c.model_version,
        "status": c.status,
        "resolved": c.resolved,
        "detail": c.detail,
        "vehicleProfile": c.profile,
        "searchBounds": c.bounds,
        "truncated": c.truncated,
        "evaluationTruncated": c.evaluation_truncated,
        "truncationDetail": c.truncation_detail,
        "upstreamCandidates": [port_dict(p) for p in c.upstream],
        "downstreamCandidates": [port_dict(p) for p in c.downstream],
        "candidatePairs": [pair_dict(p) for p in c.pairs],
        "candidatePairCount": len(c.pairs),
        "validPairCount": sum(1 for p in c.pairs if p.valid),
        "chosenPair": pair_dict(c.chosen) if c.chosen else None,
        "admissibilityLevel": c.admissibility_level,
        "confidence": c.confidence,
        "seedBeyondSearchBound": c.seed_beyond_search_bound,
        "witness": (None if c.witness is None else {
            "arcIds": c.witness.arc_ids,
            "fromNode": c.witness.from_node,
            "toNode": c.witness.to_node,
            "continuous": c.witness.continuous,
            "connectsChosenNodes": c.witness.connects_chosen_nodes,
            "traversesClosure": c.witness.traverses_closure,
            "closureArcsUsed": c.witness.closure_arcs_used,
            "valid": c.witness.valid,
            "detail": c.witness.detail,
        }),
        "witnessRejections": c.witness_rejections,
        "explanation": c.explanation,
        # RAMM is named here so its ABSENCE is a stated decision rather than an
        # omission a reader has to notice.
        "continuityEvidenceUsed": [
            "ROUTE_DESIGNATION_CONTINUES", "ROAD_NAME_CONTINUES",
            "DEGREE_TWO_NO_CHOICE", "STATE_HIGHWAY_CONTINUES",
            "ROAD_CLASS_CONTINUES", "HEADING_CONTINUOUS",
        ],
        "continuityEvidenceExcluded": [
            {"evidence": "RAMM_CORRIDOR_IDENTITY",
             "reason": "licence not cleared (docs/LICENSING.md), absent from "
                       "this database, and its corridor spans hundreds of "
                       "kilometres so it would separate nothing"},
        ],
        "stageMs": c.stage_ms,
        "runtimeMs": c.runtime_ms,
    }
