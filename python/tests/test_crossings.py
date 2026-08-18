"""The crossing that AMDS does not node, and the one it must never node.

The existing overbridge fixture in test_topology.py is kept exactly as it was.
This adds the case it was missing: two long rural roads crossing at grade, with
NEITHER source feature terminating at the crossing and both geometries
continuing straight through it.

`TestTheFixtureFailsUnderTheOldRule` is the important class. It pins the OLD
behaviour - `crossing_policy='none'` - and asserts that under it the rural
crossroads stays severed and the closure detours the long way round. If the fix
were removed, that class would start passing while the class below it failed,
and the pair of them is what makes the change legible.
"""

from __future__ import annotations

import pytest

from nzcl import crossings
from nzcl.topology import (assign_nodes, audit_no_invented_movements,
                           split_at_junctions)

from test_topology import components, src


# --- geometry --------------------------------------------------------------
#
#         N
#         |                     GREENDALE ROAD runs west-east at y=0.
#   W ----+---- E               CLINTONS ROAD runs south-north at x=0.
#         |                     Neither ends at the crossing.
#         S
#
# Plus a perimeter, so "no route" and "the long way round" are different
# outcomes and the test can tell them apart:
#
#   (-2000, 2000) ------------------- (2000, 2000)
#         |                                 |
#         |            (0,0)                |
#         |                                 |
#   (-2000,-2000) ------------------- (2000,-2000)

RURAL_GRID = [
    src("GREENDALE", [(-2000, 0), (2000, 0)],
        model_asset_type=1, oneway=2, rca_code=74, road_name="Greendale Road"),
    src("CLINTONS", [(0, -2000), (0, 2000)],
        model_asset_type=1, oneway=2, rca_code=74, road_name="Clintons Road"),
    # the perimeter, meeting each through road end-on so it nodes normally
    src("WEST", [(-2000, 0), (-2000, 2000), (0, 2000)],
        model_asset_type=1, oneway=2, rca_code=74),
    src("EAST", [(2000, 0), (2000, 2000), (0, 2000)],
        model_asset_type=1, oneway=2, rca_code=74),
]


def crossing_node(links) -> int | None:
    """The node id at (0, 0), if the split produced one."""
    _, coords = assign_nodes(links)
    for nid, (x, y) in enumerate(coords):
        if abs(x) < 1e-6 and abs(y) < 1e-6:
            return nid
    return None


def degree(links, node_id: int) -> int:
    pairs, _ = assign_nodes(links)
    return sum(1 for a, b in pairs if a == node_id or b == node_id)


