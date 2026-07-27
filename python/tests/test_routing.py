"""Known-answer tests for the pgRouting-backed detour engine.

Every expected number is derivable on paper from the fixture geometry. These
run against a real PostGIS database with real pgr_dijkstra calls, so they check
the deployed path rather than a stand-in.
"""

from __future__ import annotations

import pytest

from nzcl.detour import compute
from nzcl.routing import route

from conftest import requires_db

pytestmark = requires_db


SQUARE = [
    {"id": "S", "pts": [(0, 0), (100, 0)]},
    {"id": "E", "pts": [(100, 0), (100, 100)]},
    {"id": "N", "pts": [(100, 100), (0, 100)]},
    {"id": "W", "pts": [(0, 100), (0, 0)]},
]


def arcs_of_group(snapshot_id: str, link_id: int) -> list[int]:
    from nzcl import db
    group = db.query_one(
        "SELECT closure_group_id FROM links WHERE snapshot_id=%s AND link_id=%s",
        (snapshot_id, link_id))["closure_group_id"]
    return [r["arc_id"] for r in db.query(
        "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND closure_group_id=%s",
        (snapshot_id, group))]


class TestIsolatedEdge:
    def test_reports_disconnected_not_an_error_and_not_a_zero(self, synthetic):
        net = synthetic([{"id": "A", "pts": [(0, 0), (100, 0)]}])
        r = compute(net.snapshot_id, net.link_id("A"))
        assert r.forward.status == "DISCONNECTED"
        assert r.reverse.status == "DISCONNECTED"
        assert r.forward.alternative_distance_m is None
        assert r.forward.detour_ratio_vs_link is None


class TestSquareLoop:
    def test_detours_the_long_way_for_exactly_300_m(self, synthetic):
        net = synthetic(SQUARE)
        f = compute(net.snapshot_id, net.link_id("S")).forward
        assert f.status == "OK"
        assert f.selected_link_length_m == pytest.approx(100, abs=1e-6)
        assert f.alternative_distance_m == pytest.approx(300, abs=1e-6)
        assert f.added_distance_vs_link_m == pytest.approx(200, abs=1e-6)
        assert f.detour_ratio_vs_link == pytest.approx(3.0, abs=1e-9)
        assert f.normal_path_distance_m == pytest.approx(100, abs=1e-6)
        assert f.network_penalty_m == pytest.approx(200, abs=1e-6)

    def test_is_symmetric_on_a_two_way_loop(self, synthetic):
        net = synthetic(SQUARE)
        r = compute(net.snapshot_id, net.link_id("S"))
        assert r.reverse.alternative_distance_m == pytest.approx(300, abs=1e-6)


class TestTriangle:
    def test_replaces_the_500_m_hypotenuse_with_400_plus_300(self, synthetic):
        net = synthetic([
            {"id": "AB", "pts": [(0, 0), (400, 0)]},
            {"id": "AC", "pts": [(0, 0), (0, 300)]},
            {"id": "BC", "pts": [(400, 0), (0, 300)]},
        ])
        f = compute(net.snapshot_id, net.link_id("BC")).forward
        assert f.selected_link_length_m == pytest.approx(500, abs=1e-6)
        assert f.alternative_distance_m == pytest.approx(700, abs=1e-6)
        assert f.detour_ratio_vs_link == pytest.approx(1.4, abs=1e-9)


class TestDirectional:
    NET = [
        {"id": "X", "pts": [(0, 0), (100, 0)]},
        {"id": "BC", "pts": [(100, 0), (100, 100)], "oneway": True},
        {"id": "CD", "pts": [(100, 100), (0, 100)], "oneway": True},
        {"id": "DA", "pts": [(0, 100), (0, 0)], "oneway": True},
    ]

    def test_forward_and_reverse_differ(self, synthetic):
        net = synthetic(self.NET)
        r = compute(net.snapshot_id, net.link_id("X"))
        assert r.forward.status == "DISCONNECTED"
        assert r.reverse.status == "OK"
        assert r.reverse.alternative_distance_m == pytest.approx(300, abs=1e-6)

    def test_one_way_link_generates_a_single_arc(self, synthetic):
        from nzcl import db
        net = synthetic(self.NET)
        n = db.query_one(
            "SELECT count(*) AS n FROM arcs WHERE snapshot_id=%s AND link_id=%s",
            (net.snapshot_id, net.link_id("BC")))["n"]
        assert n == 1

    def test_two_way_link_generates_two_arcs(self, synthetic):
        from nzcl import db
        net = synthetic(self.NET)
        n = db.query_one(
            "SELECT count(*) AS n FROM arcs WHERE snapshot_id=%s AND link_id=%s",
            (net.snapshot_id, net.link_id("X")))["n"]
        assert n == 2


