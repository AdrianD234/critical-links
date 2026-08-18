"""One unresolved junction and four stacked assumptions must not read alike.

The POSSIBLE graph exists to answer "would this result change if the crossings
we cannot resolve turned out to be junctions?". Answering it with a single
boolean throws away the only part a reader can act on: WHICH crossings, and
whether any ONE of them is the claim worth going and checking.

`TestARouteThatAssumedNothing` is the class that keeps the rest honest. It
routes straight through a noded unresolved crossroads - the crossing point is
literally on the route, at zero distance - and asserts that nothing is
reported. A proximity test would pass every other class in this file and fail
that one, which is exactly why it is written first.

Fixtures follow test_crossings.py: small synthetic grids, real geometry, real
`split_at_junctions`. They sit at genuine NZTM2000 coordinates near Darfield
rather than around an origin of (0, 0), because the provenance block reports
lon/lat so somebody can open an aerial photograph at that point, and a fixture
whose coordinates reproject to Antarctica would never catch a broken one.
"""

from __future__ import annotations

import heapq

import pytest

from nzcl import crossings
from nzcl.provenance import (DECISIVENESS_NONE, DECISIVENESS_REROUTED,
                             DECISIVENESS_SINGLE, DECISIVENESS_UNTESTED,
                             MULTIPLE_UNRESOLVED_CROSSINGS,
                             ONE_UNRESOLVED_CROSSING,
                             RELIES_ON_UNRESOLVED_CROSSING, ROBUST,
                             CrossingRecord, RouteLink, analyse, annotate,
                             as_dict)
from nzcl.replacement import ReplacementPath, path_dict
from nzcl.topology import assign_nodes, split_at_junctions

from test_topology import src


# --- geometry --------------------------------------------------------------
#
# Everything is laid out in metres relative to a point on the Canterbury Plains,
# so `x`/`y` are plausible EPSG:2193 and `lon`/`lat` land in New Zealand.
ORIGIN_E, ORIGIN_N = 1520000.0, 5165000.0


def at(dx: float, dy: float) -> tuple[float, float]:
    return (ORIGIN_E + dx, ORIGIN_N + dy)


def road(amds_id: str, offsets, *, oneway: int = 2, **attrs):
    return src(amds_id, [at(dx, dy) for dx, dy in offsets],
               model_asset_type=1, oneway=oneway, rca_code=74, **attrs)


#            (0,3000)
#               |                  GREENDALE runs west-east through (0,0).
#   +-----------+                  CLINTONS runs south-north through (0,0) and
#   |           |                  is ONE-WAY, which is what makes the crossing
#   |     ------+------            UNRESOLVED rather than AT_GRADE: the
#   |           |                  classifier has no evidence either way when a
#   |           |                  side is not an ordinary two-way roadway.
#   PERIMETER
#
# LANE_A x LANE_B is a second unresolved crossing 10 m north of GREENDALE and
# 1,500 m along it. It exists so "near the route" and "used by the route" can
# be told apart by a test rather than by reading the implementation.
DARFIELD = [
    road("GREENDALE", [(-2000, 0), (2000, 0)], road_name="Greendale Road"),
    road("CLINTONS", [(0, -2000), (0, 3000)], oneway=1,
         road_name="Clintons Road"),
    # Joins GREENDALE's west end to CLINTONS' north end the long way round, so
    # "the crossing changed the answer" and "there was no other answer" are
    # different outcomes.
    road("PERIMETER", [(-2000, 0), (-3000, 0), (-3000, 3000), (0, 3000)]),
    road("LANE_A", [(-1500, 3), (-1500, 600)]),
    road("LANE_B", [(-1560, 10), (-1440, 10)], oneway=1),
]