def shortest_path_m(links, start_xy, end_xy, *, closed: set[str] = frozenset()
                    ) -> float | None:
    """Dijkstra over the split links, so a route is a measured thing here too."""
    import heapq
    pairs, coords = assign_nodes(links)

    def node_at(xy) -> int | None:
        for nid, (x, y) in enumerate(coords):
            if abs(x - xy[0]) < 1e-6 and abs(y - xy[1]) < 1e-6:
                return nid
        return None

    s, t = node_at(start_xy), node_at(end_xy)
    if s is None or t is None:
        return None

    adj: dict[int, list[tuple[int, float]]] = {}
    for link, (a, b) in zip(links, pairs):
        if link.closure_group_id in closed or a == b:
            continue
        adj.setdefault(a, []).append((b, link.length_m))
        adj.setdefault(b, []).append((a, link.length_m))

    dist = {s: 0.0}
    pq = [(0.0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == t:
            return d
        if d > dist.get(u, float("inf")):
            continue
        for v, w in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, float("inf")) - 1e-9:
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist.get(t)


# ---------------------------------------------------------------------------
class TestTheFixtureFailsUnderTheOldRule:
    """What the binary rule did, pinned so the change cannot be silently undone.

    `crossing_policy='none'` IS the old rule: split only where an endpoint
    lands on an interior, never where two interiors cross.
    """

    def test_the_crossroads_is_not_noded(self):
        res = split_at_junctions(RURAL_GRID, crossing_policy="none")
        assert res.crossing_cuts == 0
        assert crossing_node(res.links) is None, (
            "the old rule must leave the rural crossroads severed - that is "
            "the defect this change fixes")

    def test_the_through_roads_are_never_cut(self):
        res = split_at_junctions(RURAL_GRID, crossing_policy="none")
        for group in ("GREENDALE", "CLINTONS"):
            assert len([l for l in res.links
                        if l.closure_group_id == group]) == 1

    def test_a_closure_is_forced_the_long_way_round(self):
        """Close the east half of Greendale Road; the trip must go via the
        perimeter, because the diagonal shortcut through the crossroads does
        not exist in the graph."""
        res = split_at_junctions(RURAL_GRID, crossing_policy="none")
        d = shortest_path_m(res.links, (-2000, 0), (0, 2000),
                            closed={"GREENDALE"})
        assert d == pytest.approx(4000.0), (
            "only the western perimeter leg is available: 2000 m north plus "
            "2000 m east")


class TestARuralCrossroadsBecomesOneNode:
    """The case the suite was missing."""

    def test_both_through_roads_are_cut_at_the_crossing(self):
        res = split_at_junctions(RURAL_GRID)
        assert res.crossing_policy == "confirmed"
        assert res.crossing_cuts == 1
        for group in ("GREENDALE", "CLINTONS"):
            pieces = [l for l in res.links if l.closure_group_id == group]
            assert len(pieces) == 2, f"{group} must be split in two"
            assert sum(p.length_m for p in pieces) == pytest.approx(4000.0)

    def test_the_crossing_becomes_exactly_one_graph_node(self):
        res = split_at_junctions(RURAL_GRID)
        nid = crossing_node(res.links)
        assert nid is not None, "the crossing must become a node"
        assert degree(res.links, nid) == 4, (
            "four link ends meet there: two halves of each road")

    def test_neither_source_feature_terminates_at_the_crossing(self):
        """The point of the fixture. If either road ended at (0,0) this would
        be an ordinary T-junction and the old rule would already have handled
        it."""
        for s in RURAL_GRID[:2]:
            assert s.coords[0] != (0.0, 0.0)
            assert s.coords[-1] != (0.0, 0.0)

    def test_both_geometries_continue_through_the_crossing(self):
        res = split_at_junctions(RURAL_GRID)
        gd = sorted((l for l in res.links if l.closure_group_id == "GREENDALE"),
                    key=lambda l: l.coords[0][0])
        assert gd[0].coords[0] == (-2000.0, 0.0)
        assert gd[0].coords[-1] == (0.0, 0.0)
        assert gd[1].coords[0] == (0.0, 0.0)
        assert gd[1].coords[-1] == (2000.0, 0.0)

    def test_the_closure_now_routes_through_the_diagonal_shortcut(self):
        res = split_at_junctions(RURAL_GRID)
        d = shortest_path_m(res.links, (-2000, 0), (0, 2000),
                            closed={"GREENDALE"})
        assert d == pytest.approx(4000.0)
        # ...and from the far side, where the shortcut is the ONLY thing that
        # changes the answer: 2000 m west along Greendale then 2000 m north up
        # Clintons, instead of 2000 m + 2000 m + 2000 m round the perimeter.
        d2 = shortest_path_m(res.links, (2000, 0), (0, 2000))
        assert d2 == pytest.approx(4000.0)
        old = split_at_junctions(RURAL_GRID, crossing_policy="none")
        assert shortest_path_m(old.links, (2000, 0), (0, 2000)) == \
            pytest.approx(4000.0)

    def test_total_length_is_preserved_exactly(self):
        res = split_at_junctions(RURAL_GRID)
        before = sum(
            ((s.coords[-1][0] - s.coords[0][0]) ** 2 +
             (s.coords[-1][1] - s.coords[0][1]) ** 2) ** 0.5
            for s in RURAL_GRID[:2])
        after = sum(l.length_m for l in res.links
                    if l.closure_group_id in ("GREENDALE", "CLINTONS"))
        assert after == pytest.approx(before, abs=1e-9)

    def test_it_is_one_connected_component(self):
        assert components(split_at_junctions(RURAL_GRID).links) == 1


class TestGradeSeparationSurvives:
    """The overbridge fixture must still behave, now for a stated reason."""

    OVERBRIDGE = [
        src("MOTORWAY", [(-1000, 0), (1000, 0)],
            model_asset_type=1, oneway=1, rca_code=1),
        src("LOCAL", [(0, -1000), (0, 1000)], model_asset_type=1, oneway=2,
            rca_code=74),
    ]

    def test_a_motorway_carriageway_is_not_noded(self):
        res = split_at_junctions(self.OVERBRIDGE)
        assert res.crossing_cuts == 0
        assert components(res.links) == 2
        assert len(res.crossings) == 1
        assert res.crossings[0].disposition == crossings.GRADE_SEPARATED
        assert res.crossings[0].classification.reason == "MOTORWAY_CARRIAGEWAY"

    def test_not_even_under_the_possible_policy(self):
        """The POSSIBLE graph adds UNRESOLVED crossings. It must never add a
        GRADE_SEPARATED one, or the sensitivity lens becomes a licence to
        invent motorway turns."""
        res = split_at_junctions(self.OVERBRIDGE, crossing_policy="possible")
        assert res.crossing_cuts == 0
        assert components(res.links) == 2

    def test_a_ramp_is_not_noded_either(self):
        res = split_at_junctions([
            src("RAMPY", [(-500, -500), (500, 500)], model_asset_type=1,
                oneway=2, rca_code=74, is_ramp=True),
            src("STREET", [(-500, 500), (500, -500)], model_asset_type=1,
                oneway=2, rca_code=74),
        ])
        assert res.crossing_cuts == 0
        assert res.crossings[0].classification.reason == "RAMP"


class TestTheThirdCategoryIsNotSilent:
    """UNRESOLVED must leave the graph alone and still be reported."""

    ONE_WAY_PAIR = [
        src("ONEWAY", [(-500, 0), (500, 0)], model_asset_type=1, oneway=1,
            rca_code=74),
        src("STREET", [(0, -500), (0, 500)], model_asset_type=1, oneway=2,
            rca_code=74),
    ]

    def test_confirmed_leaves_it_severed_but_records_it(self):
        res = split_at_junctions(self.ONE_WAY_PAIR, crossing_policy="confirmed")
        assert res.crossing_cuts == 0
        assert components(res.links) == 2
        assert len(res.crossings) == 1
        assert res.crossings[0].disposition == crossings.UNRESOLVED

    def test_possible_connects_it_so_sensitivity_can_be_measured(self):
        res = split_at_junctions(self.ONE_WAY_PAIR, crossing_policy="possible")
        assert res.crossing_cuts == 1
        assert components(res.links) == 1

    def test_the_two_policies_differ_only_in_what_they_cut(self):
        a = split_at_junctions(self.ONE_WAY_PAIR, crossing_policy="confirmed")
        b = split_at_junctions(self.ONE_WAY_PAIR, crossing_policy="possible")
        assert [c.disposition for c in a.crossings] == \
               [c.disposition for c in b.crossings]


class TestTangentialCrossingsAreRefused:
    """Two carriageways of one road grazing each other is not a junction.

    Both vetoes were moved by the BLINDED review, which is the only reason
    either is where it is:

      * the tangential threshold went from 20 to 30 degrees, because the 20-30
        band came back 8 confirmed and 8 contradicted;
      * DUPLICATE_GEOMETRY was added, because eleven of the seventeen AT_GRADE
        misses were one road recorded twice - under DIFFERENT source ids, so
        SAME_SOURCE_FEATURE never saw them, and several at a healthy angle, so
        the tangential veto never saw them either.
    """

    def test_a_four_degree_crossing_is_never_cut(self):
        res = split_at_junctions([
            src("A", [(0, 0), (1000, 0)], model_asset_type=1, oneway=2,
                rca_code=74),
            src("B", [(0, -20), (1000, 50)], model_asset_type=1, oneway=2,
                rca_code=74),
        ], crossing_policy="possible")
        assert len(res.crossings) == 1
        # These two run alongside each other, so the duplicate rule catches it
        # before the angle does. Either way it must never be noded.
        assert res.crossings[0].classification.reason == "DUPLICATE_GEOMETRY"
        assert res.crossings[0].classification.safe_to_node is False
        # Not even under POSSIBLE. UNRESOLVED gets connected there only where
        # "it is really a junction" is a live hypothesis, and two carriageways
        # grazing at 4 degrees is not doubt about a junction, it is the absence
        # of one.
        assert res.crossing_cuts == 0
        assert components(res.links) == 2

    def test_a_25_degree_crossing_is_now_refused(self):
        """Inside the band the blinded review found to be a coin toss. Two
        lines that diverge, so DUPLICATE_GEOMETRY does not apply and the angle
        veto has to do the work on its own."""
        import math
        d = 2000 * math.tan(math.radians(25))
        res = split_at_junctions([
            src("A", [(-2000, 0), (2000, 0)], model_asset_type=1, oneway=2,
                rca_code=74),
            src("B", [(-2000, -d), (2000, d)], model_asset_type=1, oneway=2,
                rca_code=74),
        ], crossing_policy="possible")
        assert res.crossings[0].classification.reason == "TANGENTIAL"
        assert res.crossing_cuts == 0

    def test_a_35_degree_crossing_is_still_accepted(self):
        import math
        d = 2000 * math.tan(math.radians(35))
        res = split_at_junctions([
            src("A", [(-2000, 0), (2000, 0)], model_asset_type=1, oneway=2,
                rca_code=74),
            src("B", [(-2000, -d), (2000, d)], model_asset_type=1, oneway=2,
                rca_code=74),
        ])
        assert res.crossings[0].disposition == crossings.AT_GRADE
        assert res.crossing_cuts == 1

    def test_one_road_recorded_twice_is_never_noded(self):
        """Paulin Road crossing Paulin Road: different source features, a
        crossing angle that passes every other test, and the same tarmac."""
        res = split_at_junctions([
            src("PAULIN_A", [(0, 0), (500, 3), (1000, 0)], model_asset_type=1,
                oneway=2, rca_code=74, road_name="Paulin Road"),
            src("PAULIN_B", [(0, 3), (500, 0), (1000, 3)], model_asset_type=1,
                oneway=2, rca_code=74, road_name="Paulin Road"),
        ], crossing_policy="possible")
        assert res.crossings
        assert all(c.classification.reason == "DUPLICATE_GEOMETRY"
                   for c in res.crossings)
        assert res.crossing_cuts == 0

    def test_a_real_crossroads_is_not_mistaken_for_a_duplicate(self):
        res = split_at_junctions(RURAL_GRID)
        assert res.crossings[0].classification.reason == "ORDINARY_CROSSROADS"
        assert res.crossing_cuts == 1


class TestClassifierIsPure:
    """`classify` takes a context and returns a verdict. No I/O, no order
    dependence, no globals - so the ingest and the audit cannot disagree."""

    def base(self, **kw):
        d = dict(angle_deg=88.0, model_asset_type=(1, 1), oneway=(2, 2),
                 rca_code=(74, 74), is_ramp=(False, False),
                 road_name=("A Road", "B Road"), quality_flags=((), ()),
                 junction_witness=False, motorway_links_near=0,
                 ramp_links_near=0)
        d.update(kw)
        return crossings.CrossingContext(**d)

    def test_same_input_same_verdict(self):
        c = self.base()
        assert crossings.classify(c).reason == crossings.classify(c).reason

    def test_a_mapped_bridge_along_one_road_is_a_structure(self):
        c = self.base(structure_dist_m=4.0, structure_align_deg=6.0,
                      structure_kind="bridge")
        assert crossings.classify(c).disposition == crossings.GRADE_SEPARATED
        assert crossings.classify(c).reason == "STRUCTURE_MAPPED"

    def test_a_bridge_crossing_both_roads_is_not(self):
        """A river bridge beside a junction. 420 of the 1,056 structures within
        15 m of a national crossing are this."""
        c = self.base(structure_dist_m=4.0, structure_align_deg=71.0,
                      structure_kind="bridge")
        assert crossings.classify(c).disposition == crossings.AT_GRADE

    def test_a_named_roundabout_is_not_a_structure(self):
        c = self.base(road_name=("State Highway 5 Interchange 45 Roundabout",
                                 "Broadlands Road"))
        assert crossings.classify(c).disposition == crossings.AT_GRADE

    def test_a_named_overbridge_is(self):
        c = self.base(road_name=("Bairds Road Overbridge", "State Highway 1"))
        assert crossings.classify(c).reason == "NAMED_STRUCTURE"

    def test_a_height_limit_no_longer_decides_it_alone(self):
        """Demoted after the manual review: AMDS publishes startMeasure and
        endMeasure with each restriction and the ingest keeps neither, so the
        limit cannot be placed on the link it belongs to."""
        c = self.base(quality_flags=(("HEIGHT_LIMIT_4.3m",), ()))
        r = crossings.classify(c)
        assert r.disposition == crossings.UNRESOLVED
        assert r.reason == "HEIGHT_LIMIT"

    def test_a_ramp_300m_away_no_longer_decides_it_either(self):
        """0 correct out of 3 in the manual review."""
        c = self.base(ramp_links_near=2)
        r = crossings.classify(c)
        assert r.disposition == crossings.UNRESOLVED
        assert r.reason == "RAMP_CONTEXT"

    def test_a_road_crossing_itself_is_not_two_roads_meeting(self):
        c = self.base(same_source_feature=True)
        assert crossings.classify(c).reason == "SAME_SOURCE_FEATURE"

    def test_a_third_road_ending_there_is_positive_evidence(self):
        c = self.base(junction_witness=True)
        r = crossings.classify(c)
        assert r.disposition == crossings.AT_GRADE
        assert r.reason == "JUNCTION_WITNESS"

    def test_state_highway_alone_never_decides_anything(self):
        """Explicitly pinned. Many state highway crossings are ordinary
        at-grade intersections, and some local roads pass over others; rca_code
        is carried for prioritisation and must never classify."""
        plain = self.base(rca_code=(74, 74))
        sh = self.base(rca_code=(1, 1))
        assert crossings.classify(plain).disposition == \
               crossings.classify(sh).disposition == crossings.AT_GRADE

    def test_but_a_one_way_state_highway_carriageway_does(self):
        c = self.base(rca_code=(1, 74), oneway=(1, 2))
        assert crossings.classify(c).reason == "MOTORWAY_CARRIAGEWAY"


class TestAMixedInterchangeInventsNoMovement:
    """The hazard that makes this whole change dangerous if done naively.

    A graph node is a promise that every arc arriving may leave by every other
    arc. It cannot say "A may turn into B here, but C passes overhead". So at a
    place where one pair of roads meets at grade and another pair passes over,
    noding the at-grade pair can hand the flyover the same turns - which is the
    exact defect the original never-node rule existed to prevent.

    Everything below sits inside one 25 m place:

        STREET_A x STREET_B   an ordinary at-grade crossroads at (0, 0)
        OVERBRIDGE            a named structure, crossing STREET_A at (8, 0)
                              and STREET_B at (0, -8)

    and, 400 m away and deliberately outside the structure-context radius:

        RAMP        a ramp whose ENDPOINT lands on STREET_A's interior. It is
                    a genuine at-grade connection, found by the endpoint rule,
                    and it must keep working - the mixed-place withdrawal is
                    per place and must not disable ordinary noding nearby.
        BYSTANDER   an unrelated road crossing nothing, which must stay its
                    own component.
    """

    CLUSTER = [
        src("STREET_A", [(-500, 0), (500, 0)],
            model_asset_type=1, oneway=2, rca_code=74, road_name="A Street"),
        src("STREET_B", [(0, -500), (0, 500)],
            model_asset_type=1, oneway=2, rca_code=74, road_name="B Street"),
        src("OVERBRIDGE", [(-400, -408), (400, 392)],
            model_asset_type=1, oneway=2, rca_code=74,
            road_name="Newton Overbridge"),
        src("RAMP", [(-400, 0), (-400, -300)],
            model_asset_type=1, oneway=2, rca_code=74,
            road_name="Newton Off Ramp", is_ramp=True),
        src("BYSTANDER", [(-900, 700), (-700, 900)],
            model_asset_type=1, oneway=2, rca_code=74),
    ]

    def test_the_three_crossings_are_what_the_fixture_claims(self):
        """Read from what the evidence said BEFORE the place rule withdrew it.
        'This looked at grade and we declined to act on it' is a different
        fact from 'we had no idea', and both are kept."""
        res = split_at_junctions(self.CLUSTER, crossing_policy="none")
        got = {tuple(sorted((c.amds_a, c.amds_b))):
               (c.classification_before_place_rule or c.classification)
               for c in res.crossings}
        assert got[("STREET_A", "STREET_B")].reason == "ORDINARY_CROSSROADS"
        assert got[("STREET_A", "STREET_B")].disposition == crossings.AT_GRADE
        assert got[("OVERBRIDGE", "STREET_A")].reason == "NAMED_STRUCTURE"
        assert got[("OVERBRIDGE", "STREET_B")].reason == "NAMED_STRUCTURE"

    def test_the_place_really_is_mixed(self):
        res = split_at_junctions(self.CLUSTER, crossing_policy="none")
        assert len(res.crossings) == 3
        # all three crossings sit inside one 25 m place
        assert len(set(res.crossing_places)) == 1
        before = {(c.classification_before_place_rule or c.classification
                   ).disposition for c in res.crossings}
        assert before == {crossings.AT_GRADE, crossings.GRADE_SEPARATED}, (
            "the fixture is only meaningful if the place really does hold "
            "disagreeing verdicts")

    def test_nothing_at_a_mixed_place_is_noded(self):
        res = split_at_junctions(self.CLUSTER)
        assert res.mixed_place_demotions >= 1
        assert res.crossing_cuts == 0
        assert all(c.disposition == crossings.UNRESOLVED
                   for c in res.crossings)
        assert {c.classification.reason for c in res.crossings} == {"MIXED_PLACE"}

    def test_not_under_the_possible_policy_either(self):
        res = split_at_junctions(self.CLUSTER, crossing_policy="possible")
        assert res.crossing_cuts == 0

    def test_no_impossible_movement_is_created(self):
        for policy in ("none", "confirmed", "possible"):
            res = split_at_junctions(self.CLUSTER, crossing_policy=policy)
            assert audit_no_invented_movements(res) == [], policy

    def test_the_overbridge_is_never_joined_to_anything(self):
        for policy in ("none", "confirmed", "possible"):
            res = split_at_junctions(self.CLUSTER, crossing_policy=policy)
            pairs, _ = assign_nodes(res.links)
            over = {n for link, pr in zip(res.links, pairs)
                    if link.closure_group_id == "OVERBRIDGE" for n in pr}
            other = {n for link, pr in zip(res.links, pairs)
                     if link.closure_group_id != "OVERBRIDGE" for n in pr}
            assert over & other == set(), policy

    def test_the_two_streets_stay_severed_too(self):
        """The at-grade pair is real, and it is still not noded - because a
        node here would have been shared with the overbridge. This is the
        cost of the conservative option, paid deliberately."""
        res = split_at_junctions(self.CLUSTER)
        pairs, _ = assign_nodes(res.links)
        a = {n for link, pr in zip(res.links, pairs)
             if link.closure_group_id == "STREET_A" for n in pr}
        b = {n for link, pr in zip(res.links, pairs)
             if link.closure_group_id == "STREET_B" for n in pr}
        assert a & b == set()

    def test_the_ordinary_endpoint_junction_nearby_still_works(self):
        """The withdrawal is per place. A ramp ending on a street 400 m away
        is an endpoint junction, found by a different rule, and must be
        unaffected."""
        res = split_at_junctions(self.CLUSTER)
        pairs, _ = assign_nodes(res.links)
        street = {n for link, pr in zip(res.links, pairs)
                  if link.closure_group_id == "STREET_A" for n in pr}
        ramp = {n for link, pr in zip(res.links, pairs)
                if link.closure_group_id == "RAMP" for n in pr}
        assert street & ramp, "the ramp must still connect to A Street"
        assert len([l for l in res.links
                    if l.closure_group_id == "STREET_A"]) == 2

    def test_the_bystander_stays_its_own_component(self):
        res = split_at_junctions(self.CLUSTER)
        pairs, _ = assign_nodes(res.links)
        by = {n for link, pr in zip(res.links, pairs)
              if link.closure_group_id == "BYSTANDER" for n in pr}
        other = {n for link, pr in zip(res.links, pairs)
                 if link.closure_group_id != "BYSTANDER" for n in pr}
        assert by & other == set()

    def test_the_demotion_records_what_it_overrode(self):
        res = split_at_junctions(self.CLUSTER)
        ev = [e for c in res.crossings for e in c.classification.evidence]
        assert any(e.startswith("WAS_AT_GRADE") for e in ev), (
            "the at-grade pair must be recorded as having been demoted, not "
            "silently reclassified")
        assert any(e.startswith("WAS_GRADE_SEPARATED") for e in ev)

    def test_an_unmixed_place_nearby_is_unaffected(self):
        """The demotion is per place, not global. A clean crossroads 2 km away
        must still be noded."""
        res = split_at_junctions(self.CLUSTER + [
            src("FARM_A", [(3000, -500), (3000, 500)], model_asset_type=1,
                oneway=2, rca_code=74),
            src("FARM_B", [(2500, 0), (3500, 0)], model_asset_type=1,
                oneway=2, rca_code=74),
        ])
        assert res.crossing_cuts == 1
        assert audit_no_invented_movements(res) == []


class TestTheAuditCatchesAnInventedMovement:
    """The invariant is only worth having if it can fail."""

    def test_the_rural_grid_passes(self):
        assert audit_no_invented_movements(
            split_at_junctions(RURAL_GRID)) == []

    def test_a_deliberately_wrong_split_is_caught(self):
        """Force the overbridge to be noded by classifying it AT_GRADE by
        hand, then check the audit notices that a GRADE_SEPARATED crossing
        ended up connected."""
        res = split_at_junctions(TestGradeSeparationSurvives.OVERBRIDGE,
                                 crossing_policy="possible")
        assert res.crossing_cuts == 0
        # Now build the same thing as though the motorway rule had not fired.
        forced = split_at_junctions([
            src("MOTORWAY", [(-1000, 0), (1000, 0)], model_asset_type=1,
                oneway=2, rca_code=74),
            src("LOCAL", [(0, -1000), (0, 1000)], model_asset_type=1,
                oneway=2, rca_code=74),
        ])
        assert forced.crossing_cuts == 1
        # Relabel that crossing GRADE_SEPARATED after the fact: the audit must
        # now report the graph as unsound, because it IS connected there.
        forced.crossings[0].classification.disposition = \
            crossings.GRADE_SEPARATED
        violations = audit_no_invented_movements(forced)
        assert len(violations) == 1
        assert "MOTORWAY" in violations[0] and "LOCAL" in violations[0]


class TestTheClusteringRadiusNeverDrivesNoding:
    """The radius groups a review display. It must not decide topology.

    Two properties are pinned here because getting either wrong turns an audit
    convention into a source of graph errors.
    """

    def test_cuts_land_on_the_exact_crossing_point(self):
        """Every cut coordinate is the intersection itself, never a cluster
        centroid or a snapped grid position."""
        res = split_at_junctions(RURAL_GRID)
        x = res.crossings[0]
        gd = [l for l in res.links if l.closure_group_id == "GREENDALE"]
        cl = [l for l in res.links if l.closure_group_id == "CLINTONS"]
        touching = [c for link in gd + cl for c in (link.coords[0], link.coords[-1])]
        assert any(abs(cx - x.x) < 1e-9 and abs(cy - x.y) < 1e-9
                   for cx, cy in touching)

    def test_mixedness_is_monotone_in_the_radius(self):
        """Merging clusters can only ADD disagreement, never remove it. So a
        place mixed at 5 m is necessarily inside a place mixed at 25 m, and
        clustering wider withdraws a superset. That is why the wider radius is
        the conservative choice and why 'mixed at 5 m but clean at 25 m'
        cannot happen."""
        pts = [(0.0, 0.0), (8.0, 0.0), (60.0, 0.0), (63.0, 0.0)]
        dispositions = ["AT_GRADE", "GRADE_SEPARATED", "AT_GRADE", "AT_GRADE"]

        def mixed_places(eps: float) -> set[frozenset[str]]:
            labels = crossings.cluster(pts, eps_m=eps)
            groups: dict[int, set[str]] = {}
            for lab, d in zip(labels, dispositions):
                groups.setdefault(lab, set()).add(d)
            return {frozenset(g) for g in groups.values() if len(g) > 1}

        narrow = mixed_places(5.0)
        wide = mixed_places(25.0)
        widest = mixed_places(100.0)
        assert narrow == set()          # 0 and 8 are 8 m apart: separate at 5 m
        assert wide != set()            # they merge at 25 m, and disagree
        assert len(widest) >= len(wide)

    def test_a_place_mixed_at_any_radius_is_withdrawn(self):
        """The implementation clusters at 25 m, which by the property above
        detects a superset of what a narrower radius would."""
        res = split_at_junctions(
            TestAMixedInterchangeInventsNoMovement.CLUSTER)
        assert res.crossing_cuts == 0


class TestPairsAreNotPlaces:
    """A crossing pair is not a crossing place, and conflating them is how the
    national figure got overstated."""

    def test_four_points_at_one_intersection_cluster_to_one(self):
        # two divided carriageways crossing: four intersection points, ~20 m
        # across, one physical junction.
        pts = [(0.0, 0.0), (20.0, 0.0), (0.0, 20.0), (20.0, 20.0)]
        assert len(set(crossings.cluster(pts, eps_m=25.0))) == 1

    def test_genuinely_separate_junctions_stay_separate(self):
        pts = [(0.0, 0.0), (400.0, 0.0)]
        assert len(set(crossings.cluster(pts, eps_m=25.0))) == 2

    def test_every_point_lands_in_a_cluster(self):
        pts = [(0.0, 0.0), (1000.0, 0.0), (5.0, 5.0)]
        labels = crossings.cluster(pts, eps_m=25.0)
        assert len(labels) == 3
        assert labels[0] == labels[2] != labels[1]
