"""Known-answer tests for the boundary-movement engine (PR 2, phases 6 to 9).

Every network here is small enough that the right answer can be worked out by
hand and written into the assertion as a number, not as a relation to whatever
the code happened to produce. Where a figure appears in an assertion, the
comment says where it comes from.

The properties that matter most, in order:

  COMPATIBILITY   on a simple two-way segment the movement measure and the
                  endpoint measure must give the SAME numbers. If they did not,
                  every difference from PR 1 would be unattributable.

  THE CLOSURE IS  no returned route may traverse its own closure, and no
  RESPECTED       closure may remove an arc the request did not declare.

  UNRESOLVED IS   a search that did not finish is never DISCONNECTED. PR 1
  NOT A FINDING   found this guard silently skipping on CI for its entire life,
                  so it is asserted here by injecting the database error and
                  following it through the whole engine.

  DETERMINISM     shuffling the input reassigns every link, arc and node id.
                  Nothing a reader sees may change.
"""

from __future__ import annotations

import random

import pytest

from nzcl import closure as closure_mod
from nzcl import corridor, db, impactv2, movements, physical, ports
from nzcl import replacement as repl_mod
from nzcl import routegeom, routing

from conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    physical.clear_cache()
    yield
    physical.clear_cache()


# ---------------------------------------------------------------- networks
#: A block. Closing one side sends you round the other three.
SQUARE = [
    {"id": "S", "pts": [(0, 0), (100, 0)], "road_name": "South Street"},
    {"id": "E", "pts": [(100, 0), (100, 100)], "road_name": "East Street"},
    {"id": "N", "pts": [(100, 100), (0, 100)], "road_name": "North Street"},
    {"id": "W", "pts": [(0, 100), (0, 0)], "road_name": "West Street"},
]

#: Three links in a line. The middle one is a true bridge: nothing replaces it.
CHAIN = [
    {"id": "A", "pts": [(0, 0), (100, 0)], "road_name": "Chain Road"},
    {"id": "B", "pts": [(100, 0), (200, 0)], "road_name": "Chain Road"},
    {"id": "C", "pts": [(200, 0), (300, 0)], "road_name": "Chain Road"},
]

#: Northbound and southbound one-way carriageways joined at both ends.
COUPLET = [
    {"id": "NB", "pts": [(0, 0), (0, 200)], "oneway": True,
     "road_name": "Queen Street"},
    {"id": "SB", "pts": [(100, 200), (100, 0)], "oneway": True,
     "road_name": "Queen Street"},
    {"id": "XN", "pts": [(0, 200), (100, 200)], "road_name": "North Cross"},
    {"id": "XS", "pts": [(100, 0), (0, 0)], "road_name": "South Cross"},
]

#: A divided carriageway: two one-way carriageways, crossovers at both ends and
#: mid-block, plus a two-way service lane on the far side of the northbound
#: carriageway.
#:
#: The service lane is there because WITHOUT it a divided carriageway has no
#: represented replacement at all, which is worth knowing: the opposite
#: carriageway runs the wrong way, so a crossover does not help. The first
#: draft of this fixture assumed it did.
DIVIDED = [
    {"id": "NB1", "pts": [(0, 0), (0, 200)], "oneway": True, "road_name": "The Parade"},
    {"id": "NB2", "pts": [(0, 200), (0, 400)], "oneway": True, "road_name": "The Parade"},
    {"id": "SB1", "pts": [(60, 400), (60, 200)], "oneway": True, "road_name": "The Parade"},
    {"id": "SB2", "pts": [(60, 200), (60, 0)], "oneway": True, "road_name": "The Parade"},
    {"id": "XMID", "pts": [(0, 200), (60, 200)], "road_name": "Crossover"},
    {"id": "XN", "pts": [(0, 400), (60, 400)], "road_name": "North Cross"},
    {"id": "XS", "pts": [(60, 0), (0, 0)], "road_name": "South Cross"},
    # Two-way, west of the northbound carriageway: 60 + 200 + 60 = 320 m.
    {"id": "SVC", "pts": [(0, 0), (-60, 0), (-60, 200), (0, 200)],
     "road_name": "Service Lane"},
]

#: The same divided carriageway with the service lane taken away.
DIVIDED_NO_SERVICE = [s for s in DIVIDED if s["id"] != "SVC"]

#: Main road with a parallel frontage road that rejoins it.
FRONTAGE = [
    {"id": "M1", "pts": [(0, 0), (200, 0)], "road_name": "Main Road"},
    {"id": "M2", "pts": [(200, 0), (400, 0)], "road_name": "Main Road"},
    {"id": "M3", "pts": [(400, 0), (600, 0)], "road_name": "Main Road"},
    {"id": "M4", "pts": [(600, 0), (800, 0)], "road_name": "Main Road"},
    {"id": "F1", "pts": [(200, 0), (200, -150)], "road_name": "Frontage Road"},
    {"id": "F2", "pts": [(200, -150), (600, -150)], "road_name": "Frontage Road"},
    {"id": "F3", "pts": [(600, -150), (600, 0)], "road_name": "Frontage Road"},
    {"id": "X1", "pts": [(0, 0), (0, 300)], "road_name": "Side Street"},
    {"id": "X2", "pts": [(800, 0), (800, 300)], "road_name": "Other Street"},
]

#: A one-way main street with a one-way parallel bypass. Closing a middle
#: segment leaves its OWN endpoints unreachable from each other while a
#: perfectly good corridor detour exists further out. This is the endpoint
#: artefact the V1 audit measured, in its smallest honest form.
ENDPOINT_ARTEFACT = [
    {"id": "MA", "pts": [(0, 0), (100, 0)], "oneway": True, "road_name": "High Street"},
    {"id": "MB", "pts": [(100, 0), (200, 0)], "oneway": True, "road_name": "High Street"},
    {"id": "MC", "pts": [(200, 0), (300, 0)], "oneway": True, "road_name": "High Street"},
    {"id": "BY1", "pts": [(0, 0), (100, -120)], "oneway": True, "road_name": "Bypass"},
    {"id": "BY2", "pts": [(100, -120), (200, -120)], "oneway": True, "road_name": "Bypass"},
    {"id": "BY3", "pts": [(200, -120), (300, 0)], "oneway": True, "road_name": "Bypass"},
]

#: A T. Closing the stem leaves three ports on one side of the closure.
TEE = [
    {"id": "STEM", "pts": [(100, 0), (100, 100)], "road_name": "Stem Road"},
    {"id": "ARM_W", "pts": [(0, 100), (100, 100)], "road_name": "Cross Road"},
    {"id": "ARM_E", "pts": [(100, 100), (200, 100)], "road_name": "Cross Road"},
    {"id": "ARM_N", "pts": [(100, 100), (100, 200)], "road_name": "North Road"},
    {"id": "FOOT", "pts": [(100, 0), (0, 0)], "road_name": "Foot Road"},
]

#: Two networks that never touch. A closure across both is disjoint in the
#: strongest sense: the parts were already in different components.
TWO_ISLANDS = [
    {"id": "I1A", "pts": [(0, 0), (100, 0)], "road_name": "Island One Road"},
    {"id": "I1B", "pts": [(100, 0), (200, 0)], "road_name": "Island One Road"},
    {"id": "I2A", "pts": [(0, 5000), (100, 5000)], "road_name": "Island Two Road"},
    {"id": "I2B", "pts": [(100, 5000), (200, 5000)], "road_name": "Island Two Road"},
]

