"""Junction splitting: the rule that decides whether two roads meet.

The whole model turns on the distinction tested here. Splitting where a road
ENDS on another road is what makes the network connected at all; refusing to
split where two roads merely CROSS is what preserves every overbridge, tunnel
and grade-separated interchange in the country.
"""

from __future__ import annotations

import pytest

from nzcl.topology import SourceLink, assign_nodes, split_at_junctions


def src(amds_id: str, coords, **attrs) -> SourceLink:
    return SourceLink(amds_id=amds_id, coords=[(float(x), float(y)) for x, y in coords],
                      attrs=attrs)


def components(links) -> int:
    """Count weakly connected components over the split links."""
    pairs, coords = assign_nodes(links)
    parent = list(range(len(coords)))

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return len({find(i) for i in range(len(coords))})


T_JUNCTION = [
    src("THROUGH", [(0, 0), (200, 0)]),
    src("SIDE", [(100, 0), (100, 100)]),
]


class TestTJunction:
    def test_disconnected_before_splitting(self):
        # Without splitting, endpoint-only noding leaves two components.
        links = [
            type("L", (), {"coords": s.coords})() for s in T_JUNCTION
        ]
        pairs, coords = assign_nodes(links)  # type: ignore[arg-type]
        assert len(coords) == 4  # no shared node

    def test_splits_the_through_road(self):
        res = split_at_junctions(T_JUNCTION)
        assert res.parents_split == 1
        assert res.cuts_made == 1
        assert len(res.links) == 3  # two halves of THROUGH, plus SIDE

    def test_produces_one_connected_component(self):
        res = split_at_junctions(T_JUNCTION)
        assert components(res.links) == 1

    def test_preserves_total_length_exactly(self):
        res = split_at_junctions(T_JUNCTION)
        through = [l for l in res.links if l.closure_group_id == "THROUGH"]
        assert sum(l.length_m for l in through) == pytest.approx(200.0, abs=1e-9)
        assert sorted(l.length_m for l in through) == pytest.approx([100.0, 100.0])

    def test_keeps_both_halves_in_one_closure_group(self):
        res = split_at_junctions(T_JUNCTION)
        through = [l for l in res.links if l.closure_group_id == "THROUGH"]
        assert len(through) == 2
        assert sorted(l.amds_id for l in through) == ["THROUGH#0", "THROUGH#1"]

    def test_reports_no_near_misses_for_an_exact_junction(self):
        assert split_at_junctions(T_JUNCTION).near_misses == []


class TestGradeSeparation:
    """Two roads crossing with neither ending: an overbridge."""

    X = [
        src("OVER", [(0, 50), (100, 50)]),
        src("UNDER", [(50, 0), (50, 100)]),
    ]

    def test_makes_no_cut(self):
        res = split_at_junctions(self.X)
        assert res.cuts_made == 0
        assert res.parents_split == 0
        assert len(res.links) == 2

    def test_leaves_them_unconnected(self):
        res = split_at_junctions(self.X)
        assert components(res.links) == 2
        _, coords = assign_nodes(res.links)
        assert len(coords) == 4  # a crossing node would give five


class TestInterchange:
    def test_ramp_cuts_the_motorway_but_an_overbridge_does_not(self):
        res = split_at_junctions([
            src("MOTORWAY", [(0, 0), (1000, 0)]),
            src("RAMP", [(400, 0), (500, 120)]),
            src("OVERBRIDGE", [(800, -60), (800, 60)]),
        ])
        # One cut, from the ramp only. The overbridge crosses mid-span with both
        # its endpoints clear of the motorway, so it must not cut anything.
        assert res.cuts_made == 1
        assert len(res.links) == 4
        # Motorway + ramp are one component; the overbridge stays separate.
        assert components(res.links) == 2


class TestNearMisses:
    GAP = [
        src("THROUGH", [(0, 0), (200, 0)]),
        src("SIDE", [(100, 1), (100, 100)]),
    ]

    def test_does_not_join_an_endpoint_one_metre_away(self):
        res = split_at_junctions(self.GAP)
        assert res.cuts_made == 0
        assert components(res.links) == 2
        assert len(res.near_misses) > 0
        assert res.near_misses[0].distance_m == pytest.approx(1.0, abs=1e-6)

    def test_joins_it_when_the_tolerance_is_widened_deliberately(self):
        res = split_at_junctions(self.GAP, split_tolerance_m=1.5)
        assert res.cuts_made == 1
        assert components(res.links) == 1


