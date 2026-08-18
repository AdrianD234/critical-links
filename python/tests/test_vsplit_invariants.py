"""Invariants the corridor work is about to build on.

These are the properties that, if they broke, would produce a plausible number
rather than an error - so each one is asserted directly rather than inferred
from a result looking about right.

The one worth reading first is `TestTheAnchorDoesNotShortCircuitTheSearch`.
Anchoring a virtual node to its host link's real component fixed a false
DISCONNECTED, and a repair of that shape can just as easily manufacture a false
CONNECTED. The test pins both halves: the anchor decides only whether the
search is worth attempting, and Dijkstra alone decides the answer.
"""

from __future__ import annotations

import pytest

from nzcl import db, routing, vsplit
from nzcl.vsplit import LinkInterval

from conftest import requires_db

pytestmark = requires_db


#: One road and nothing else. Closing its middle severs it, genuinely.
LONE_ROAD = [
    {"id": "ONLY", "pts": [(0, 0), (400, 0)], "road_name": "Only Road"},
]

#: A road with a right-angle bend, and a long way round.
#:
#:     BEND   (0,0) - (100,0) - (100,100)                      200 m
#:     ROUND  (0,0) - (-100,0) - (-100,200) - (100,200) - (100,100)  600 m
CURVED = [
    {"id": "BEND", "pts": [(0, 0), (100, 0), (100, 100)],
     "road_name": "Bend Road"},
    {"id": "ROUND", "pts": [(0, 0), (-100, 0), (-100, 200), (100, 200),
                            (100, 100)], "road_name": "Round Road"},
]


def length_of(net, link_id):
    return float(db.query_one(
        "SELECT length_m FROM links WHERE snapshot_id=%s AND link_id=%s",
        (net.snapshot_id, link_id))["length_m"])


def build(net, amds, a, b, *, mode="both", traversal="forward"):
    link = net.link_id(amds)
    lo, hi = (a, b) if a < b else (b, a)
    return vsplit.build(
        net.snapshot_id,
        [LinkInterval(link, lo, hi, traversal, length_of(net, link))],
        handle_a=(link, a), handle_b=(link, b), direction_mode=mode)


def drive(net, s, u=None, v=None, metric="distance"):
    return routing.route(
        net.snapshot_id,
        s.node_at_a if u is None else u,
        s.node_at_b if v is None else v,
        metric=metric, excluded_arcs=s.excluded_arc_ids, overlay=s.overlay)


class TestTheAnchorDoesNotShortCircuitTheSearch:
    """The component anchor permits an attempt; it never decides the outcome."""

    def test_the_anchor_says_connected_where_the_road_is_severed(self, synthetic):
        """On a lone road, closing the middle really does sever it.

        The anchor still reports one component - correctly, because that is a
        statement about the network BEFORE the closure, which is all it is for.
        If the anchor were being read as the answer, this would wrongly come
        back OK."""
        net = synthetic(LONE_ROAD)
        s = build(net, "ONLY", 0.25, 0.5)

        permitted = routing._same_component(
            net.snapshot_id, s.node_at_a, s.node_at_b, s.overlay)
        assert permitted is True

        assert drive(net, s).status == "DISCONNECTED"

    def test_the_same_nodes_route_when_a_direction_stays_open(self, synthetic):
        """Proof the DISCONNECTED above is the closure and not the anchor
        failing: the identical virtual nodes route fine the other way."""
        net = synthetic(LONE_ROAD)
        s = build(net, "ONLY", 0.25, 0.5, mode="a_to_b")

        assert drive(net, s).status == "DISCONNECTED"
        back = drive(net, s, u=s.node_at_b, v=s.node_at_a)
        assert back.status == "OK"
        assert back.distance_m == pytest.approx(100.0, abs=1e-6)

    def test_a_disconnected_result_reports_the_search_not_the_anchor(
            self, synthetic):
        net = synthetic(LONE_ROAD)
        s = build(net, "ONLY", 0.25, 0.5)

        r = drive(net, s)
        assert "search space exhausted" in (r.detail or "")