#: ONE AMDS source feature represented by two pieces 50 km apart, each with its
#: own side roads. The realistic form of a disjoint closure, and the fixture
#: that reproduced the 0.0 m defect.
SPLIT_FEATURE = [
    {"id": "SPLIT", "pts": [(0, 0), (300, 0)], "road_name": "Split Road"},
    {"id": "NEAR_W", "pts": [(0, 0), (0, 200)], "road_name": "Near West"},
    {"id": "NEAR_E", "pts": [(300, 0), (300, 200)], "road_name": "Near East"},
    {"id": "SPLIT", "pts": [(50000, 0), (50300, 0)], "road_name": "Split Road"},
    {"id": "FAR_W", "pts": [(50000, 0), (50000, 200)], "road_name": "Far West"},
    {"id": "FAR_E", "pts": [(50300, 0), (50300, 200)], "road_name": "Far East"},
]

#: An overbridge. The two roads cross where each is INTERIOR, so AMDS does not
#: node them and neither does this system - which is what preserves grade
#: separation. Closing one must not touch the other.
GRADE_SEPARATED = [
    {"id": "EW", "pts": [(0, 0), (400, 0)], "road_name": "Motorway"},
    {"id": "NS", "pts": [(200, -200), (200, 200)], "road_name": "Overbridge Road"},
]

#: One long AMDS feature cut by side roads into children of wildly different
#: lengths - 500 m, 2 m and 1,998 m - with a bypass that rejoins it at the far
#: end. The clicked child is the 2 m one, which is the shape of the reported
#: Tokoroa case: children from 1.99 m to 5,201 m under one parent.
LONG_FEATURE = [
    {"id": "TRUNK", "pts": [(0, 0), (500, 0), (502, 0), (2500, 0)],
     "road_name": "Long Road"},
    {"id": "T1", "pts": [(500, 0), (500, -300)], "road_name": "First Side Road"},
    {"id": "T2", "pts": [(502, 0), (502, -300)], "road_name": "Second Side Road"},
    {"id": "BY", "pts": [(500, -300), (502, -300), (2500, -300), (2500, 0)],
     "road_name": "Long Bypass"},
]

#: Two ways round, of exactly equal length and identical attributes. Every
#: measurable term ties and the choice must fall to the stable identifier.
SYMMETRIC = [
    {"id": "IN", "pts": [(-100, 0), (0, 0)], "road_name": "Approach Road"},
    {"id": "MID", "pts": [(0, 0), (200, 0)], "road_name": "Middle Road"},
    {"id": "OUT", "pts": [(200, 0), (300, 0)], "road_name": "Departure Road"},
    {"id": "UP1", "pts": [(0, 0), (100, 150)], "road_name": "Loop Road"},
    {"id": "UP2", "pts": [(100, 150), (200, 0)], "road_name": "Loop Road"},
    {"id": "DN1", "pts": [(0, 0), (100, -150)], "road_name": "Loop Road"},
    {"id": "DN2", "pts": [(100, -150), (200, 0)], "road_name": "Loop Road"},
]

#: A corridor whose continuation is geometrically ambiguous: a side road leaves
#: dead straight while the named road bends. V1's straightest-continuation walk
#: takes the side road; continuity evidence must not.
AMBIGUOUS_BRANCH = [
    {"id": "R1", "pts": [(0, 0), (200, 0)], "road_name": "Bendy Road"},
    {"id": "R2", "pts": [(200, 0), (400, 0)], "road_name": "Bendy Road"},
    # The named road turns north here...
    {"id": "R3", "pts": [(400, 0), (500, 120)], "road_name": "Bendy Road"},
    {"id": "R4", "pts": [(500, 120), (700, 120)], "road_name": "Bendy Road"},
    # ...while an unrelated side road carries straight on.
    {"id": "STRAIGHT", "pts": [(400, 0), (700, 0)], "road_name": "Straight Lane"},
    {"id": "LINK", "pts": [(700, 0), (700, 120)], "road_name": "Link Lane"},
]


# ----------------------------------------------------------------- helpers
def analyse(net, name, **kw):
    kw.setdefault("with_isolation", False)
    return impactv2.analyse(net.snapshot_id, net.link_id(name), **kw)


def stages(net, name, *, scope="segment", profile="car"):
    """closure -> boundary -> movements -> replacements, without the extras."""
    lid = net.link_id(name)
    c = closure_mod.resolve(net.snapshot_id, lid, scope=scope, profile=profile)
    b = ports.derive(net.snapshot_id, c.removed_link_ids, lid, c.fingerprint,
                     profile=profile, shape=c.shape)
    ms = movements.identify(b, c.removed_arc_ids, profile=profile)
    rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                          c.selected_segment_length_m, profile=profile)
    return c, b, ms, rs


def endpoint_measure(net, name, u, v):
    """What the V1/PR-1 endpoint measure gives, computed the V1 way.

    Used only to assert the COMPATIBILITY property. Nothing else in this file
    compares against it.
    """
    lid = net.link_id(name)
    c = closure_mod.resolve(net.snapshot_id, lid, scope="segment")
    intact = routing.route(net.snapshot_id, u, v)
    after = routing.route(net.snapshot_id, u, v, excluded_arcs=c.removed_arc_ids)
    return intact, after


class TestSimpleSquare:
    """Fixture 1 and 2: a simple square, and one segment in two directions."""

    def test_two_movements_one_each_way(self, synthetic):
        net = synthetic(SQUARE)
        _, b, ms, _ = stages(net, "S")
        assert ms.status == "OK"
        # Two crossings each side of a two-way segment: 2 x 2 = 4 candidates.
        assert ms.candidate_pairs == 4
        # Two of them are the same link in and out at one node: U-turns.
        assert sum(1 for m in ms.movements
                   if m.reason_code == "U_TURN_AT_BOUNDARY") == 2
        assert len(ms.included) == 2
        assert {m.reason_code for m in ms.included} == {"THROUGH_MOVEMENT"}

    def test_the_two_movements_are_opposite_directions(self, synthetic):
        net = synthetic(SQUARE)
        u, v = net.nodes_of("S")
        _, _, ms, _ = stages(net, "S")
        pairs = {(m.from_node, m.to_node) for m in ms.included}
        assert pairs == {(u, v), (v, u)}

    def test_numbers_match_the_endpoint_measure_exactly(self, synthetic):
        """THE COMPATIBILITY PROPERTY.

        100 m closed; the other three sides are 100 m each, so the replacement
        is 300 m and the penalty 200 m. Both measures must say so, to the
        metre, or PR 2 is changing answers on the easy cases too.
        """
        net = synthetic(SQUARE)
        u, v = net.nodes_of("S")
        intact, after = endpoint_measure(net, "S", u, v)
        assert intact.status == "OK" and after.status == "OK"
        assert intact.distance_m == pytest.approx(100.0)
        assert after.distance_m == pytest.approx(300.0)

        _, _, _, rs = stages(net, "S")
        for p in rs.paths:
            assert p.status == "OK"
            assert p.intact_distance_m == pytest.approx(intact.distance_m)
            assert p.replacement_distance_m == pytest.approx(after.distance_m)
            assert p.network_penalty_m == pytest.approx(200.0)
            assert p.ratio == pytest.approx(3.0)

    def test_the_ports_sit_on_the_segments_own_endpoints(self, synthetic):
        net = synthetic(SQUARE)
        _, b, _, _ = stages(net, "S")
        assert b.reduces_to_endpoints is True