class TestGradeSeparation:
    def test_crossing_polylines_do_not_connect(self, synthetic):
        net = synthetic([
            {"id": "OVER", "pts": [(0, 50), (100, 50)]},
            {"id": "UNDER", "pts": [(50, 0), (50, 100)]},
        ])
        # Four endpoints, four nodes. A crossing node would give five.
        assert len(net.node_coords) == 4
        u, _ = net.nodes_of("OVER")
        _, v = net.nodes_of("UNDER")
        assert route(net.snapshot_id, u, v).status == "DISCONNECTED"


class TestProhibitedTurn:
    NET = [
        {"id": "AB", "pts": [(0, 0), (100, 0)]},
        {"id": "BC", "pts": [(100, 0), (200, 0)]},
        {"id": "BD", "pts": [(100, 0), (100, 100)]},
        {"id": "DC", "pts": [(100, 100), (200, 0)]},
    ]
    BAN = [{"seq": ["AB", "BC"], "vehicle": True, "heavy": True, "emergency": False}]

    def test_routes_straight_through_with_no_restriction(self, synthetic):
        net = synthetic(self.NET)
        u, _ = net.nodes_of("AB")
        _, v = net.nodes_of("BC")
        assert route(net.snapshot_id, u, v).distance_m == pytest.approx(200, abs=1e-6)

    def test_forces_the_longer_legal_path_when_banned_for_cars(self, synthetic):
        net = synthetic(self.NET, self.BAN)
        u, _ = net.nodes_of("AB")
        _, v = net.nodes_of("BC")
        res = route(net.snapshot_id, u, v, profile="car")
        assert res.status == "OK"
        assert res.used_expanded_graph is True
        assert res.distance_m == pytest.approx(200 + (100**2 + 100**2) ** 0.5, abs=1e-6)

    def test_lets_an_exempt_profile_take_the_banned_turn(self, synthetic):
        net = synthetic(self.NET, self.BAN)
        u, _ = net.nodes_of("AB")
        _, v = net.nodes_of("BC")
        res = route(net.snapshot_id, u, v, profile="emergency")
        assert res.distance_m == pytest.approx(200, abs=1e-6)
        assert res.used_expanded_graph is False


class TestModeRestriction:
    NET = [
        {"id": "PRIVATE", "pts": [(0, 0), (100, 0)], "mode_vehicle": False,
         "mode_emergency": True},
        {"id": "L1", "pts": [(0, 0), (0, 100)]},
        {"id": "L2", "pts": [(0, 100), (100, 100)]},
        {"id": "L3", "pts": [(100, 100), (100, 0)]},
    ]

    def test_car_takes_the_long_way(self, synthetic):
        net = synthetic(self.NET)
        u, v = net.nodes_of("PRIVATE")
        assert route(net.snapshot_id, u, v, profile="car").distance_m == \
            pytest.approx(300, abs=1e-6)

    def test_emergency_may_use_it(self, synthetic):
        net = synthetic(self.NET)
        u, v = net.nodes_of("PRIVATE")
        assert route(net.snapshot_id, u, v, profile="emergency").distance_m == \
            pytest.approx(100, abs=1e-6)

    def test_heavy_profile_excluded_separately(self, synthetic):
        net = synthetic([
            {"id": "LIGHT_ONLY", "pts": [(0, 0), (100, 0)], "mode_vehicle_heavy": False},
            {"id": "L1", "pts": [(0, 0), (0, 100)]},
            {"id": "L2", "pts": [(0, 100), (100, 100)]},
            {"id": "L3", "pts": [(100, 100), (100, 0)]},
        ])
        u, v = net.nodes_of("LIGHT_ONLY")
        assert route(net.snapshot_id, u, v, profile="car").distance_m == \
            pytest.approx(100, abs=1e-6)
        assert route(net.snapshot_id, u, v, profile="heavy").distance_m == \
            pytest.approx(300, abs=1e-6)


