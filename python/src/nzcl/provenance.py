"""What a POSSIBLE-graph route actually assumed, junction by junction.

Why this exists
---------------
`topology.CROSSING_POLICIES` offers a third graph. `possible` nodes the
UNRESOLVED crossings as well as the AT_GRADE ones, and it is a SENSITIVITY
INSTRUMENT: it answers "would this result change if the crossings we cannot
resolve turned out to be junctions?" That question is only worth asking if the
answer says WHICH crossings, and HOW MANY of them the answer leaned on.

A result that hinges on ONE unresolved junction and a result that needs FOUR
stacked assumptions must not read identically. The first is a single
checkable claim - one aerial photograph settles it, and the crossing has a
coordinate this module reports so somebody can go and look. The second is a
chain, and the chance that every link in it holds is the product of four
uncertainties nobody has measured. Reporting both as "possible graph" would
flatten a difference that decides whether the number is worth acting on.

What "relied on" means here, and what it refuses to mean
--------------------------------------------------------
A crossing is RELIED ON only when the route passes THROUGH it: the crossing
point is the shared node between two consecutive links of the route, and those
two links come from the crossing's two source features - so the route arrives
on `source_a` and leaves on `source_b`, or the other way round.

Proximity is explicitly NOT enough, and this is the whole reason the join is
written the way it is. A rural crossroads sits within a metre of every route
that drives past it on either road, and a route that runs STRAIGHT THROUGH on
one road would be identical if the crossing had never been noded - the two
halves of that road would simply still be one link. Counting such a crossing
would inflate the assumption count on exactly the routes that made no
assumption at all, which is worse than not reporting anything: it would train
a reader to ignore the figure.

The correspondingly expensive part is that this needs the route's ORDER and
the links' endpoints, not a spatial buffer. That cost is accepted. Getting the
join right matters more than getting it fast; a buffer query would be one line
and would answer a different question.

What this module does NOT do
----------------------------
It does not classify crossings - `nzcl.crossings` does that, and this module
reads its verdict without revisiting it. It does not decide policy: it reports
what a route assumed, and refuses to describe a POSSIBLE-graph route as
canonical anywhere in its output.

DECISIVENESS, AND THE ONE PLACE THIS IS ALLOWED TO SAY "I DID NOT TEST THAT"
----------------------------------------------------------------------------
`changed_by_one_crossing` asks whether removing any SINGLE relied-on crossing
would change the outcome.

  * zero relied on          - false. Nothing to remove.
  * exactly one relied on   - TRUE BY CONSTRUCTION. Removing it removes the
                              only speculative node the route used, so either
                              the route changes or it never needed the node,
                              and it did need it: it passed through it.
  * two or more relied on   - a real question, and the only honest way to
                              answer it is to route again with that one
                              crossing's node removed and compare.

"The outcome" is the CANONICAL ANSWER - minimum represented-network distance -
and not the sequence of links. Those differ, and the difference is the whole
point: a route driven through a node necessarily changes its link sequence
when that node goes, so comparing sequences would report every relied-on
crossing as decisive and the field would carry no information at all. What a
reader wants to know is whether the NUMBER moves. Where an equal-cost way
round exists, it does not.

Re-routing needs a graph, and this module deliberately holds none - it works
on a list of links and a list of crossing rows so it can be tested without a
database. So the re-run arrives as a HOOK: pass `reroute`, a callable taking
the set of crossing ids to suppress and returning the resulting distance, and
every relied-on crossing is tested individually.

WITH NO HOOK, DECISIVENESS IS NOT TESTED AND IS NOT GUESSED. The count-based
answer is reported and `decisivenessMethod` says `UNTESTED_COUNT_ONLY` in the
serialised output, so a reader can tell the difference between "no single
crossing is decisive" and "nobody checked". Returning a plausible-looking
boolean with nothing behind it is the failure this field exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from . import db
from .crossings import UNRESOLVED
from .geo import nztm_to_lonlat

Coord = tuple[float, float]

#: The three answers, and there are exactly three. A fourth would be a severity
#: judgement, and this module measures rather than judges.
ROBUST = "ROBUST"
ONE_UNRESOLVED_CROSSING = "ONE_UNRESOLVED_CROSSING"
MULTIPLE_UNRESOLVED_CROSSINGS = "MULTIPLE_UNRESOLVED_CROSSINGS"

#: Raised on any replacement path that relied on unresolved topology, and only
#: on those. The web inspector renders arbitrary flags, so the flag is how this
#: reaches a reader who never opens the provenance block.
RELIES_ON_UNRESOLVED_CROSSING = "RELIES_ON_UNRESOLVED_CROSSING"

#: How close the shared endpoint of two consecutive route links has to be to a
#: recorded crossing point before they are the same junction.
#:
#: The same 50 mm `split_at_junctions` cuts with, and for the same reason: the
#: cut coordinate IS the crossing coordinate, so this tolerance absorbs float
#: round-tripping through the database and nothing else. Widening it would let
#: a route that turns at a genuine junction 2 m away inherit the doubt of a
#: crossing it never touched.
JUNCTION_MATCH_M = 0.05

#: How `changed_by_one_crossing` was arrived at. Reported, because the same
#: boolean means different things depending on this.
DECISIVENESS_NONE = "NO_UNRESOLVED_CROSSINGS"
DECISIVENESS_SINGLE = "SINGLE_CROSSING_BY_CONSTRUCTION"
DECISIVENESS_REROUTED = "REROUTED_WITHOUT_EACH_CROSSING"
DECISIVENESS_UNTESTED = "UNTESTED_COUNT_ONLY"

#: Given the crossing ids whose nodes to take back out, the cost of the route
#: that results - metres, the canonical answer - or None when no route remains.
#:
#: Distance and not a link sequence. See the module docstring: a route driven
#: through a node always changes its links when that node goes, so a sequence
#: comparison would call every crossing decisive and mean nothing.
Reroute = Callable[[frozenset[int]], float | None]

#: Two costs this close are the same answer. Absorbs float summation order in
#: the router, nothing else; a real alternative differs by metres.
COST_EPSILON_M = 1e-6


@dataclass(frozen=True)
class CrossingRecord:
    """One row of the `crossings` table, as this module needs it.

    Deliberately a plain value rather than a database cursor: the join below is
    the part that can be wrong, and it has to be testable on a synthetic grid
    with no PostGIS anywhere near it.
    """

    crossing_id: int
    #: AMDS SOURCE FEATURE ids, not link ids. Link ids are positional and are
    #: reassigned on every ingest; `links.closure_group_id` is what matches.
    source_a: str
    source_b: str
    x: float
    y: float
    disposition: str = UNRESOLVED
    #: Was this crossing cut in the snapshot the route was found on? A crossing
    #: that was NOT cut cannot have been relied on: the node does not exist.
    noded: bool = True
    reason: str = ""
    confidence: str = "MEDIUM"
    angle_deg: float | None = None
    place_id: int | None = None

    @property
    def sources(self) -> frozenset[str]:
        return frozenset({self.source_a, self.source_b})


@dataclass(frozen=True)
class RouteLink:
    """One link of a route, in travel order.

    Only the endpoints are carried. The join asks where two consecutive links
    MEET, and the interior vertices cannot answer that - a crossing point is a
    node by construction, because `split_at_junctions` cut both roads there.
    """

    link_id: int
    closure_group_id: str
    endpoints: tuple[Coord, Coord]


@dataclass
class Reliance:
    """One unresolved crossing the route actually drove through."""

    crossing: CrossingRecord
    #: The two links, in travel order, the route passed between here.
    from_link_id: int
    to_link_id: int
    #: True when removing THIS crossing alone changes the route. None when no
    #: re-route hook was supplied, which is not the same as False.
    decisive: bool | None = None


@dataclass
class Provenance:
    robustness: str
    relied_on: list[Reliance] = field(default_factory=list)
    changed_by_one_crossing: bool = False
    requires_multiple_assumptions: bool = False
    decisiveness_method: str = DECISIVENESS_NONE
    detail: str = ""

    @property
    def speculative_junction_count(self) -> int:
        return len(self.relied_on)


# --------------------------------------------------------------- the join
def relied_upon(route: Sequence[RouteLink],
                crossings: Sequence[CrossingRecord],
                *, tolerance_m: float = JUNCTION_MATCH_M) -> list[Reliance]:
    """Which of `crossings` this route actually passed through.

    Refuses to report a crossing that merely lies near the route. Two things
    must both hold at one point: the route TURNS from one of the crossing's
    source features onto the other there, and the shared node between those two
    consecutive links is the crossing's own coordinate.

    A route running straight through a noded crossroads on ONE road satisfies
    neither - both its links come from the same source feature - and is
    correctly reported as relying on nothing, because without the node those
    two links would simply still be one link and the route would be identical.
    """
    # Index on the unordered source PAIR. The `crossings` table records
    # (source_a, source_b) in detection order, which has nothing to do with
    # travel direction, so matching on the ordered pair would find a crossing
    # only when the route happened to drive it the way it was detected.
    by_pair: dict[frozenset[str], list[CrossingRecord]] = {}
    for x in crossings:
        if x.disposition != UNRESOLVED or not x.noded:
            # Not speculative. AT_GRADE is in the canonical graph and carries
            # no assumption this module is reporting on; a crossing that was
            # not noded created no node for any route to use.
            continue
        if x.source_a == x.source_b:
            # A road crossing itself is never noded under any policy, but the
            # pair key would collapse to a single-element set and match a route
            # running straight through on that one road. Refused explicitly
            # rather than relied on being unreachable.
            continue
        by_pair.setdefault(x.sources, []).append(x)

    if not by_pair:
        return []

    tol2 = tolerance_m * tolerance_m
    out: list[Reliance] = []
    seen: set[int] = set()

    for first, second in zip(route, route[1:]):
        pair = frozenset({first.closure_group_id, second.closure_group_id})
        candidates = by_pair.get(pair)
        if not candidates:
            continue

        junctions = _shared_points(first, second, tol2)
        if not junctions:
            continue

        for x in candidates:
            if x.crossing_id in seen:
                continue
            if any((jx - x.x) ** 2 + (jy - x.y) ** 2 <= tol2
                   for jx, jy in junctions):
                seen.add(x.crossing_id)
                out.append(Reliance(crossing=x,
                                    from_link_id=first.link_id,
                                    to_link_id=second.link_id))
    return out


def _shared_points(first: RouteLink, second: RouteLink,
                   tol2: float) -> list[Coord]:
    """Where two consecutive route links meet.

    Both endpoints of each are tried because a route carries no statement of
    which way round a link's geometry is digitised, and inferring it from the
    previous link would propagate one wrong guess along the whole route.
    """
    found: list[Coord] = []
    for a in first.endpoints:
        for b in second.endpoints:
            if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 <= tol2:
                found.append(a)
    return found


# ------------------------------------------------------- the classification
def analyse(route: Sequence[RouteLink],
            crossings: Sequence[CrossingRecord],
            *,
            reroute: Reroute | None = None,
            tolerance_m: float = JUNCTION_MATCH_M) -> Provenance:
    """Everything this module can say about one route, in one value.

    `reroute` is the decisiveness hook described in the module docstring. When
    it is supplied the BASELINE is taken from `reroute(frozenset())` rather
    than from `route`, so the comparison is between two outputs of the same
    router and cannot fail on a tie the caller happened to break differently.

    Without it, decisiveness for two or more crossings is NOT tested and NOT
    guessed: the count-based answer is reported and the method says so.
    """
    relied = relied_upon(route, crossings, tolerance_m=tolerance_m)
    n = len(relied)

    if n == 0:
        return Provenance(
            robustness=ROBUST,
            relied_on=[],
            changed_by_one_crossing=False,
            requires_multiple_assumptions=False,
            decisiveness_method=DECISIVENESS_NONE,
            detail=("this route passes through no unresolved crossing, so it "
                    "is the same route the canonical graph gives"))

    if n == 1:
        relied[0].decisive = True
        return Provenance(
            robustness=ONE_UNRESOLVED_CROSSING,
            relied_on=relied,
            changed_by_one_crossing=True,
            requires_multiple_assumptions=False,
            decisiveness_method=DECISIVENESS_SINGLE,
            detail=_one_crossing_detail(relied[0]))

    method = DECISIVENESS_UNTESTED
    if reroute is not None:
        method = DECISIVENESS_REROUTED
        baseline = reroute(frozenset())
        for r in relied:
            r.decisive = _differs(
                reroute(frozenset({r.crossing.crossing_id})), baseline)

    decisive = [r for r in relied if r.decisive]
    changed_by_one = bool(decisive)
    return Provenance(
        robustness=MULTIPLE_UNRESOLVED_CROSSINGS,
        relied_on=relied,
        changed_by_one_crossing=changed_by_one,
        # The complement, and the point of the pair: a route no single crossing
        # decides is not therefore robust. It is a route standing on several
        # assumptions at once, and none of them can be checked in isolation.
        requires_multiple_assumptions=not changed_by_one,
        decisiveness_method=method,
        detail=_many_crossings_detail(relied, decisive, method))


def _differs(cost: float | None, baseline: float | None) -> bool:
    """Did taking one crossing out move the answer?

    A route appearing or disappearing counts, which is why None is compared
    rather than coerced to a number: "there is no longer a represented route"
    is the largest change this can report, not a missing value.
    """
    if cost is None or baseline is None:
        return cost is not baseline
    return abs(float(cost) - float(baseline)) > COST_EPSILON_M


def _one_crossing_detail(r: Reliance) -> str:
    x = r.crossing
    return (
        f"this route turns from {x.source_a} onto {x.source_b} at "
        f"({x.x:.1f}, {x.y:.1f}), a crossing the classifier could not resolve "
        f"({x.reason}, {x.confidence} confidence). It is a single checkable "
        f"claim: one look at that coordinate settles whether this route "
        f"exists. It is NOT the canonical answer and must not be shown as one.")


def _many_crossings_detail(relied: Sequence[Reliance],
                           decisive: Sequence[Reliance],
                           method: str) -> str:
    n = len(relied)
    if method == DECISIVENESS_UNTESTED:
        return (
            f"this route passes through {n} unresolved crossings. Whether any "
            f"single one of them decides the route was not tested - no "
            f"re-routing hook was supplied - so the count is reported and the "
            f"decisiveness is not claimed either way.")
    if decisive:
        ids = ", ".join(str(r.crossing.crossing_id) for r in decisive)
        return (
            f"this route passes through {n} unresolved crossings, and removing "
            f"crossing {ids} on its own changes it. That one is the claim worth "
            f"checking first.")
    return (
        f"this route passes through {n} unresolved crossings and no single one "
        f"of them decides it: removing any one leaves the route unchanged, so "
        f"the result stands on {n} stacked assumptions at once. That is a "
        f"weaker footing than one unresolved junction, not a stronger one.")


# ---------------------------------------------------------------- API shape
def crossing_dict(r: Reliance) -> dict:
    x = r.crossing
    lon, lat = nztm_to_lonlat(x.x, x.y)
    return {
        "crossingId": x.crossing_id,
        "sourceA": x.source_a,
        "sourceB": x.source_b,
        # Both projections. 2193 is what the analysis measured in; 4326 is what
        # a reader needs to put the point on a map and go and look at it, which
        # is the entire remedy for an unresolved crossing.
        "x": round(x.x, 3),
        "y": round(x.y, 3),
        "lon": round(lon, 7),
        "lat": round(lat, 7),
        "reason": x.reason,
        "confidence": x.confidence,
        "angleDeg": None if x.angle_deg is None else round(x.angle_deg, 1),
        "placeId": x.place_id,
        "fromLinkId": r.from_link_id,
        "toLinkId": r.to_link_id,
        "decisive": r.decisive,
    }


def as_dict(p: Provenance) -> dict:
    return {
        # Said in the payload, not only in a docstring. Anything rendering this
        # block is looking at a sensitivity result, never the official route.
        "graph": "possible",
        "canonical": False,
        "robustness": p.robustness,
        "speculativeJunctionCount": p.speculative_junction_count,
        "changedByOneCrossing": p.changed_by_one_crossing,
        "requiresMultipleAssumptions": p.requires_multiple_assumptions,
        "decisivenessMethod": p.decisiveness_method,
        "unresolvedCrossingIds": [r.crossing.crossing_id for r in p.relied_on],
        "decisiveCrossingIds": [r.crossing.crossing_id for r in p.relied_on
                                if r.decisive],
        "crossings": [crossing_dict(r) for r in p.relied_on],
        "detail": p.detail,
    }


def annotate(path, provenance: Provenance | None) -> None:
    """Attach provenance to a replacement path, and raise its flag with it.

    ONE function, so the field and the flag cannot disagree. A route carrying
    `RELIES_ON_UNRESOLVED_CROSSING` with no provenance block would be a caveat
    with nothing behind it, and a provenance block with no flag would be a
    caveat nobody sees - the inspector shows flags on the summary and the block
    only when expanded.

    Does nothing when `provenance` is None, which is the canonical-graph case:
    a route nobody asked this question about must serialise exactly as before.
    """
    if provenance is None:
        return
    path.possible_provenance = as_dict(provenance)
    if provenance.relied_on and \
            RELIES_ON_UNRESOLVED_CROSSING not in path.quality_flags:
        path.quality_flags.append(RELIES_ON_UNRESOLVED_CROSSING)


# ------------------------------------------------------------- the reader
#
# The first reader of the `crossings` table. It has been write-only since the
# migration that created it, which is precisely why the doubt it records never
# reached an answer.
def load_route(snapshot_id: str, link_ids: Sequence[int]) -> list[RouteLink]:
    """The route's links, in the order given, with their endpoints.

    Only the endpoints are fetched. The interior geometry cannot participate in
    the join - a crossing point is a graph node, so it is an endpoint of both
    links or it is not on the route at all - and pulling full LineStrings for a
    300-link route to discard every vertex but two is a waste of the wire.
    """
    ids = [int(i) for i in link_ids]
    if not ids:
        return []
    rows = db.query(
        "SELECT link_id, closure_group_id, "
        "       ST_X(ST_StartPoint(geom_2193)) AS x0, "
        "       ST_Y(ST_StartPoint(geom_2193)) AS y0, "
        "       ST_X(ST_EndPoint(geom_2193))   AS x1, "
        "       ST_Y(ST_EndPoint(geom_2193))   AS y1 "
        "  FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)",
        (snapshot_id, sorted(set(ids))))
    by_id = {int(r["link_id"]): r for r in rows}

    out: list[RouteLink] = []
    for lid in ids:
        r = by_id.get(lid)
        if r is None:
            # A route naming a link this snapshot does not have is a caller
            # error, not something to paper over: silently dropping it would
            # make two non-adjacent links look consecutive and could invent a
            # reliance that no route has.
            raise KeyError(
                f"link {lid} is not in snapshot {snapshot_id}; a route cannot "
                f"be attributed to crossings without its own geometry")
        out.append(RouteLink(
            link_id=lid,
            closure_group_id=str(r["closure_group_id"]),
            endpoints=((float(r["x0"]), float(r["y0"])),
                       (float(r["x1"]), float(r["y1"])))))
    return out


def is_possible_graph(snapshot_id: str) -> bool:
    """Was this snapshot built with `crossing_policy='possible'`?

    Asked of the DATA, not of the snapshot's prose. The ingest records the
    policy only in a free-text note, and parsing prose to decide whether an
    answer is canonical would make the distinction depend on a sentence
    somebody could reword. The structural fact is equivalent and cannot drift:
    a noded UNRESOLVED crossing exists under exactly one policy.

    A `possible` snapshot on which the classifier happened to resolve
    everything answers False, which is correct - it produced the canonical
    graph, and there is no assumption to report.
    """
    row = db.query_one(
        "SELECT 1 AS yes FROM crossings "
        " WHERE snapshot_id=%s AND disposition='UNRESOLVED' AND noded "
        " LIMIT 1", (snapshot_id,))
    return row is not None


def load_all_speculative_crossings(snapshot_id: str) -> list[CrossingRecord]:
    """Every noded UNRESOLVED crossing in the snapshot.

    For the cached lookup. Nationally this is a subset of 13,056 crossing
    pairs, so it is a small table to hold rather than a per-route query - and a
    closure with a dozen movements would otherwise run a dozen of them.
    """
    return _records(db.query(
        "SELECT crossing_id, source_a, source_b, disposition, noded, reason, "
        "       confidence, angle_deg, place_id, "
        "       ST_X(geom_2193) AS x, ST_Y(geom_2193) AS y "
        "  FROM crossings "
        " WHERE snapshot_id=%s AND disposition='UNRESOLVED' AND noded",
        (snapshot_id,)))


def load_candidate_crossings(snapshot_id: str,
                             closure_group_ids: Sequence[str]
                             ) -> list[CrossingRecord]:
    """Noded UNRESOLVED crossings whose BOTH sides the route uses.

    Both sides, in SQL, because that is a necessary condition for reliance and
    it is the half of the test an index can do: `crossings_source_a_idx` and
    `crossings_source_b_idx` exist on exactly this. The sufficient condition -
    that the route passes between those two features at that point - needs the
    route's order and is applied in `relied_upon`.
    """
    groups = sorted({str(g) for g in closure_group_ids})
    if len(groups) < 2:
        # One source feature cannot cross another one. Skipping the query is
        # not an optimisation here; it is the correct answer.
        return []
    rows = db.query(
        "SELECT crossing_id, source_a, source_b, disposition, noded, reason, "
        "       confidence, angle_deg, place_id, "
        "       ST_X(geom_2193) AS x, ST_Y(geom_2193) AS y "
        "  FROM crossings "
        " WHERE snapshot_id=%s AND disposition='UNRESOLVED' AND noded "
        "   AND source_a = ANY(%s) AND source_b = ANY(%s)",
        (snapshot_id, groups, groups))
    return _records(rows)


def _records(rows) -> list[CrossingRecord]:
    return [
        CrossingRecord(
            crossing_id=int(r["crossing_id"]),
            source_a=str(r["source_a"]), source_b=str(r["source_b"]),
            x=float(r["x"]), y=float(r["y"]),
            disposition=str(r["disposition"]), noded=bool(r["noded"]),
            reason=str(r["reason"] or ""),
            confidence=str(r["confidence"] or "MEDIUM"),
            angle_deg=(None if r["angle_deg"] is None
                       else float(r["angle_deg"])),
            place_id=(None if r["place_id"] is None else int(r["place_id"])),
        )
        for r in rows
    ]


def for_route(snapshot_id: str, link_ids: Sequence[int], *,
              reroute: Reroute | None = None) -> Provenance:
    """Provenance for one route on one snapshot, straight from the database."""
    route = load_route(snapshot_id, link_ids)
    crossings = load_candidate_crossings(
        snapshot_id, [l.closure_group_id for l in route])
    return analyse(route, crossings, reroute=reroute)


def lookup_for(snapshot_id: str, *, reroute: Reroute | None = None
               ) -> Callable[[Sequence[int]], Provenance]:
    """A per-snapshot lookup, for handing to `replacement.compute`.

    A closure rather than a bound method so the CALLER decides whether this
    question is being asked at all - see `is_possible_graph`. On a canonical
    snapshot nobody should ask: `possible_provenance` stays None and every
    canonical route serialises exactly as it did before this module existed.

    The snapshot's speculative crossings are loaded once, on the first route,
    and reused. A closure has one replacement path per movement and they are
    all on one snapshot; querying per path would multiply one small read by
    the movement count for no new information.
    """
    cached: list[CrossingRecord] | None = None

    def lookup(link_ids: Sequence[int]) -> Provenance:
        nonlocal cached
        if cached is None:
            cached = load_all_speculative_crossings(snapshot_id)
        return analyse(load_route(snapshot_id, link_ids), cached,
                       reroute=reroute)
    return lookup