class TestTrueBridge:
    """Fixture 11: no alternative exists, and that is the answer."""

    def test_no_represented_replacement_either_way(self, synthetic):
        net = synthetic(CHAIN)
        _, _, ms, rs = stages(net, "B")
        assert len(ms.included) == 2
        assert rs.status == "OK"          # the SEARCH resolved
        assert all(p.status == "DISCONNECTED" for p in rs.paths)
        assert all(p.replacement_distance_m is None for p in rs.paths)

    def test_disconnected_is_not_reported_as_unresolved(self, synthetic):
        net = synthetic(CHAIN)
        r = analyse(net, "B")
        assert r.headline == "Through movement has no represented replacement"
        assert "MOVEMENT_SEARCH_UNRESOLVED" not in r.quality_flags


class TestOneWayCouplet:
    """Fixture 3: only the direction that used the closure is affected."""

    def test_the_direction_that_never_used_it_is_excluded(self, synthetic):
        """Southbound traffic never drove up the northbound carriageway.

        The pair exists and routes perfectly well in the intact network - it
        just does not use the closure, so closing it changes nothing for that
        trip. Reporting it as an interrupted movement would invent a detour.
        """
        net = synthetic(COUPLET)
        _, _, ms, _ = stages(net, "NB")
        assert len(ms.included) == 1
        excluded = {m.reason_code for m in ms.movements if not m.included}
        assert "DOES_NOT_TRAVERSE_CLOSURE" in excluded or \
               "U_TURN_IN_ROUTE" in excluded

    def test_the_northbound_movement_has_no_replacement(self, synthetic):
        net = synthetic(COUPLET)
        _, _, ms, rs = stages(net, "NB")
        (m,) = ms.included
        u, v = net.nodes_of("NB")
        assert (m.from_node, m.to_node) == (u, v)
        assert m.intact_distance_m == pytest.approx(200.0)   # NB is 200 m
        (p,) = rs.paths
        assert p.status == "DISCONNECTED"


class TestDividedCarriageway:
    """Fixture 4: only the direction that used the carriageway is affected."""

    def test_only_the_northbound_crossing_is_a_movement(self, synthetic):
        """Southbound traffic was never on the northbound carriageway.

        This is the property that separates a divided carriageway from an
        ordinary two-way road, and the one V1's endpoint measure cannot state.
        """
        net = synthetic(DIVIDED)
        u, v = net.nodes_of("NB1")
        _, _, ms, _ = stages(net, "NB1")
        assert {(m.from_node, m.to_node) for m in ms.included} == {(u, v)}

    def test_the_service_lane_is_the_replacement(self, synthetic):
        """NB1 is 200 m; the service lane is 60 + 200 + 60 = 320 m.

        Several movements share those two nodes - one per pair of approach and
        departure roads - and every one of them must give the same figures,
        because they are the same crossing entered and left by different roads.
        """
        net = synthetic(DIVIDED)
        _, _, _, rs = stages(net, "NB1")
        assert rs.paths
        for p in rs.paths:
            assert p.status == "OK"
            assert p.intact_distance_m == pytest.approx(200.0)
            assert p.replacement_distance_m == pytest.approx(320.0)
            assert p.network_penalty_m == pytest.approx(120.0)

    def test_without_it_a_crossover_alone_does_not_help(self, synthetic):
        """The opposite carriageway runs the wrong way, so it is no use.

        Asserted rather than assumed, because "there is a crossover, so there
        is a way round" is exactly the intuition that is wrong here.
        """
        net = synthetic(DIVIDED_NO_SERVICE)
        u, v = net.nodes_of("NB1")
        _, _, ms, rs = stages(net, "NB1")
        assert {(m.from_node, m.to_node) for m in ms.included} == {(u, v)}
        assert rs.status == "OK"
        assert rs.paths
        assert {p.status for p in rs.paths} == {"DISCONNECTED"}