class TestMultipleJunctions:
    def test_cuts_once_per_distinct_junction(self):
        res = split_at_junctions([
            src("MAIN", [(0, 0), (300, 0)]),
            src("S1", [(100, 0), (100, 50)]),
            src("S2", [(200, 0), (200, 50)]),
        ])
        assert res.cuts_made == 2
        pieces = [l for l in res.links if l.closure_group_id == "MAIN"]
        assert len(pieces) == 3
        assert [p.length_m for p in pieces] == pytest.approx([100.0, 100.0, 100.0])

    def test_does_not_cut_twice_at_the_same_point(self):
        res = split_at_junctions([
            src("MAIN", [(0, 0), (300, 0)]),
            src("S1", [(150, 0), (150, 50)]),
            src("S2", [(150, 0), (150, -50)]),
        ])
        assert res.cuts_made == 1
        assert len([l for l in res.links if l.closure_group_id == "MAIN"]) == 2


class TestJunctionAtExistingVertex:
    def test_splits_at_an_interior_vertex_without_duplicating_it(self):
        res = split_at_junctions([
            src("MAIN", [(0, 0), (100, 0), (200, 0)]),
            src("SIDE", [(100, 0), (100, 80)]),
        ])
        assert res.cuts_made == 1
        pieces = [l for l in res.links if l.closure_group_id == "MAIN"]
        assert [p.length_m for p in pieces] == pytest.approx([100.0, 100.0])
        assert components(res.links) == 1

    def test_no_cut_when_roads_meet_end_to_end(self):
        res = split_at_junctions([
            src("A", [(0, 0), (100, 0)]),
            src("B", [(100, 0), (200, 0)]),
        ])
        assert res.cuts_made == 0
        assert components(res.links) == 1


class TestNodeAssignment:
    def test_shares_a_node_between_links_meeting_end_to_end(self):
        res = split_at_junctions([
            src("A", [(0, 0), (100, 0)]),
            src("B", [(100, 0), (200, 0)]),
        ])
        pairs, coords = assign_nodes(res.links)
        assert len(coords) == 3
        assert pairs[0][1] == pairs[1][0]

    def test_absorbs_sub_millimetre_float_noise(self):
        res = split_at_junctions([
            src("A", [(0, 0), (100, 0)]),
            src("B", [(100.000001, 0), (200, 0)]),
        ])
        _, coords = assign_nodes(res.links, tolerance_m=0.01)
        assert len(coords) == 3


class TestGridBoundarySnapping:
    """Regression: two endpoints either side of a quantisation cell boundary.

    Found by cross-validating against the TypeScript engine. Two Wellington
    links met end-to-end 0.4 mm apart but were assigned different nodes,
    severing the junction and sending a detour 3.6 km the wrong way. Snapping
    to a single grid cell splits any pair that straddles a boundary, however
    close they are.
    """

    def test_endpoints_astride_a_cell_boundary_still_share_a_node(self):
        # 0.2 mm apart, deliberately placed either side of a 0.01 m cell edge.
        a = 1000.0099
        b = 1000.0101
        assert abs(b - a) < 0.001
        assert int(a // 0.01) != int(b // 0.01)  # different cells
        res = split_at_junctions([
            src("A", [(0, 0), (a, 0)]),
            src("B", [(b, 0), (2000, 0)]),
        ])
        pairs, coords = assign_nodes(res.links, tolerance_m=0.01)
        assert pairs[0][1] == pairs[1][0], "endpoints 0.4 mm apart must share a node"
        assert components(res.links) == 1

    def test_still_refuses_to_merge_endpoints_beyond_the_tolerance(self):
        res = split_at_junctions([
            src("A", [(0, 0), (100, 0)]),
            src("B", [(100.5, 0), (200, 0)]),
        ])
        pairs, coords = assign_nodes(res.links, tolerance_m=0.01)
        assert pairs[0][1] != pairs[1][0]
        assert components(res.links) == 2

    def test_picks_the_nearest_candidate_when_several_are_in_range(self):
        res = split_at_junctions([
            src("A", [(0, 0), (100, 0)]),
            src("B", [(100.004, 0), (200, 0)]),
            src("C", [(100.008, 0), (300, 0)]),
        ])
        pairs, coords = assign_nodes(res.links, tolerance_m=0.01)
        # All three within 10 mm of each other: one shared node, not three.
        assert pairs[0][1] == pairs[1][0] == pairs[2][0]