class TestCostProration:
    """A piece is charged its parent's cost, scaled by the geometry it covers."""

    def test_distance_and_time_are_both_prorated(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)
        parents = {int(r["arc_id"]): r for r in db.query(
            "SELECT arc_id, cost_distance_m, cost_time_s FROM arcs "
            "WHERE snapshot_id=%s AND link_id=%s",
            (net.snapshot_id, net.link_id("BEND")))}

        assert s.overlay.arcs
        for piece in s.overlay.arcs:
            parent = parents[piece.parent_arc_id]
            share = piece.cost_distance_m / float(parent["cost_distance_m"])
            assert piece.cost_time_s == pytest.approx(
                share * float(parent["cost_time_s"]), rel=1e-12)

    def test_the_pieces_of_one_arc_tile_its_whole_cost(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)
        parent_id = min(a.parent_arc_id for a in s.overlay.arcs)
        parent = db.query_one(
            "SELECT cost_distance_m FROM arcs WHERE snapshot_id=%s AND arc_id=%s",
            (net.snapshot_id, parent_id))

        open_share = sum(a.cost_distance_m for a in s.overlay.arcs
                         if a.parent_arc_id == parent_id)
        assert open_share + s.closed_length_m == pytest.approx(
            float(parent["cost_distance_m"]), abs=1e-6)

    def test_no_piece_is_zero_length(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)

        assert all(a.cost_distance_m > 0.0 for a in s.overlay.arcs)


class TestGeometryOrientation:
    """A piece must run the way its parent arc runs."""

    def test_forward_pieces_ascend_and_reverse_pieces_descend(self, synthetic):
        net = synthetic(CURVED)
        link = net.link_id("BEND")
        s = build(net, "BEND", 0.25, 0.75)
        ends = db.query_one(
            "SELECT source_node, target_node FROM links WHERE snapshot_id=%s "
            "AND link_id=%s", (net.snapshot_id, link))
        start, end = int(ends["source_node"]), int(ends["target_node"])

        forward = [a for a in s.overlay.arcs if _direction(net, a) == "forward"]
        reverse = [a for a in s.overlay.arcs if _direction(net, a) == "reverse"]

        # The piece before the outage runs start -> A going forward, and
        # A -> start going back.
        assert any(a.source == start and a.target == s.node_at_a
                   for a in forward)
        assert any(a.source == s.node_at_a and a.target == start
                   for a in reverse)
        # The piece after it runs B -> end forward, end -> B reverse.
        assert any(a.source == s.node_at_b and a.target == end
                   for a in forward)
        assert any(a.source == end and a.target == s.node_at_b
                   for a in reverse)


class TestACurvedSpan:
    """Fractions are fractions of length, so a bend changes nothing."""

    def test_the_closure_spans_the_corner(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)

        assert s.closed_length_m == pytest.approx(100.0, abs=1e-6)

    def test_the_detour_goes_the_long_way_round(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)

        r = drive(net, s)
        # 50 m back to the start, 600 m round, 50 m in from the far end.
        assert r.status == "OK"
        assert r.distance_m == pytest.approx(700.0, abs=1e-6)


class TestTheRedPreviewMatchesTheClosure:
    """The stop condition: a partial outage must not close more road than it
    draws, nor draw more than it closes."""

    @pytest.mark.parametrize("a,b", [(0.25, 0.75), (0.0, 0.4), (0.6, 1.0),
                                     (0.1, 0.9)])
    def test_drawn_length_equals_closed_length_to_a_millimetre(
            self, synthetic, a, b):
        net = synthetic(CURVED)
        s = build(net, "BEND", a, b)

        geom = vsplit.span_geometry(net.snapshot_id, s.intervals)

        assert geom["measuredLengthM"] == pytest.approx(
            s.closed_length_m, abs=0.001)
        assert geom["arithmeticLengthM"] == pytest.approx(
            geom["measuredLengthM"], abs=0.001)

    def test_the_drawn_geometry_is_a_real_feature_collection(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)

        geom = vsplit.span_geometry(net.snapshot_id, s.intervals)

        assert geom["type"] == "FeatureCollection"
        assert len(geom["features"]) == 1
        line = geom["features"][0]["geometry"]
        assert line["type"] == "LineString"
        # It crosses the bend, so it cannot be a straight two-point line.
        assert len(line["coordinates"]) >= 3