class TestParallelFrontageRoad:
    """Fixture 10: an ordinary, unremarkable detour with real numbers."""

    def test_penalty_is_the_frontage_road_less_the_closed_segment(self, synthetic):
        """M3 is 200 m. Round the frontage road is 150 + 400 + 150 + 200 = 900 m.

        The 200 m tail is M2, because the frontage road rejoins Main Road at
        (200, 0) rather than at the closure's own boundary.
        """
        net = synthetic(FRONTAGE)
        _, _, ms, rs = stages(net, "M3")
        ok = [p for p in rs.paths if p.status == "OK"]
        assert ok
        for p in ok:
            assert p.intact_distance_m == pytest.approx(200.0)
            assert p.replacement_distance_m == pytest.approx(900.0)
            assert p.network_penalty_m == pytest.approx(700.0)

    def test_the_corridor_names_a_junction_and_explains_itself(self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3")
        assert r.corridor is not None and r.corridor.chosen is not None
        assert r.corridor.explanation
        assert r.corridor.admissibility_level in ("decision_points",
                                                  "all_candidates")
        # Every bound the search ran under is reported, not implied - and the
        # outward one is named for what it actually governs.
        for key in ("beamWidth", "maxHops", "maxExpansionOutwardM",
                    "seedMayExceedExpansionBound", "maxCandidatesPerSide",
                    "maxPairs"):
            assert key in r.corridor.bounds
        assert r.corridor.bounds["seedMayExceedExpansionBound"] is True


class TestEndpointArtefact:
    """Fixture 5: the endpoint measure fails where a corridor detour exists.

    This is the shape the V1 audit found across the country: the closed
    segment's own two nodes cannot be rejoined, so V1 says DISCONNECTED, while
    a driver a hundred metres back has a perfectly good way round.
    """

    def test_the_endpoint_measure_reports_disconnected(self, synthetic):
        net = synthetic(ENDPOINT_ARTEFACT)
        u, v = net.nodes_of("MB")
        _, after = endpoint_measure(net, "MB", u, v)
        assert after.status == "DISCONNECTED"

    def test_the_movement_also_has_no_replacement_between_its_own_ends(
            self, synthetic):
        net = synthetic(ENDPOINT_ARTEFACT)
        _, _, ms, rs = stages(net, "MB")
        assert [p.status for p in rs.paths] == ["DISCONNECTED"] * len(rs.paths)

    def test_but_the_corridor_finds_the_bypass(self, synthetic):
        """The point of the whole PR: further out, there IS a way round.

        The bypass is 100 + 120 hypotenuse legs; what matters is that a
        represented replacement route exists at corridor level where none
        exists between the segment's own endpoints.
        """
        net = synthetic(ENDPOINT_ARTEFACT)
        r = analyse(net, "MB")
        assert r.corridor is not None
        assert r.corridor.chosen is not None, (
            "a corridor detour exists via the bypass and must be found")
        assert r.corridor.chosen.valid
        assert r.corridor.chosen.replacement_cost_m > 0


class TestThreePortClosure:
    """Fixture 7: a closure whose boundary meets three separate roads."""

    def test_three_roads_meet_the_closure_at_one_end(self, synthetic):
        net = synthetic(TEE)
        _, b, ms, _ = stages(net, "STEM")
        # ARM_W, ARM_E and ARM_N at the top; FOOT at the bottom.
        assert len(b.entry_ports) == 4
        assert len(b.exit_ports) == 4
        assert ms.candidate_pairs == 16

    def test_every_candidate_is_returned_with_a_reason(self, synthetic):
        net = synthetic(TEE)
        _, _, ms, _ = stages(net, "STEM")
        assert len(ms.movements) == ms.candidate_pairs
        assert all(m.reason_code in movements.REASON_CODES for m in ms.movements)
        assert all(m.reason for m in ms.movements)


class TestDisjointClosureAcrossComponents:
    """Fixture 8: the closed parts were never connected to each other."""

    def test_no_movement_spans_the_two_islands(self, synthetic):
        net = synthetic(TWO_ISLANDS)
        lid = net.link_id("I1A")
        c = closure_mod.resolve(net.snapshot_id, lid)
        # Hand-built disjoint closure: one link from each island.
        removed_links = sorted([net.link_id("I1A"), net.link_id("I2A")])
        removed_arcs = sorted(int(r["arc_id"]) for r in db.query(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND link_id = ANY(%s)",
            (net.snapshot_id, removed_links)))
        b = ports.derive(net.snapshot_id, removed_links, lid, c.fingerprint,
                         shape="disjoint")
        ms = movements.identify(b, removed_arcs)
        assert ms.status == "OK"
        assert b.closure_component_count == 2
        assert b.is_disjoint is True

        # Pairs are formed WITHIN a piece, so a cross-island pair is never
        # routed at all. It is counted, and the count is reported.
        assert ms.closure_components == 2
        assert ms.cross_component_pair_count > 0
        for m in ms.movements:
            e = next(p for p in b.entry_ports if p.port_id == m.entry_port_id)
            x = next(p for p in b.exit_ports if p.port_id == m.exit_port_id)
            assert e.closure_component_id == x.closure_component_id
        for m in ms.included:
            assert m.removed_arc_ids_used

    def test_the_far_piece_has_no_along_closure_distance(self, synthetic):
        """It is not zero. Zero says it is right beside the selection.

        The Dijkstra that measures along-closure distance is seeded from the
        selected segment's own two nodes, so a node on another piece is never
        reached. It used to fall through to a 0.0 default and sort to the FRONT
        of the port list - a piece 50 km away taking the candidate allowance
        from the one the user actually clicked.
        """
        net = synthetic(TWO_ISLANDS)
        lid = net.link_id("I1A")
        c = closure_mod.resolve(net.snapshot_id, lid)
        removed_links = sorted([net.link_id("I1A"), net.link_id("I2A")])
        b = ports.derive(net.snapshot_id, removed_links, lid, c.fingerprint,
                         shape="disjoint")

        near = [p for p in b.ports if p.in_selected_component]
        far = [p for p in b.ports if not p.in_selected_component]
        assert near and far
        assert all(p.distance_from_selected_m is not None for p in near)
        assert all(p.distance_from_selected_m is None for p in far)
        # And the selected piece's ports come first, so truncation keeps them.
        assert b.entry_ports[0].in_selected_component is True
        assert b.exit_ports[0].in_selected_component is True

    def test_a_split_source_feature_keeps_the_clicked_piece_first(
            self, synthetic):
        """The realistic shape: ONE source feature, two pieces 50 km apart.

        Reproduced before the fix: all four ports on the far piece reported
        0.0 m and sorted ahead of every port on the piece that was clicked.
        """
        net = synthetic(SPLIT_FEATURE)
        near = db.query_one(
            "SELECT link_id FROM links WHERE snapshot_id=%s "
            "  AND closure_group_id='SPLIT' AND ST_X(ST_StartPoint(geom_2193)) < 1000",
            (net.snapshot_id,))
        lid = int(near["link_id"])
        c = closure_mod.resolve(net.snapshot_id, lid, scope="source_feature")
        assert c.removed_link_count == 2
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid,
                         c.fingerprint, shape=c.shape)

        assert b.closure_component_count == 2
        far = [p for p in b.ports if not p.in_selected_component]
        assert len(far) == 4
        assert {p.distance_from_selected_m for p in far} == {None}
        # Two ports of each kind on each piece, and within EACH list the
        # clicked piece comes first. (`b.ports` concatenates the two lists, so
        # a slice of it spans both kinds and proves nothing about ordering.)
        for side in (b.entry_ports, b.exit_ports):
            assert [p.in_selected_component for p in side] == \
                [True, True, False, False]
        assert "disconnected piece" in b.detail


class TestDeadEndSpur:
    """A cul-de-sac has no through traffic, and that is the answer.

    78 of the first 500 sampled national links are this shape - by far the
    commonest reason a closure has no movement - and V1 reports 74 of them as
    "No endpoint route", which reads as a failed analysis. Nothing failed:
    there was never a trip through a cul-de-sac.
    """

    SPUR = [
        {"id": "THROUGH_W", "pts": [(0, 0), (100, 0)], "road_name": "Main Road"},
        {"id": "THROUGH_E", "pts": [(100, 0), (200, 0)], "road_name": "Main Road"},
        {"id": "SPUR", "pts": [(100, 0), (100, 150)], "road_name": "The Close"},
    ]

    def test_every_crossing_is_at_one_node(self, synthetic):
        net = synthetic(self.SPUR)
        _, b, _, _ = stages(net, "SPUR")
        assert {p.closure_node for p in b.ports} == set(b.boundary_nodes)
        assert len(b.boundary_nodes) == 1
        # The dead end is reachable from nothing once the spur is closed.
        assert len(b.interior_nodes) == 1

    def test_no_through_movement_and_the_reason_names_the_shape(self, synthetic):
        net = synthetic(self.SPUR)
        _, _, ms, _ = stages(net, "SPUR")
        assert ms.status == "OK"
        assert ms.included == []
        assert "same place" in ms.detail
        assert "only trips to and from it" in ms.detail

    def test_the_headline_says_so_rather_than_reporting_a_failure(
            self, synthetic):
        net = synthetic(self.SPUR)
        r = analyse(net, "SPUR")
        assert r.headline == "No through movement identified"
        assert r.headline in impactv2.HEADLINES

    def test_closing_it_strands_nothing_but_itself(self, synthetic):
        """And the isolation block says that separately, in its own words."""
        net = synthetic(self.SPUR)
        r = impactv2.analyse(net.snapshot_id, net.link_id("SPUR"),
                             with_isolation=True)
        assert r.isolation is not None
        assert r.isolation.separated_link_count == 0
        assert r.isolation.physically_isolates is False