# A Manhattan block. Four one-way/two-way crossings, all UNRESOLVED, and TWO
# shortest routes of identical length between the same two points - one using
# the southern pair of crossings, one the northern pair.
#
# That symmetry is the fixture's whole purpose. Every crossing on the chosen
# route has an equal-cost way round, so no single one of them moves the answer,
# and the route still cannot be driven if the unresolved crossings are not
# junctions. That is "requires multiple assumptions", and it is a real shape
# rather than a contrived one - it is what a city grid looks like.
BLOCK = 1000.0
LATTICE_NS = [
    road("ROAD_W", [(0, -500), (0, BLOCK + 500)], oneway=1),
    road("ROAD_E", [(BLOCK, -500), (BLOCK, BLOCK + 500)], oneway=1),
]
LATTICE_S = road("ROAD_S", [(-500, 0), (BLOCK + 500, 0)])
LATTICE_N = road("ROAD_N", [(-500, BLOCK), (BLOCK + 500, BLOCK)])

LATTICE = [*LATTICE_NS, LATTICE_S, LATTICE_N]
#: The same block with its northern road taken away, so the only route through
#: it uses both southern crossings and neither has a way round.
LATTICE_ONE_WAY_THROUGH = [*LATTICE_NS, LATTICE_S]


# --- turning a SplitResult into what the database would hold ---------------
def crossing_rows(res) -> list[CrossingRecord]:
    """The `crossings` rows the ingest would COPY for this split.

    Mirrors ingest.py, including `noded` - which is NOT implied by the
    disposition. A snapshot built under a policy that honours nothing records
    every classification and cuts at none of them, and provenance has to read
    what was actually cut, not what the evidence said.
    """
    honoured = {
        "none": frozenset(),
        "confirmed": frozenset({crossings.AT_GRADE}),
        "possible": frozenset({crossings.AT_GRADE, crossings.UNRESOLVED}),
    }[res.crossing_policy]
    out = []
    for cid, x in enumerate(res.crossings):
        cls = x.classification
        out.append(CrossingRecord(
            crossing_id=cid, source_a=x.amds_a, source_b=x.amds_b,
            x=x.x, y=x.y, disposition=x.disposition,
            noded=(x.disposition in honoured and cls.safe_to_node),
            reason=cls.reason, confidence=cls.confidence,
            angle_deg=x.angle_deg,
            place_id=res.crossing_places[cid] if res.crossing_places else None,
        ))
    return out


def route_of(res, link_ids) -> list[RouteLink]:
    """The link ids a router returned, as the value provenance reads."""
    return [
        RouteLink(link_id=i,
                  closure_group_id=res.links[i].closure_group_id,
                  endpoints=(res.links[i].coords[0], res.links[i].coords[-1]))
        for i in link_ids
    ]