class TestCulDeSacAndIslands:
    def test_terminal_link_has_no_replacement(self, synthetic):
        net = synthetic([
            {"id": "MAIN", "pts": [(0, 0), (100, 0)]},
            {"id": "SPUR", "pts": [(100, 0), (180, 0)]},
        ])
        f = compute(net.snapshot_id, net.link_id("SPUR")).forward
        assert f.status == "DISCONNECTED"
        assert f.isolation is not None

    def test_separate_island_is_unreachable(self, synthetic):
        net = synthetic([
            {"id": "MAINLAND", "pts": [(0, 0), (100, 0)]},
            {"id": "ISLAND", "pts": [(9000, 9000), (9100, 9000)]},
        ])
        u, _ = net.nodes_of("MAINLAND")
        _, v = net.nodes_of("ISLAND")
        res = route(net.snapshot_id, u, v)
        assert res.status == "DISCONNECTED"
        assert "component" in (res.detail or "")


class TestTimeout:
    def test_a_timeout_is_never_reported_as_disconnected(self, synthetic):
        """The distinction that matters most: an unanswered query must not be
        reported as a finding about the network.

        A synthetic four-link graph completes faster than any timeout worth
        setting, so this runs against a real ingested snapshot where a 1 ms
        budget genuinely cannot load the edge set."""
        from nzcl import db
        real = db.query_one(
            "SELECT snapshot_id FROM network_snapshots "
            "WHERE snapshot_id NOT LIKE 'test-%' AND routable_link_count > 1000 "
            "ORDER BY routable_link_count DESC LIMIT 1"
        )
        if not real:
            pytest.skip("no ingested snapshot available to time out against")
        snap = real["snapshot_id"]
        link = db.query_one(
            "SELECT source_node, target_node FROM links WHERE snapshot_id=%s "
            "AND in_analysis_area AND source_node <> target_node LIMIT 1", (snap,))
        res = route(snap, link["source_node"], link["target_node"],
                    statement_timeout_ms=1)
        assert res.status == "UNRESOLVED_TIMEOUT"
        assert res.status != "DISCONNECTED"
        assert res.distance_m is None
        assert "timeout" in (res.detail or "").lower()

    def test_finds_the_answer_with_an_adequate_budget(self, synthetic):
        net = synthetic(SQUARE)
        assert compute(net.snapshot_id, net.link_id("S")).forward.status == "OK"


class TestClosureScope:
    NET = [
        {"id": "L1", "pts": [(0, 0), (100, 0)]},
        {"id": "L2", "pts": [(100, 0), (200, 0)]},
        {"id": "BY1", "pts": [(0, 0), (0, -100)]},
        {"id": "BY2", "pts": [(0, -100), (200, -100)]},
        {"id": "BY3", "pts": [(200, -100), (200, 0)]},
    ]

    def test_physical_removes_both_arcs_of_the_link(self, synthetic):
        net = synthetic(self.NET)
        r = compute(net.snapshot_id, net.link_id("L1"), closure_scope="physical")
        assert len(r.forward.removed_arc_ids) == 2

    def test_directed_removes_one_arc(self, synthetic):
        net = synthetic(self.NET)
        r = compute(net.snapshot_id, net.link_id("L1"), closure_scope="directed")
        assert len(r.forward.removed_arc_ids) == 1

    def test_directed_closure_leaves_a_route_the_physical_one_does_not(self, synthetic):
        net = synthetic(self.NET)
        directed = compute(net.snapshot_id, net.link_id("L1"),
                           closure_scope="directed").forward
        assert directed.status == "OK"
        # 100 down + 200 across + 100 up + 100 back along L2 = 500
        assert directed.alternative_distance_m == pytest.approx(500, abs=1e-6)