class TestGradeSeparatedCrossing:
    """Fixture 9: an overbridge is not a junction."""

    def test_closing_one_road_leaves_the_other_alone(self, synthetic):
        net = synthetic(GRADE_SEPARATED)
        _, b, ms, _ = stages(net, "EW")
        # Nothing meets EW, because the crossing was never noded.
        assert b.entry_ports == [] and b.exit_ports == []
        assert ms.included == []
        assert "at least one of each" in ms.detail


class TestDirectionOnlyClosure:
    """Fixture 17: one traversal withdrawn; the road is still there."""

    def test_only_the_closed_direction_loses_its_movement(self, synthetic):
        net = synthetic(SQUARE)
        lid = net.link_id("S")
        c = closure_mod.resolve(net.snapshot_id, lid, scope="direction",
                                direction="forward")
        assert len(c.removed_arc_ids) == 1
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid,
                         c.fingerprint, shape=c.shape)
        ms = movements.identify(b, c.removed_arc_ids)
        # Only the forward crossing used the withdrawn arc.
        assert len(ms.included) == 1
        (m,) = ms.included
        assert m.removed_arc_ids_used == c.removed_arc_ids

    def test_the_open_direction_still_routes(self, synthetic):
        net = synthetic(SQUARE)
        lid = net.link_id("S")
        c = closure_mod.resolve(net.snapshot_id, lid, scope="direction",
                                direction="forward")
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid,
                         c.fingerprint, shape=c.shape)
        ms = movements.identify(b, c.removed_arc_ids)
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        (p,) = rs.paths
        # Going round the other three sides, 300 m, exactly as for a full
        # segment closure - the reverse arc is still there but leads the wrong
        # way for this movement.
        assert p.status == "OK"
        assert p.replacement_distance_m == pytest.approx(300.0)


class TestBranchingSourceFeature:
    """Fixtures 6 and 18: the AMDS feature is not the road.

    The clicked child is 2 m long and its parent is 2.5 km. `segment` scope
    must close the 2 m; `source_feature` scope must close all of it and say so.
    """

    @staticmethod
    def _smallest_child(net):
        """The shortest graph child of the trunk, found rather than assumed."""
        row = db.query_one(
            "SELECT link_id, length_m FROM links "
            " WHERE snapshot_id=%s AND amds_id LIKE 'TRUNK%%' "
            " ORDER BY length_m, link_id LIMIT 1", (net.snapshot_id,))
        return int(row["link_id"]), float(row["length_m"])

    def test_segment_scope_closes_only_the_clicked_child(self, synthetic):
        net = synthetic(LONG_FEATURE)
        lid, length = self._smallest_child(net)
        assert length == pytest.approx(2.0), "the 2 m child is the one clicked"
        c = closure_mod.resolve(net.snapshot_id, lid)
        assert c.removed_link_ids == [lid]
        assert c.selected_segment_length_m == pytest.approx(2.0)
        assert c.warning is None

    def test_a_two_metre_closure_has_a_six_hundred_metre_detour(self, synthetic):
        """300 m down the first side road, 2 m across, 300 m back up."""
        net = synthetic(LONG_FEATURE)
        lid, _ = self._smallest_child(net)
        c = closure_mod.resolve(net.snapshot_id, lid)
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid,
                         c.fingerprint, shape=c.shape)
        ms = movements.identify(b, c.removed_arc_ids)
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        ok = [p for p in rs.paths if p.status == "OK"]
        assert ok
        for p in ok:
            assert p.intact_distance_m == pytest.approx(2.0)
            assert p.replacement_distance_m == pytest.approx(602.0)

    def test_source_feature_scope_closes_the_whole_parent_and_warns(
            self, synthetic):
        net = synthetic(LONG_FEATURE)
        lid, _ = self._smallest_child(net)
        c = closure_mod.resolve(net.snapshot_id, lid, scope="source_feature")
        assert len(c.removed_link_ids) > 1
        assert c.total_closure_length_m == pytest.approx(2500.0)
        assert c.warning is not None
        assert c.warning["code"] == "SOURCE_FEATURE_SCOPE_EXCEEDS_SELECTION"
        assert c.selected_segment_length_m == pytest.approx(2.0)

    def test_the_closure_is_not_reduced_to_the_clicked_childs_endpoints(
            self, synthetic):
        """The Tokoroa failure, stated as a property.

        With the whole 2.5 km parent closed, the open network meets it in more
        than two places, so the analysis has more than one pair of ends to
        measure between. V1 reduced this to the parent's own two nodes - a
        correct number attached to a question nobody asked.
        """
        net = synthetic(LONG_FEATURE)
        lid, _ = self._smallest_child(net)
        c = closure_mod.resolve(net.snapshot_id, lid, scope="source_feature")
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid,
                         c.fingerprint, shape=c.shape)
        ms = movements.identify(b, c.removed_arc_ids)
        assert b.reduces_to_endpoints is False
        nodes = {n for m in ms.included for n in (m.from_node, m.to_node)}
        assert len(nodes) > 2, (
            "a 2.5 km closure meeting three roads cannot be described by one "
            "pair of endpoints")


class TestAmbiguousGeometricBranch:
    """Fixture 12: straight ahead is not the same as the same road."""

    def test_continuity_evidence_prefers_the_named_road(self, synthetic):
        net = synthetic(AMBIGUOUS_BRANCH)
        r = analyse(net, "R2")
        assert r.corridor is not None
        named = [p for p in r.corridor.upstream + r.corridor.downstream
                 if "ROAD_NAME_CONTINUES" in p.evidence]
        straight_only = [p for p in r.corridor.upstream + r.corridor.downstream
                         if p.evidence == ["HEADING_CONTINUOUS"]]
        assert named, "the walk must record where the road name carried on"
        # A candidate whose ONLY evidence is heading must never outrank one
        # that carries the road name, whatever the geometry looks like.
        for a in named:
            for b_ in straight_only:
                assert a.continuity_rank >= b_.continuity_rank or \
                       a.outward_distance_m != b_.outward_distance_m


class TestStableIdTieBreak:
    """Fixture 13: everything ties, so the identifier decides - reproducibly."""

    def test_a_perfect_tie_still_yields_one_answer(self, synthetic):
        net = synthetic(SYMMETRIC)
        r = analyse(net, "MID")
        ok = [p for p in r.replacements.paths if p.status == "OK"]
        assert ok
        # Both loops are 2 x sqrt(100^2 + 150^2) = 360.555... m.
        costs = {round(p.replacement_distance_m, 3) for p in ok}
        assert len(costs) == 1, "the two ways round are exactly equal"

    def test_the_same_tie_resolves_the_same_way_every_time(self, synthetic):
        net = synthetic(SYMMETRIC)
        seen = set()
        for _ in range(5):
            r = analyse(net, "MID")
            seen.add(tuple(sorted(
                (p.movement_id, p.status, round(p.replacement_distance_m or -1, 3))
                for p in r.replacements.paths)))
        assert len(seen) == 1


