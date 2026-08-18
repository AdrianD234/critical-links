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
from nzcl.topology import assign_nodes, split_at_junctions

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
    """Two carriageways of one road grazing each other is not a junction."""

    def test_a_four_degree_crossing_is_never_cut(self):
        res = split_at_junctions([
            src("A", [(0, 0), (1000, 0)], model_asset_type=1, oneway=2,
                rca_code=74),
            src("B", [(0, -20), (1000, 50)], model_asset_type=1, oneway=2,
                rca_code=74),
        ], crossing_policy="possible")
        assert len(res.crossings) == 1
        assert res.crossings[0].classification.reason == "TANGENTIAL"
        assert res.crossings[0].classification.plausible_junction is False
        # Not even under POSSIBLE. UNRESOLVED gets connected there only where
        # "it is really a junction" is a live hypothesis, and two carriageways
        # grazing at 4 degrees is not doubt about a junction, it is the absence
        # of one.
        assert res.crossing_cuts == 0
        assert components(res.links) == 2


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