class TestReversedHandles:
    """Which handle was placed first changes direction, never geometry."""

    def test_the_closed_stretch_is_the_same_either_way(self, synthetic):
        net = synthetic(CURVED)
        forward = build(net, "BEND", 0.25, 0.75)
        reversed_ = build(net, "BEND", 0.75, 0.25)

        assert reversed_.closed_length_m == pytest.approx(
            forward.closed_length_m, abs=1e-9)
        assert (vsplit.span_geometry(net.snapshot_id, reversed_.intervals)
                ["measuredLengthM"]
                == pytest.approx(
                    vsplit.span_geometry(net.snapshot_id, forward.intervals)
                    ["measuredLengthM"], abs=0.001))

    def test_both_directions_shut_measures_the_same_either_way(self, synthetic):
        net = synthetic(CURVED)
        forward = build(net, "BEND", 0.25, 0.75)
        reversed_ = build(net, "BEND", 0.75, 0.25)

        assert drive(net, forward).distance_m == pytest.approx(
            drive(net, reversed_).distance_m, abs=1e-6)

    def test_the_handles_swap_ends(self, synthetic):
        net = synthetic(CURVED)
        forward = build(net, "BEND", 0.25, 0.75)
        reversed_ = build(net, "BEND", 0.75, 0.25)

        # Same two places, opposite roles.
        assert forward.node_at_a == reversed_.node_at_b
        assert forward.node_at_b == reversed_.node_at_a


class TestRouteGeometryOverVirtualArcsIsComplete:
    """Every arc in a returned path must be accountable, or the total is a lie."""

    def test_every_arc_resolves_and_the_lengths_add_up(self, synthetic):
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)

        r = drive(net, s)
        pieces = s.overlay.by_id()
        total = 0.0
        for arc_id in r.arc_ids:
            if arc_id in pieces:
                total += pieces[arc_id].cost_distance_m
            else:
                row = db.query_one(
                    "SELECT cost_distance_m FROM arcs WHERE snapshot_id=%s "
                    "AND arc_id=%s", (net.snapshot_id, arc_id))
                assert row is not None, f"arc {arc_id} resolves to nothing"
                total += float(row["cost_distance_m"])

        assert total == pytest.approx(r.distance_m, abs=1e-6)

    def test_an_unaccounted_arc_raises_rather_than_shortening_the_total(
            self, synthetic):
        """The regression that made a 1100 m path measure 1000 m silently."""
        net = synthetic(CURVED)
        s = build(net, "BEND", 0.25, 0.75)
        r = drive(net, s)
        stray = min(a.arc_id for a in s.overlay.arcs) - 1

        with pytest.raises(routing.UnknownArc):
            routing._summarise(net.snapshot_id, r.arc_ids + [stray], s.overlay)


class TestHandlesAtOrBeyondTheEnds:

    def test_a_handle_a_hair_from_the_end_is_treated_as_at_the_end(
            self, synthetic):
        """Otherwise the split would mint a piece a few micrometres long and
        ask the network to route through it."""
        net = synthetic(CURVED)
        link = net.link_id("BEND")
        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 1e-12, 0.5, "forward", length_of(net, link))],
            handle_a=(link, 1e-12), handle_b=(link, 0.5))
        start = int(db.query_one(
            "SELECT source_node FROM links WHERE snapshot_id=%s AND link_id=%s",
            (net.snapshot_id, link))["source_node"])

        assert s.node_at_a == start
        assert all(a.cost_distance_m > 0.0 for a in s.overlay.arcs)

    def test_two_handles_at_one_measure_are_refused(self, synthetic):
        net = synthetic(CURVED)
        link = net.link_id("BEND")
        with pytest.raises(ValueError, match="same position"):
            vsplit.build(
                net.snapshot_id,
                [LinkInterval(link, 0.5, 0.5, "forward", length_of(net, link))],
                handle_a=(link, 0.5), handle_b=(link, 0.5))

    def test_an_interval_closing_nothing_is_refused(self, synthetic):
        net = synthetic(CURVED)
        link = net.link_id("BEND")
        with pytest.raises(ValueError, match="closes nothing"):
            vsplit.build(
                net.snapshot_id,
                [LinkInterval(link, 0.5, 0.5, "forward", length_of(net, link))],
                handle_a=(link, 0.2), handle_b=(link, 0.8))