class TestGeometry:
    """Phase 9: what may be drawn, and what may not."""

    def test_a_clean_route_is_one_continuous_piece(self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3", with_geometry=True)
        g = r.replacement_geometry
        assert g is not None
        assert g.continuous is True
        assert len(g.pieces) == 1
        assert g.gaps == []
        assert g.animation_safe is True

    def test_a_gap_splits_the_line_and_is_never_bridged(self, synthetic):
        """The geometry is nudged in the DATABASE, leaving the nodes alone.

        That is the real defect this guards against: AMDS links that share an
        inferred node while their drawn ends sit metres apart. It cannot be
        produced through the fixture loader, because node assignment works to a
        10 mm tolerance and would simply not join them.
        """
        net = synthetic(CHAIN)
        lid = net.link_id("B")
        # Shift link B's geometry 3 m north. Its nodes are untouched, so the
        # graph is unchanged and only the drawing is discontinuous.
        db.execute(
            "UPDATE links SET geom_4326 = ST_Transform("
            "  ST_Translate(ST_Transform(geom_4326, 2193), 0, 3.0), 4326), "
            "  geom_2193 = ST_Translate(geom_2193, 0, 3.0) "
            " WHERE snapshot_id=%s AND link_id=%s", (net.snapshot_id, lid))

        arcs = [int(r["arc_id"]) for r in db.query(
            "SELECT a.arc_id FROM arcs a JOIN links l "
            "    ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id "
            " WHERE a.snapshot_id=%s AND a.direction='forward' "
            " ORDER BY ST_X(ST_StartPoint(l.geom_2193))", (net.snapshot_id,))]
        g = routegeom.assemble(net.snapshot_id, arcs)

        assert g.has_gaps is True
        assert g.continuous is False
        assert g.animation_safe is False, "a gapped route must not be animated"
        assert len(g.pieces) == 3, "one piece per contiguous run"
        assert len(g.gaps) == 2
        for gap in g.gaps:
            assert gap.distance_m == pytest.approx(3.0, abs=0.01)
        assert "GEOMETRY_GAP" in g.quality_flags

        # And nothing invented: every coordinate emitted belongs to a real
        # piece, and no piece bridges the hole.
        assert as_flat(g) == as_flat_expected(net.snapshot_id, arcs)

    def test_geojson_is_multilinestring_so_pieces_cannot_be_concatenated(
            self, synthetic):
        net = synthetic(SQUARE)
        r = analyse(net, "S", with_geometry=True)
        gj = routegeom.as_geojson(r.replacement_geometry)
        assert gj["type"] == "MultiLineString"

    def test_a_closure_is_a_set_of_links_and_cannot_have_gaps(self, synthetic):
        """A gap is a defect in a PATH. A closure is not a path.

        The first national sample reported a geometry gap on 237 of 500 links
        because the closure was run through the route assembler: a fifteen-child
        source feature came out as "fourteen gaps, the widest 406 m", which is
        just the distance between links that were never adjacent. A warning
        that fires on half the network teaches people to ignore it.
        """
        net = synthetic(LONG_FEATURE)
        lid, _ = TestBranchingSourceFeature._smallest_child(net)
        r = impactv2.analyse(net.snapshot_id, lid, scope="source_feature",
                             with_geometry=True, with_isolation=False)
        g = r.closure_geometry
        assert g is not None
        assert g.kind == "collection"
        # Three children of the trunk, drawn as three separate lines...
        assert len(g.pieces) == len(r.closure.removed_link_ids)
        # ...and not one of them is a gap.
        assert g.gaps == []
        assert g.has_gaps is False
        assert "GEOMETRY_GAP" not in g.quality_flags
        # A collection is still not animation-safe: sweeping along it would
        # animate link-id order, which means nothing.
        assert g.animation_safe is False

    def test_a_two_way_segment_is_drawn_once_not_out_and_back(self, synthetic):
        """Collected per LINK, not per arc.

        Assembling from arcs emits the forward traversal and then the reverse
        one, tracing the same road twice - invisible on screen and twice the
        coordinates.
        """
        net = synthetic(SQUARE)
        r = analyse(net, "S", with_geometry=True)
        g = r.selected_geometry
        assert g is not None
        assert len(g.pieces) == 1
        # The fixture's S link is a straight two-point line. Traced out and
        # back it would carry three.
        assert len(g.pieces[0]) == 2


def as_flat(g):
    return [tuple(round(c, 7) for c in pt) for piece in g.pieces for pt in piece]


def as_flat_expected(snapshot_id, arc_ids):
    """Every vertex of every arc, in order, with duplicates at joins dropped.

    Built here from the raw rows rather than from `routegeom`, so the test does
    not check the assembler against itself.
    """
    import json

    out = []
    for arc in arc_ids:
        row = db.query_one(
            "SELECT a.direction, ST_AsGeoJSON(l.geom_4326, 7) AS geom "
            "  FROM arcs a JOIN links l "
            "    ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id "
            " WHERE a.snapshot_id=%s AND a.arc_id=%s", (snapshot_id, arc))
        pts = [tuple(round(c, 7) for c in p)
               for p in json.loads(row["geom"])["coordinates"]]
        if row["direction"] == "reverse":
            pts.reverse()
        out.extend(pts)
    return out


class TestTheClosureIsRespected:
    """Two stop conditions, asserted rather than assumed."""

    @pytest.mark.parametrize("network,link", [
        (SQUARE, "S"), (FRONTAGE, "M3"), (DIVIDED, "NB1"), (TEE, "STEM"),
        (SYMMETRIC, "MID"), (COUPLET, "NB"),
    ])
    def test_no_returned_route_traverses_its_own_closure(
            self, synthetic, network, link):
        net = synthetic(network)
        c, _, ms, rs = stages(net, link)
        removed = set(c.removed_arc_ids)
        for p in rs.paths:
            assert p.traverses_own_closure is False
            assert not (set(p.arc_ids) & removed)
        # And the intact movements DO use it - that is what makes them
        # movements, and a test that only checked the negative would pass on an
        # engine that closed nothing.
        for m in ms.included:
            assert set(m.removed_arc_ids_used) <= removed
            assert m.removed_arc_ids_used

    def test_a_closure_removing_an_undeclared_arc_is_refused(self, synthetic):
        net = synthetic(SQUARE)
        c, _, ms, _ = stages(net, "S")
        smuggled = sorted(set(c.removed_arc_ids) | {999_999})
        rs = repl_mod.compute(ms, smuggled, c.removed_arc_ids,
                              c.selected_segment_length_m)
        assert rs.status == "INVALID_GRAPH"
        assert "did not declare" in rs.detail
        assert rs.paths == []


class TestTruncationWithholdsADefinitiveHeadline:
    """A bounded search may report what it found. It may not imply it found
    everything.

    The national sample recorded 10 truncated movement analyses that still
    carried "Through movement diverts" or "has no represented replacement".
    Each of those reads as a statement about every movement the closure
    interrupts, and an unevaluated pair could hold the worst detour, the only
    disconnected movement, or the one the reader cares about.
    """

    def test_an_exhaustive_search_keeps_its_definitive_headline(self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3")
        assert r.movement_set.exhaustive is True
        assert r.headline in impactv2.DEFINITIVE_HEADLINES

    def test_a_truncated_movement_search_downgrades_to_partial(self, synthetic):
        net = synthetic(TEE)
        c, b, _, _ = stages(net, "STEM")
        ms = movements.identify(b, c.removed_arc_ids, max_ports_per_side=1)
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        assert ms.exhaustive is False
        headline, flags = impactv2._classify(ms, rs, rs.paths[0] if rs.paths
                                             else None)
        assert headline == "Partial analysis"
        assert headline in impactv2.HEADLINES
        assert headline not in impactv2.DEFINITIVE_HEADLINES
        assert "MOVEMENT_CANDIDATES_TRUNCATED" in flags
        assert "HEADLINE_WITHHELD_NOT_EXHAUSTIVE" in flags

    def test_a_truncated_corridor_also_downgrades(self, synthetic):
        net = synthetic(FRONTAGE)
        c, b, ms, rs = stages(net, "M3")

        class _Truncated:
            truncated = True
            confidence = "high"

        headline, flags = impactv2._classify(ms, rs, rs.paths[0], _Truncated())
        assert headline == "Partial analysis"
        assert "CORRIDOR_CANDIDATES_TRUNCATED" in flags

    def test_the_resolved_sub_results_stay_visible(self, synthetic):
        """Downgrading the headline must not hide what WAS established.

        FRONTAGE with TWO ports per side. With one, the surviving pair on
        either fixture is the F3 entry against the F3 exit - the same link in
        and out, a U-turn - so there is no movement to keep and the test would
        pass for the wrong reason.
        """
        net = synthetic(FRONTAGE)
        c, b, _, _ = stages(net, "M3")
        ms = movements.identify(b, c.removed_arc_ids, max_ports_per_side=2)
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)

        assert ms.exhaustive is False
        assert ms.status == "OK", "the search itself resolved"
        assert ms.movements, "the pair that WAS evaluated keeps its verdict"
        assert rs.status == "OK"
        assert rs.paths, "and its replacement is still reported"
        assert rs.paths[0].replacement_distance_m == pytest.approx(900.0)

        headline, flags = impactv2._classify(ms, rs, rs.paths[0])
        assert headline == "Partial analysis"
        assert "HEADLINE_WITHHELD_NOT_EXHAUSTIVE" in flags


class TestOneBrokenPathPoisonsTheRequest:
    """A replacement that traverses its own closure is a contract failure.

    Every path in the set came from the same edge query under the same
    exclusion, so if one of them used a closed arc, none of the others can be
    trusted either - including the ones that look fine.
    """

    @staticmethod
    def _corrupt(monkeypatch, removed_arc):
        """Make the exclusion fail: hand back a path through the closure."""
        real = repl_mod.route_many_paths

        def fake(*a, **kw):
            res = real(*a, **kw)
            for key in list(res.paths):
                res.paths[key] = list(res.paths[key]) + [int(removed_arc)]
            return res

        monkeypatch.setattr(repl_mod, "route_many_paths", fake)

    def test_the_whole_set_becomes_invalid_graph(self, synthetic, monkeypatch):
        net = synthetic(SQUARE)
        c, b, ms, _ = stages(net, "S")
        self._corrupt(monkeypatch, c.removed_arc_ids[0])
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        assert rs.status == "INVALID_GRAPH"
        assert rs.resolved is False
        assert "did not take" in rs.detail

    def test_no_ordinary_headline_survives_it(self, synthetic, monkeypatch):
        net = synthetic(SQUARE)
        c, b, ms, _ = stages(net, "S")
        self._corrupt(monkeypatch, c.removed_arc_ids[0])
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        headline, flags = impactv2._classify(ms, rs, None)
        assert headline == "Analysis unresolved"
        assert "INVALID_GRAPH" in flags
        assert headline not in impactv2.DEFINITIVE_HEADLINES

    def test_a_clean_request_is_untouched_by_the_guard(self, synthetic):
        net = synthetic(SQUARE)
        _, _, _, rs = stages(net, "S")
        assert rs.status == "OK"
        assert all(p.traverses_own_closure is False for p in rs.paths)


class TestCorridorNeedsAnIntactWitness:
    """A corridor pair must be backed by a trip that actually went through.

    Every term in the choice rule is about the post-closure world. Without a
    witness, a pair can have a perfectly good replacement route while the
    cheapest intact route between those two nodes never touched the closure -
    a diversion nobody needs to make.
    """

    def test_the_chosen_pair_carries_a_valid_witness(self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3")
        w = r.corridor.witness
        assert w is not None
        assert w.valid is True
        assert w.continuous is True
        assert w.connects_chosen_nodes is True
        assert w.traverses_closure is True
        assert w.closure_arcs_used

    def test_the_witness_runs_from_the_chosen_upstream_to_the_downstream_node(
            self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3")
        up = {c.candidate_id: c for c in r.corridor.upstream}
        down = {c.candidate_id: c for c in r.corridor.downstream}
        chosen = r.corridor.chosen
        assert r.corridor.witness.from_node == up[chosen.upstream_id].node
        assert r.corridor.witness.to_node == down[chosen.downstream_id].node
        # And the REPLACEMENT joins the same two nodes.
        assert chosen.upstream_node == r.corridor.witness.from_node
        assert chosen.downstream_node == r.corridor.witness.to_node

    def test_the_witness_is_directionally_continuous_arc_by_arc(self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3")
        arcs = r.corridor.witness.arc_ids
        rows = db.query(
            "SELECT arc_id, source, target FROM arcs "
            " WHERE snapshot_id=%s AND arc_id = ANY(%s)",
            (net.snapshot_id, sorted(set(arcs))))
        ends = {int(x["arc_id"]): (int(x["source"]), int(x["target"]))
                for x in rows}
        for a, b_ in zip(arcs, arcs[1:]):
            assert ends[a][1] == ends[b_][0], "the witness jumps a node"

    def test_a_pair_with_no_intact_witness_is_never_chosen(self, synthetic):
        """Hand the search a witness that does not use the closure."""
        net = synthetic(FRONTAGE)
        lid = net.link_id("M3")
        c = closure_mod.resolve(net.snapshot_id, lid)
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid,
                         c.fingerprint, shape=c.shape)
        # An arc that is not part of the closure: no witness built from it can
        # traverse the closure, so every pair must be rejected.
        outside = db.query_one(
            "SELECT arc_id FROM arcs WHERE snapshot_id=%s "
            "  AND NOT (link_id = ANY(%s)) ORDER BY arc_id LIMIT 1",
            (net.snapshot_id, c.removed_link_ids))
        cr = corridor.select(
            b, c.removed_link_ids, c.removed_arc_ids,
            entry_ports=[b.entry_ports[0]], exit_ports=[b.exit_ports[0]],
            witness_arcs=[int(outside["arc_id"])])
        assert cr.chosen is None
        assert cr.witness_rejections
        assert "no candidate pair has a demonstrable intact trip" in cr.detail


class TestTimeoutIsNeverDisconnected:
    """The stop-condition contract, exercised end to end.

    PR 1 found this guard silently skipping on CI for its entire life, so it is
    written to run unconditionally: the database error is injected, and the
    assertion follows it all the way to the headline.
    """

    @staticmethod
    def _timeout_everything(monkeypatch):
        class _Timeout(Exception):
            pass

        def boom(*a, **kw):
            raise _Timeout("canceling statement due to statement timeout")

        monkeypatch.setattr(routing.db, "connection", boom)

    def test_route_many_paths_reports_a_timeout_as_unresolved(
            self, synthetic, monkeypatch):
        net = synthetic(SQUARE)
        self._timeout_everything(monkeypatch)
        res = routing.route_many_paths(net.snapshot_id, [0], [1])
        assert res.status == "UNRESOLVED_TIMEOUT"
        assert res.resolved is False
        assert res.paths == {}

    def test_an_unresolved_search_never_becomes_disconnected(
            self, synthetic, monkeypatch):
        net = synthetic(SQUARE)
        c, b, ms, _ = stages(net, "S")
        assert len(ms.included) == 2          # resolved before the injection

        self._timeout_everything(monkeypatch)
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        assert rs.status == "UNRESOLVED_TIMEOUT"
        assert rs.resolved is False
        assert rs.paths, "every movement must still be reported"
        for p in rs.paths:
            assert p.status == "UNRESOLVED_TIMEOUT"
            assert p.status != "DISCONNECTED"
            assert p.resolved is False
            assert p.replacement_distance_m is None

    def test_the_movement_search_timing_out_reports_no_movements_as_absent(
            self, synthetic, monkeypatch):
        net = synthetic(SQUARE)
        lid = net.link_id("S")
        c = closure_mod.resolve(net.snapshot_id, lid)
        b = ports.derive(net.snapshot_id, c.removed_link_ids, lid, c.fingerprint,
                         shape=c.shape)
        self._timeout_everything(monkeypatch)
        ms = movements.identify(b, c.removed_arc_ids)
        assert ms.status == "UNRESOLVED_TIMEOUT"
        assert ms.resolved is False
        assert ms.movements, "candidates are still reported"
        for m in ms.movements:
            assert m.reason_code == "SEARCH_UNRESOLVED"
            assert m.included is False
            # Crucially NOT "there was no such trip".
            assert m.reason_code != "NO_INTACT_ROUTE"
            assert m.reason_code != "DOES_NOT_TRAVERSE_CLOSURE"

    def test_the_headline_says_unresolved_and_not_a_finding(
            self, synthetic, monkeypatch):
        net = synthetic(SQUARE)
        c, b, ms, _ = stages(net, "S")
        self._timeout_everything(monkeypatch)
        rs = repl_mod.compute(ms, c.removed_arc_ids, c.removed_arc_ids,
                              c.selected_segment_length_m)
        headline, flags = impactv2._classify(ms, rs, None)
        assert headline == "Analysis unresolved"
        assert headline in impactv2.HEADLINES
        assert "REPLACEMENT_SEARCH_UNRESOLVED" in flags


class TestBoundedCandidates:
    """A candidate count that grows without a defensible bound is a stop
    condition. The bound is enforced, reported, and its victims are listed."""

    def test_the_pair_count_is_capped_and_the_cap_is_reported(self, synthetic):
        net = synthetic(TEE)
        _, b, _, _ = stages(net, "STEM")
        ms = movements.identify(b, [0], max_ports_per_side=1)
        assert ms.candidate_pairs == 1
        assert ms.truncated is True
        assert ms.exhaustive is False

    def test_omitted_pairs_are_counted_exactly_and_sampled_boundedly(
            self, synthetic):
        """Counts are exact; the LIST is a worked example, not a manifest.

        Returning a row per omitted pair made the audit payload the whole
        dropped cross-product - a bounded computation with an unbounded report,
        which is still unbounded.
        """
        net = synthetic(TEE)
        _, b, _, _ = stages(net, "STEM")
        ms = movements.identify(b, [0], max_ports_per_side=1)

        # 4 entry x 4 exit = 16 pairs; one evaluated, fifteen not.
        assert ms.omitted_pair_count == 15
        assert ms.omitted_entry_ports == 3
        assert ms.omitted_exit_ports == 3
        assert 0 < len(ms.omitted_pair_sample) <= movements.OMITTED_SAMPLE_LIMIT
        assert all("reason" in d for d in ms.omitted_pair_sample)

    def test_the_omitted_sample_is_the_same_on_every_run(self, synthetic):
        net = synthetic(TEE)
        _, b, _, _ = stages(net, "STEM")
        seen = {tuple((d["entryStableKey"], d["exitStableKey"])
                      for d in movements.identify(
                          b, [0], max_ports_per_side=1).omitted_pair_sample)
                for _ in range(4)}
        assert len(seen) == 1

    def test_an_exhaustive_search_says_so(self, synthetic):
        net = synthetic(SQUARE)
        _, _, ms, _ = stages(net, "S")
        assert ms.truncated is False
        assert ms.omitted_pair_count == 0
        assert ms.exhaustive is True

    def test_corridor_bounds_are_declared(self, synthetic):
        net = synthetic(FRONTAGE)
        r = analyse(net, "M3")
        assert r.corridor.bounds["beamWidth"] == corridor.BEAM_WIDTH
        assert r.corridor.bounds["maxHops"] == corridor.MAX_HOPS
        assert isinstance(r.corridor.truncated, bool)


class TestRowOrderIndependence:
    """Fixture 16. Shuffling the input reassigns every link, arc and node id.

    The existing port test re-derives from ONE snapshot five times, which
    exercises the query plan but not the id assignment. This rebuilds the
    network from scratch with the features in a different order, which is the
    case the brief actually names, and which found a real defect: the corridor
    tie-break used to hash `arc_id` and flipped on three seeds in eight.
    """

    @staticmethod
    def _digest(r):
        """Everything a reader sees, named so that no id appears in it."""
        p, m = r.principal, r.principal_movement
        d = {
            "headline": r.headline,
            "included": len(r.movement_set.included),
            "candidates": r.movement_set.candidate_pairs,
            "status": None if p is None else p.status,
            "intact": None if p is None else _r(p.intact_distance_m),
            "replacement": None if p is None else _r(p.replacement_distance_m),
            "penalty": None if p is None else _r(p.network_penalty_m),
            "movement": None if m is None else m.key,
        }
        if r.corridor and r.corridor.chosen:
            up = {c.candidate_id: c for c in r.corridor.upstream}
            dn = {c.candidate_id: c for c in r.corridor.downstream}
            u = up[r.corridor.chosen.upstream_id]
            v = dn[r.corridor.chosen.downstream_id]
            d["corridor"] = (u.stable_key, v.stable_key,
                             _r(u.outward_distance_m), _r(v.outward_distance_m),
                             _r(r.corridor.chosen.replacement_cost_m),
                             r.corridor.admissibility_level)
        else:
            d["corridor"] = None
        return d

    @pytest.mark.parametrize("network,link", [
        (FRONTAGE, "M3"), (SQUARE, "S"), (SYMMETRIC, "MID"), (TEE, "STEM"),
        (DIVIDED, "NB1"), (ENDPOINT_ARTEFACT, "MB"),
    ])
    def test_nothing_a_reader_sees_depends_on_insertion_order(
            self, synthetic, network, link):
        baseline = None
        for seed in range(6):
            spec = list(network)
            random.Random(seed).shuffle(spec)
            net = synthetic(spec)
            got = self._digest(analyse(net, link))
            if baseline is None:
                baseline = got
            assert got == baseline, f"seed {seed} changed the reported result"


def _r(v):
    return None if v is None else round(v, 3)