# --- a router, so "the answer changed" is measured rather than asserted -----
def shortest(res, start_xy, end_xy, *, suppress=frozenset()):
    """Cheapest route over the split links, with some crossings un-noded.

    `suppress` names crossing ids whose node is taken back out: at that
    coordinate each source feature keeps its own node instead of sharing one,
    which is exactly the graph the CONFIRMED policy would have produced for
    that crossing. Returns (distance, [link ids]) or (None, []).
    """
    pairs, coords = assign_nodes(res.links)
    points = [(res.crossings[c].x, res.crossings[c].y) for c in suppress]

    def key(nid: int, link):
        x, y = coords[nid]
        for sx, sy in points:
            if (x - sx) ** 2 + (y - sy) ** 2 <= 1e-12:
                return (nid, link.closure_group_id)
        return (nid, None)

    adj: dict = {}
    for i, (link, (a, b)) in enumerate(zip(res.links, pairs)):
        ka, kb = key(a, link), key(b, link)
        if ka == kb:
            continue
        adj.setdefault(ka, []).append((kb, link.length_m, i))
        adj.setdefault(kb, []).append((ka, link.length_m, i))

    def node_at(xy):
        for nid, (x, y) in enumerate(coords):
            if abs(x - xy[0]) < 1e-6 and abs(y - xy[1]) < 1e-6:
                return (nid, None)
        return None

    s, t = node_at(start_xy), node_at(end_xy)
    if s is None or t is None:
        return None, []

    dist = {s: 0.0}
    prev: dict = {}
    pq = [(0.0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == t:
            break
        if d > dist.get(u, float("inf")) + 1e-12:
            continue
        for v, w, li in sorted(adj.get(u, ()), key=lambda e: (e[1], e[2])):
            nd = d + w
            if nd < dist.get(v, float("inf")) - 1e-9:
                dist[v] = nd
                prev[v] = (u, li)
                heapq.heappush(pq, (nd, v))

    if t not in dist:
        return None, []
    links: list[int] = []
    cur = t
    while cur != s:
        u, li = prev[cur]
        links.append(li)
        cur = u
    links.reverse()
    return dist[t], links


def rerouter(res, start_xy, end_xy):
    """The decisiveness hook: cost with a crossing's node taken back out."""
    def go(suppress):
        return shortest(res, start_xy, end_xy, suppress=suppress)[0]
    return go


# ---------------------------------------------------------------------------
class TestTheFixtureIsWhatItClaims:
    """A provenance test is worthless if the crossings are not the ones it
    thinks. Pinned separately so a classifier change breaks HERE, loudly,
    rather than turning every assertion below into a silent tautology."""

    def test_the_darfield_crossroads_is_unresolved_and_noded(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        rows = {r.sources: r for r in crossing_rows(res)}
        gc = rows[frozenset({"GREENDALE", "CLINTONS"})]
        assert gc.disposition == crossings.UNRESOLVED
        assert gc.noded is True
        assert gc.x == pytest.approx(ORIGIN_E)
        assert gc.y == pytest.approx(ORIGIN_N)

    def test_the_lane_crossing_really_is_beside_the_route(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        rows = {r.sources: r for r in crossing_rows(res)}
        lane = rows[frozenset({"LANE_A", "LANE_B"})]
        assert lane.disposition == crossings.UNRESOLVED
        assert lane.noded is True
        # 10 m from Greendale Road, which every route along it drives past.
        assert lane.y - ORIGIN_N == pytest.approx(10.0)

    def test_the_confirmed_graph_nodes_neither_of_them(self):
        """The contrast the whole POSSIBLE graph rests on. If these crossings
        were AT_GRADE they would be in the canonical answer and there would be
        no assumption to report."""
        res = split_at_junctions(DARFIELD, crossing_policy="confirmed")
        assert res.crossing_cuts == 0
        assert all(not r.noded for r in crossing_rows(res))

    def test_the_lattice_has_four_unresolved_crossings(self):
        res = split_at_junctions(LATTICE, crossing_policy="possible")
        rows = crossing_rows(res)
        assert len(rows) == 4
        assert all(r.disposition == crossings.UNRESOLVED and r.noded
                   for r in rows)
        # Four separate PLACES: nothing here is a mixed interchange, so the
        # mixed-place rule withdraws nothing and the fixture means what it says.
        assert len({r.place_id for r in rows}) == 4


class TestARouteThatAssumedNothing:
    """Driving THROUGH a speculative junction is not the same as using it.

    This is the case a proximity join gets wrong, and it gets it wrong in the
    worst direction: the crossing point sits at zero distance from the route,
    so any buffer catches it. But the route stays on Greendale Road the whole
    way, and without the crossing those two links would simply still be one
    link and the route would be identical. Counting it would attach a caveat to
    an answer that made no assumption at all.
    """

    ROUTE = (at(-2000, 0), at(2000, 0))

    def test_a_route_straight_through_relies_on_nothing(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        cost, links = shortest(res, *self.ROUTE)
        assert cost == pytest.approx(4000.0)

        p = analyse(route_of(res, links), crossing_rows(res))
        assert p.robustness == ROBUST
        assert p.speculative_junction_count == 0
        assert p.changed_by_one_crossing is False
        assert p.requires_multiple_assumptions is False
        assert p.decisiveness_method == DECISIVENESS_NONE

    def test_the_crossing_it_drove_over_is_on_the_route_at_zero_distance(self):
        """Stated as a fact of the fixture, so the class above is known to be
        testing the hard case rather than an absent crossing."""
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        _, links = shortest(res, *self.ROUTE)
        route = route_of(res, links)
        touched = [c for link in route for c in link.endpoints]
        assert any(abs(x - ORIGIN_E) < 1e-6 and abs(y - ORIGIN_N) < 1e-6
                   for x, y in touched)
        # ...and both sides of it are the same source feature, which is the
        # reason it is not a reliance.
        assert {l.closure_group_id for l in route} == {"GREENDALE"}

    def test_no_quality_flag_is_raised(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        _, links = shortest(res, *self.ROUTE)
        p = analyse(route_of(res, links), crossing_rows(res))
        path = ReplacementPath(movement_id="m", entry_port_id="a",
                               exit_port_id="b", from_node=1, to_node=2,
                               status="OK")
        annotate(path, p)
        assert RELIES_ON_UNRESOLVED_CROSSING not in path.quality_flags
        # The block is still attached: "asked, and the answer was none" is a
        # different fact from "never asked", and only the first is a finding.
        assert path.possible_provenance is not None


class TestOneUnresolvedJunction:
    """The single checkable claim.

    One crossing, one coordinate, one aerial photograph. This is the case where
    the sensitivity graph earns its keep: it names the thing to go and look at.
    """

    ROUTE = (at(-2000, 0), at(0, 3000))

    def possible(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        cost, links = shortest(res, *self.ROUTE)
        return res, cost, analyse(route_of(res, links), crossing_rows(res),
                                  reroute=rerouter(res, *self.ROUTE))

    def test_the_crossing_is_what_changed_the_answer(self):
        """Without it the trip goes the long way round the perimeter. Measured,
        so the fixture cannot degrade into one where the crossing is
        irrelevant and every assertion below still passes."""
        conf = split_at_junctions(DARFIELD, crossing_policy="confirmed")
        assert shortest(conf, *self.ROUTE)[0] == pytest.approx(7000.0)
        assert self.possible()[1] == pytest.approx(5000.0)

    def test_it_classifies_as_one_unresolved_crossing(self):
        _, _, p = self.possible()
        assert p.robustness == ONE_UNRESOLVED_CROSSING
        assert p.speculative_junction_count == 1

    def test_one_crossing_is_decisive_by_construction(self):
        """No re-route needed: the route passed through the only speculative
        node it had, so taking that node away cannot leave the answer alone."""
        _, _, p = self.possible()
        assert p.changed_by_one_crossing is True
        assert p.requires_multiple_assumptions is False
        assert p.decisiveness_method == DECISIVENESS_SINGLE

    def test_it_reports_the_pair_and_the_place_to_look(self):
        res, _, p = self.possible()
        d = as_dict(p)
        assert d["speculativeJunctionCount"] == 1
        one = d["crossings"][0]
        assert {one["sourceA"], one["sourceB"]} == {"GREENDALE", "CLINTONS"}
        assert one["crossingId"] == d["unresolvedCrossingIds"][0]
        assert one["x"] == pytest.approx(ORIGIN_E, abs=1e-3)
        assert one["y"] == pytest.approx(ORIGIN_N, abs=1e-3)
        # WGS84 as well, because "go and look at this point" is the remedy and
        # nobody opens imagery in NZTM2000.
        assert 166 < one["lon"] < 179
        assert -48 < one["lat"] < -34
        assert one["reason"] == "NO_EVIDENCE_EITHER_WAY"
        assert one["confidence"] in ("HIGH", "MEDIUM")
        assert one["angleDeg"] == pytest.approx(90.0, abs=0.5)
        # The two links it turned between, so the claim can be traced back into
        # the route rather than only onto a map.
        assert one["fromLinkId"] != one["toLinkId"]
        assert one["decisive"] is True

    def test_the_flag_and_the_evidence_arrive_together(self):
        _, _, p = self.possible()
        path = ReplacementPath(movement_id="m", entry_port_id="a",
                               exit_port_id="b", from_node=1, to_node=2,
                               status="OK")
        annotate(path, p)
        assert RELIES_ON_UNRESOLVED_CROSSING in path.quality_flags
        assert path.possible_provenance["robustness"] == ONE_UNRESOLVED_CROSSING


class TestACrossingBesideTheRouteIsNotReported:
    """Proximity is not reliance, on a crossing the route never touches.

    LANE_A x LANE_B is 10 m from Greendale Road and noded under the same
    policy. Any spatial buffer wide enough to be robust against float error on
    the crossings the route DID use would also catch this one.
    """

    def test_only_the_traversed_crossing_is_reported(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        rows = crossing_rows(res)
        assert sum(1 for r in rows if r.noded) == 2, (
            "the fixture must contain a second noded crossing, or this test "
            "asserts nothing")

        _, links = shortest(res, at(-2000, 0), at(0, 3000))
        p = analyse(route_of(res, links), rows)
        assert p.speculative_junction_count == 1
        reported = p.relied_on[0].crossing.sources
        assert reported == frozenset({"GREENDALE", "CLINTONS"})
        assert "LANE_A" not in reported and "LANE_B" not in reported

    def test_a_crossing_on_roads_the_route_never_uses_is_not_reported(self):
        """Both of its source features are absent from the route, which is the
        half of the test the SQL does with an index."""
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        _, links = shortest(res, at(-2000, 0), at(2000, 0))
        groups = {res.links[i].closure_group_id for i in links}
        assert "LANE_A" not in groups and "LANE_B" not in groups
        assert analyse(route_of(res, links), crossing_rows(res)).relied_on == []


class TestSeveralAssumptionsDoNotReadLikeOne:
    """MULTIPLE_UNRESOLVED_CROSSINGS is one label over two different states.

    Both need at least two speculative junctions, and they are not equally
    reassuring:

      one is decisive        there is a single claim that moves the number, and
                            checking it settles the result.
      several are needed     no one crossing moves the number, because each has
                            an equal-cost way round that uses another one. The
                            result stands on all of them at once and no single
                            photograph settles anything.

    Reporting both as "relies on unresolved topology" would hide the difference
    that decides whether the finding can be verified.
    """

    ENDS = (at(0, -500), at(BLOCK, BLOCK + 500))

    def test_the_lattice_offers_two_routes_of_identical_length(self):
        """The property the case below depends on, measured rather than
        assumed: without the tie there is always a decisive crossing."""
        res = split_at_junctions(LATTICE, crossing_policy="possible")
        assert shortest(res, *self.ENDS)[0] == pytest.approx(2 * BLOCK + 1000)

    def test_no_single_crossing_decides_it(self):
        res = split_at_junctions(LATTICE, crossing_policy="possible")
        cost, links = shortest(res, *self.ENDS)
        p = analyse(route_of(res, links), crossing_rows(res),
                    reroute=rerouter(res, *self.ENDS))

        assert p.robustness == MULTIPLE_UNRESOLVED_CROSSINGS
        assert p.speculative_junction_count == 2
        assert p.decisiveness_method == DECISIVENESS_REROUTED
        assert p.changed_by_one_crossing is False
        assert p.requires_multiple_assumptions is True
        assert as_dict(p)["decisiveCrossingIds"] == []

        # Not decisive individually, and not therefore harmless: with every
        # unresolved crossing withdrawn there is no represented route at all.
        every = frozenset(r.crossing_id for r in crossing_rows(res))
        assert shortest(res, *self.ENDS, suppress=every)[0] is None
        assert cost is not None

    def test_where_there_is_no_way_round_the_crossings_are_decisive(self):
        """The same block with its northern road removed. The route now uses
        both southern crossings and neither has an alternative, so removing
        either one changes the answer - from a number to no route at all."""
        res = split_at_junctions(LATTICE_ONE_WAY_THROUGH,
                                 crossing_policy="possible")
        _, links = shortest(res, *self.ENDS)
        p = analyse(route_of(res, links), crossing_rows(res),
                    reroute=rerouter(res, *self.ENDS))

        assert p.robustness == MULTIPLE_UNRESOLVED_CROSSINGS
        assert p.speculative_junction_count == 2
        assert p.changed_by_one_crossing is True
        assert p.requires_multiple_assumptions is False
        assert len(as_dict(p)["decisiveCrossingIds"]) == 2

    def test_without_the_hook_decisiveness_is_declared_untested(self):
        """The one thing this must never do is return a plausible boolean with
        nothing behind it. With no re-routing available the count-based answer
        is reported and the method says so, so a reader can tell 'no single
        crossing is decisive' from 'nobody checked'."""
        res = split_at_junctions(LATTICE_ONE_WAY_THROUGH,
                                 crossing_policy="possible")
        _, links = shortest(res, *self.ENDS)
        p = analyse(route_of(res, links), crossing_rows(res))

        assert p.decisiveness_method == DECISIVENESS_UNTESTED
        assert all(r.decisive is None for r in p.relied_on)
        d = as_dict(p)
        assert d["decisiveCrossingIds"] == []
        assert d["crossings"][0]["decisive"] is None
        assert "not tested" in d["detail"]

    def test_the_two_states_are_distinguishable_from_the_serialised_dict(self):
        """Belt and braces: a consumer reading only the JSON must be able to
        tell them apart without re-deriving anything."""
        many = split_at_junctions(LATTICE, crossing_policy="possible")
        one = split_at_junctions(LATTICE_ONE_WAY_THROUGH,
                                 crossing_policy="possible")
        out = []
        for res in (many, one):
            _, links = shortest(res, *self.ENDS)
            out.append(as_dict(analyse(route_of(res, links),
                                       crossing_rows(res),
                                       reroute=rerouter(res, *self.ENDS))))
        a, b = out
        assert a["robustness"] == b["robustness"]
        assert a["changedByOneCrossing"] != b["changedByOneCrossing"]
        assert a["requiresMultipleAssumptions"] != \
            b["requiresMultipleAssumptions"]


class TestTheSerialisedShape:
    """camelCase, and absent on a canonical route."""

    def test_a_canonical_route_carries_no_provenance_at_all(self):
        """The default. Nothing about an ordinary answer changes, and the key
        is OMITTED rather than null - a null would come to be read as 'the
        possible graph was consulted and found nothing', which is a different
        and much stronger statement."""
        path = ReplacementPath(movement_id="m", entry_port_id="a",
                               exit_port_id="b", from_node=1, to_node=2,
                               status="OK")
        assert path.possible_provenance is None
        d = path_dict(path)
        assert "possibleProvenance" not in d
        assert d.get("possibleProvenance") is None
        assert RELIES_ON_UNRESOLVED_CROSSING not in d["qualityFlags"]

    def test_annotating_with_nothing_leaves_a_canonical_route_untouched(self):
        path = ReplacementPath(movement_id="m", entry_port_id="a",
                               exit_port_id="b", from_node=1, to_node=2,
                               status="OK")
        before = path_dict(path)
        annotate(path, None)
        assert path_dict(path) == before

    def test_the_keys_are_camel_case(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        _, links = shortest(res, at(-2000, 0), at(0, 3000))
        d = as_dict(analyse(route_of(res, links), crossing_rows(res)))

        expected = {"graph", "canonical", "robustness",
                    "speculativeJunctionCount", "changedByOneCrossing",
                    "requiresMultipleAssumptions", "decisivenessMethod",
                    "unresolvedCrossingIds", "decisiveCrossingIds",
                    "crossings", "detail"}
        assert set(d) == expected
        assert set(d["crossings"][0]) == {
            "crossingId", "sourceA", "sourceB", "x", "y", "lon", "lat",
            "reason", "confidence", "angleDeg", "placeId", "fromLinkId",
            "toLinkId", "decisive"}
        for key in set(d) | set(d["crossings"][0]):
            assert "_" not in key, f"{key} is not camelCase"

    def test_it_says_in_the_payload_that_it_is_not_the_canonical_answer(self):
        """A consumer that renders this block must not have to know which
        snapshot produced it. The payload says so itself."""
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        _, links = shortest(res, at(-2000, 0), at(0, 3000))
        d = as_dict(analyse(route_of(res, links), crossing_rows(res)))
        assert d["graph"] == "possible"
        assert d["canonical"] is False

    def test_a_possible_route_serialises_through_the_path(self):
        res = split_at_junctions(DARFIELD, crossing_policy="possible")
        _, links = shortest(res, at(-2000, 0), at(0, 3000))
        p = analyse(route_of(res, links), crossing_rows(res))

        path = ReplacementPath(movement_id="m", entry_port_id="a",
                               exit_port_id="b", from_node=1, to_node=2,
                               status="OK")
        annotate(path, p)
        d = path_dict(path)
        assert d["possibleProvenance"]["robustness"] == ONE_UNRESOLVED_CROSSING
        assert RELIES_ON_UNRESOLVED_CROSSING in d["qualityFlags"]


class TestCrossingsThatCannotHaveBeenRelinedOn:
    """Rows that exist in the table but describe no assumption this route made.

    All three are filtered before the geometry is looked at, because each is a
    different reason and folding them into one distance test would make the
    next person guess which.
    """

    def base(self, **kw) -> CrossingRecord:
        d = dict(crossing_id=1, source_a="A", source_b="B",
                 x=ORIGIN_E, y=ORIGIN_N, disposition=crossings.UNRESOLVED,
                 noded=True, reason="NO_EVIDENCE_EITHER_WAY")
        d.update(kw)
        return CrossingRecord(**d)

    def route(self) -> list[RouteLink]:
        return [
            RouteLink(1, "A", (at(-100, 0), at(0, 0))),
            RouteLink(2, "B", (at(0, 0), at(0, 100))),
        ]

    def test_the_route_does_rely_on_the_base_case(self):
        p = analyse(self.route(), [self.base()])
        assert p.robustness == ONE_UNRESOLVED_CROSSING

    def test_an_at_grade_crossing_is_not_an_assumption(self):
        """It is in the CANONICAL graph. Reporting it would attach a caveat to
        every ordinary crossroads in the country."""
        p = analyse(self.route(), [self.base(disposition=crossings.AT_GRADE)])
        assert p.robustness == ROBUST

    def test_a_crossing_that_was_not_cut_created_no_node(self):
        """Recorded UNRESOLVED but left disconnected - which is what the
        CONFIRMED graph does with all of them. No node, so no route can have
        used it, whatever its coordinates say."""
        p = analyse(self.route(), [self.base(noded=False)])
        assert p.robustness == ROBUST

    def test_a_road_crossing_itself_is_refused(self):
        """SAME_SOURCE_FEATURE is never noded under any policy, but its source
        pair collapses to a single feature and would otherwise match a route
        running straight through on that one road."""
        p = analyse([RouteLink(1, "A", (at(-100, 0), at(0, 0))),
                     RouteLink(2, "A", (at(0, 0), at(100, 0)))],
                    [self.base(source_b="A", reason="SAME_SOURCE_FEATURE")])
        assert p.robustness == ROBUST

    def test_a_crossing_between_the_right_roads_at_the_wrong_place(self):
        """Two source features can cross more than once. Only the crossing the
        route actually turned at is a reliance."""
        p = analyse(self.route(), [self.base(x=ORIGIN_E + 400.0)])
        assert p.robustness == ROBUST

    def test_the_same_crossing_is_never_counted_twice(self):
        """A route that returns to one junction - a loop out and back - relies
        on it once. Counting it twice would double the assumption count on a
        route that made one assumption."""
        route = [
            RouteLink(1, "A", (at(-100, 0), at(0, 0))),
            RouteLink(2, "B", (at(0, 0), at(0, 100))),
            RouteLink(3, "A", (at(0, 0), at(0, 100))),
            RouteLink(4, "B", (at(0, 0), at(100, 0))),
        ]
        p = analyse(route, [self.base()])
        assert p.speculative_junction_count == 1