#: A detour network with an unrelated banned turn off to one side.
#:
#:     MAIN   (0,0) - (400,0)
#:     BYPASS (0,0) - (0,-200) - (400,-200) - (400,0)
#:     SPUR1  (400,0) - (500,0)      a banned right into
#:     SPUR2  (500,0) - (500,100)
RESTRICTED = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)]},
    {"id": "BYPASS", "pts": [(0, 0), (0, -200), (400, -200), (400, 0)]},
    {"id": "SPUR1", "pts": [(400, 0), (500, 0)]},
    {"id": "SPUR2", "pts": [(500, 0), (500, 100)]},
]


class TestTurnRestrictionsAreScopedToTheRouteTaken:
    """Fail-closed must mean "this route is banned", not "restrictions exist".

    `TURN_RESTRICTION_UNSUPPORTED` is a temporary result: while a link is split
    the expanded graph cannot reproduce the pieces, so a route that crosses a
    banned manoeuvre is refused rather than offered as legal. That refusal has
    to be triggered by an ACTUAL violation on the path taken. If the mere
    presence of a published restriction anywhere in the network were enough,
    every outage span in the country would come back unresolved - AMDS
    publishes 60 of them nationally.
    """

    def test_an_unrelated_restriction_does_not_affect_the_span(self, synthetic):
        net = synthetic(RESTRICTED, [{"seq": ["SPUR1", "SPUR2"]}])
        link = net.link_id("MAIN")
        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.25, 0.5, "forward", length_of(net, link))],
            handle_a=(link, 0.25), handle_b=(link, 0.5))

        r = drive(net, s)

        assert r.status == "OK"
        assert r.distance_m == pytest.approx(1100.0, abs=1e-6)

    def test_a_restriction_on_the_route_taken_fails_closed(self, synthetic):
        """The other half: it must not be silently ignored either. The detour
        runs MAIN -> BYPASS, and that manoeuvre is banned here."""
        net = synthetic(RESTRICTED, [{"seq": ["MAIN", "BYPASS"]}])
        link = net.link_id("MAIN")
        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.25, 0.5, "forward", length_of(net, link))],
            handle_a=(link, 0.25), handle_b=(link, 0.5))

        r = drive(net, s)

        assert r.status == "TURN_RESTRICTION_UNSUPPORTED"
        assert r.distance_m is None
        assert "banned manoeuvre" in (r.detail or "")

    def test_a_split_piece_answers_to_its_parent_link(self, synthetic):
        """The mechanism the test above depends on: restrictions are sequences
        of real link ids, so a piece must report its parent or splitting a link
        would quietly unban every manoeuvre across it."""
        net = synthetic(RESTRICTED, [{"seq": ["MAIN", "BYPASS"]}])
        link = net.link_id("MAIN")
        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.25, 0.5, "forward", length_of(net, link))],
            handle_a=(link, 0.25), handle_b=(link, 0.5))

        assert all(a.link_id == link for a in s.overlay.arcs)

        plain = routing._run_dijkstra(
            net.snapshot_id, s.node_at_a, s.node_at_b, "distance", "car",
            s.excluded_arc_ids, 20_000, s.overlay)
        _, _, link_path = routing._summarise(
            net.snapshot_id, plain.arc_ids, s.overlay)
        assert link_path[0] == link


def _direction(net, piece):
    return db.query_one(
        "SELECT direction FROM arcs WHERE snapshot_id=%s AND arc_id=%s",
        (net.snapshot_id, piece.parent_arc_id))["direction"]