class TestMetricSemantics:
    def test_network_penalty_differs_from_added_distance(self, synthetic):
        # A 1,000 m link between two nodes that also have a 100 m shortcut.
        net = synthetic([
            {"id": "LONG", "pts": [(0, 0), (0, 450), (100, 450), (100, 0)]},
            {"id": "SHORT", "pts": [(0, 0), (100, 0)]},
        ])
        f = compute(net.snapshot_id, net.link_id("LONG")).forward
        assert f.selected_link_length_m == pytest.approx(1000, abs=1e-6)
        assert f.normal_path_distance_m == pytest.approx(100, abs=1e-6)
        assert f.alternative_distance_m == pytest.approx(100, abs=1e-6)
        # Negative: the link was itself a detour between its own endpoints.
        assert f.added_distance_vs_link_m == pytest.approx(-900, abs=1e-6)
        # Closing it costs the network nothing.
        assert f.network_penalty_m == pytest.approx(0, abs=1e-6)

    def test_time_routing_can_prefer_a_longer_faster_path(self, synthetic):
        net = synthetic([
            {"id": "SLOW", "pts": [(0, 0), (1000, 0)], "speed_kph": 10},
            {"id": "FASTA", "pts": [(0, 0), (0, 300)], "speed_kph": 100},
            {"id": "FASTB", "pts": [(0, 300), (1000, 300)], "speed_kph": 100},
            {"id": "FASTC", "pts": [(1000, 300), (1000, 0)], "speed_kph": 100},
        ])
        u, v = net.nodes_of("SLOW")
        by_dist = route(net.snapshot_id, u, v, metric="distance")
        by_time = route(net.snapshot_id, u, v, metric="time")
        assert by_dist.distance_m == pytest.approx(1000, abs=1e-6)
        assert by_time.distance_m == pytest.approx(1600, abs=1e-6)
        assert by_time.time_s < by_dist.time_s


class TestCorridor:
    """A one-way carriageway whose downstream endpoint is internal to the
    one-way system: the endpoint measure has no answer, the corridor does."""

    ONE_WAY_PAIR = [
        {"id": "NB1", "pts": [(0, 0), (500, 0)], "oneway": True},
        {"id": "NB2", "pts": [(500, 0), (1000, 0)], "oneway": True},
        {"id": "SB", "pts": [(1000, 20), (0, 20)], "oneway": True},
        {"id": "JOIN_N", "pts": [(1000, 0), (1000, 20)]},
        {"id": "JOIN_S", "pts": [(0, 20), (0, 0)]},
        {"id": "L1", "pts": [(0, 0), (0, -300)]},
        {"id": "L2", "pts": [(0, -300), (1000, -300)]},
        {"id": "L3", "pts": [(1000, -300), (1000, 0)]},
    ]

    def test_endpoint_measure_is_undefined(self, synthetic):
        net = synthetic(self.ONE_WAY_PAIR)
        f = compute(net.snapshot_id, net.link_id("NB1")).forward
        assert f.status == "DISCONNECTED"
        assert f.alternative_distance_m is None

    def test_corridor_resolves_so_it_is_not_mislabelled_as_isolating(self, synthetic):
        net = synthetic(self.ONE_WAY_PAIR)
        f = compute(net.snapshot_id, net.link_id("NB1")).forward
        assert f.corridor is not None
        assert f.corridor.status == "OK"
        assert "ENDPOINT_MEASURE_UNDEFINED_CORRIDOR_USED" in f.quality_flags
        assert f.corridor.penalty_m == pytest.approx(600, abs=1e-6)

    def test_a_genuine_dead_end_is_still_flagged(self, synthetic):
        net = synthetic([
            {"id": "MAIN", "pts": [(0, 0), (100, 0)]},
            {"id": "SPUR", "pts": [(100, 0), (200, 0)]},
        ])
        f = compute(net.snapshot_id, net.link_id("SPUR")).forward
        assert f.status == "DISCONNECTED"
        assert "SOLE_ACCESS" in f.quality_flags


class TestRouteIntegrity:
    def test_route_never_uses_an_arc_from_its_own_closure(self, synthetic):
        net = synthetic(SQUARE)
        f = compute(net.snapshot_id, net.link_id("S")).forward
        assert set(f.route_arc_ids).isdisjoint(set(f.removed_arc_ids))

    def test_route_arc_lengths_sum_to_the_reported_distance(self, synthetic):
        from nzcl import db
        net = synthetic(SQUARE)
        f = compute(net.snapshot_id, net.link_id("S")).forward
        total = db.query_one(
            "SELECT sum(cost_distance_m) AS d FROM arcs WHERE snapshot_id=%s "
            "AND arc_id = ANY(%s)", (net.snapshot_id, f.route_arc_ids))["d"]
        assert total == pytest.approx(f.alternative_distance_m, abs=1e-6)
