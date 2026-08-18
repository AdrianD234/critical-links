"""Known-answer tests for partial (mid-link) closure.

The fixture is built so every answer is arithmetic a reader can check without
running anything.

    MAIN     (0,0) ------------------------------------ (400,0)   400 m
    BYPASS   (0,0) - (0,-200) - (400,-200) - (400,0)               800 m

Close MAIN between x=100 and x=200. The closed stretch is 100 m. The only way
from one side of it to the other is west to (0,0), round the bypass, and back
east from (400,0):

    100 m  +  800 m  +  200 m  =  1100 m

so the replacement path is 1100 m against a normal 100 m. Nothing in these
tests is asserted approximately except where floating point demands it.

The properties with teeth are CONSERVATION - closed plus open equals the link,
so no road is invented or lost by cutting it - and CONTAINMENT: no replacement
path may traverse a piece the closure removed. The second is the stop
condition the brief names, and it is asserted directly against the returned arc
ids rather than inferred from a distance looking plausible.
"""

from __future__ import annotations

import pytest

from nzcl import db, routing, vsplit
from nzcl.vsplit import LinkInterval

from conftest import requires_db

pytestmark = requires_db


MAIN_BYPASS = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)], "road_name": "Main Road"},
    {"id": "BYPASS", "pts": [(0, 0), (0, -200), (400, -200), (400, 0)],
     "road_name": "Bypass Road"},
]

#: Same shape, but MAIN is one-way eastbound. Used to show that a directional
#: closure and a one-way carriageway are different things.
ONEWAY_MAIN = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)], "oneway": True},
    {"id": "BYPASS", "pts": [(0, 0), (0, -200), (400, -200), (400, 0)]},
]


def link_of(net, amds_id="MAIN"):
    return net.link_id(amds_id)


def length_of(net, link_id):
    row = db.query_one("SELECT length_m FROM links WHERE snapshot_id=%s "
                       "AND link_id=%s", (net.snapshot_id, link_id))
    return float(row["length_m"])


def span(net, a=0.25, b=0.5, *, mode="both", traversal="forward",
         profile="car"):
    """Close MAIN between fractions `a` and `b`."""
    link = link_of(net)
    return vsplit.build(
        net.snapshot_id,
        [LinkInterval(link, a, b, traversal, length_of(net, link))],
        handle_a=(link, a),
        handle_b=(link, b),
        profile=profile,
        direction_mode=mode,
    )


def drive(net, s, u=None, v=None, metric="distance"):
    return routing.route(
        net.snapshot_id,
        s.node_at_a if u is None else u,
        s.node_at_b if v is None else v,
        metric=metric,
        excluded_arcs=s.excluded_arc_ids,
        overlay=s.overlay,
    )


class TestTheSplitConservesTheRoad:
    """Cutting a link must not create or destroy road."""

    def test_closed_length_is_the_stretch_between_the_handles(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        assert s.closed_length_m == pytest.approx(100.0, abs=1e-6)

    def test_open_pieces_plus_closed_equals_the_whole_link(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)
        link_len = length_of(net, link_of(net))

        # Every piece of the forward direction, open or closed, tiles the link.
        forward = [a for a in s.overlay.arcs
                   if a.cost_distance_m > 0 and a.source < a.target or True]
        open_forward = sum(
            a.cost_distance_m for a in s.overlay.arcs
            if _is_forward_piece(s, a)
        )
        assert open_forward + s.closed_length_m == pytest.approx(
            link_len, abs=1e-6)
        assert forward  # the fixture really does produce pieces

    def test_both_directions_are_split(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        # Two open pieces each way, one closed piece each way.
        assert len(s.overlay.arcs) == 4
        assert len(s.closed_piece_ids) == 2

    def test_the_real_arcs_are_superseded_not_left_in_place(self, synthetic):
        """Leaving the un-split original in the edge set would let a route
        drive straight through the closure."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        real = db.query("SELECT arc_id FROM arcs WHERE snapshot_id=%s AND "
                        "link_id=%s ORDER BY arc_id",
                        (net.snapshot_id, link_of(net)))
        assert s.excluded_arc_ids == [int(r["arc_id"]) for r in real]


class TestTheReplacementPath:
    """The number the feature exists to produce."""

    def test_the_detour_is_the_bypass(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        r = drive(net, s)

        assert r.status == "OK"
        assert r.distance_m == pytest.approx(1100.0, abs=1e-6)

    def test_no_replacement_path_traverses_the_closed_stretch(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        r = drive(net, s)

        assert set(r.arc_ids).isdisjoint(s.closed_piece_ids)
        assert set(r.arc_ids).isdisjoint(s.excluded_arc_ids)

    def test_the_path_uses_the_open_remainders_of_the_cut_link(self, synthetic):
        """The 100 m back to (0,0) and the 200 m in from (400,0) are pieces of
        MAIN, not of any other link. If the overlay were missing they would be
        unreachable and the detour would be DISCONNECTED."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        r = drive(net, s)

        used_virtual = [a for a in r.arc_ids if a < 0]
        assert len(used_virtual) == 2
        assert set(used_virtual).issubset(s.open_piece_ids)

    def test_a_wider_outage_leaves_a_shorter_run_in(self, synthetic):
        """Moving B east to x=300 closes 200 m and lengthens the run back in."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.75)

        r = drive(net, s)

        assert s.closed_length_m == pytest.approx(200.0, abs=1e-6)
        # 100 west + 800 bypass + 100 back in.
        assert r.distance_m == pytest.approx(1000.0, abs=1e-6)


class TestDirection:
    """Handle order is what orients the closure."""

    def test_closing_a_to_b_leaves_b_to_a_running(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5, mode="a_to_b")

        outbound = drive(net, s)
        inbound = drive(net, s, u=s.node_at_b, v=s.node_at_a)

        assert outbound.distance_m == pytest.approx(1100.0, abs=1e-6)
        # The contraflow direction is untouched: straight through, 100 m.
        assert inbound.status == "OK"
        assert inbound.distance_m == pytest.approx(100.0, abs=1e-6)

    def test_closing_b_to_a_is_the_mirror_image(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5, mode="b_to_a")

        assert drive(net, s).distance_m == pytest.approx(100.0, abs=1e-6)
        assert drive(net, s, u=s.node_at_b, v=s.node_at_a).distance_m == \
            pytest.approx(1100.0, abs=1e-6)

    def test_closing_both_stops_both(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5, mode="both")

        assert drive(net, s).distance_m == pytest.approx(1100.0, abs=1e-6)
        assert drive(net, s, u=s.node_at_b, v=s.node_at_a).distance_m == \
            pytest.approx(1100.0, abs=1e-6)

    def test_a_directional_closure_halves_the_pieces_removed(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        both = span(net, 0.25, 0.5, mode="both")
        one_way = span(net, 0.25, 0.5, mode="a_to_b")

        assert len(both.closed_piece_ids) == 2
        assert len(one_way.closed_piece_ids) == 1
        # The reverse piece across the closed stretch survives as an open edge.
        assert len(one_way.overlay.arcs) == len(both.overlay.arcs) + 1

    def test_traversal_against_the_geometry_closes_the_other_arc(self, synthetic):
        """A corridor running B -> A along a link stored west-to-east still has
        to close the arc the traffic is on."""
        net = synthetic(MAIN_BYPASS)
        link = link_of(net)
        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.25, 0.5, "reverse", length_of(net, link))],
            handle_a=(link, 0.5), handle_b=(link, 0.25),
            direction_mode="a_to_b",
        )
        # A is at 0.5 and B at 0.25, so A -> B runs east-to-west: the REVERSE
        # arc. Driving A -> B must detour; the forward arc stays open.
        assert drive(net, s).distance_m == pytest.approx(1100.0, abs=1e-6)
        assert drive(net, s, u=s.node_at_b, v=s.node_at_a).distance_m == \
            pytest.approx(100.0, abs=1e-6)


#: Three links end to end, with a long bypass round the outside.
#:
#:     WEST (0,0)--(200,0)  MID (200,0)--(400,0)  EAST (400,0)--(600,0)
#:     BYPASS (0,0)-(0,-300)-(600,-300)-(600,0)                  1200 m
CHAIN = [
    {"id": "WEST", "pts": [(0, 0), (200, 0)], "road_name": "Coast Road"},
    {"id": "MID", "pts": [(200, 0), (400, 0)], "road_name": "Coast Road"},
    {"id": "EAST", "pts": [(400, 0), (600, 0)], "road_name": "Coast Road"},
    {"id": "BYPASS", "pts": [(0, 0), (0, -300), (600, -300), (600, 0)],
     "road_name": "Inland Road"},
]


class TestASpanAcrossSeveralLinks:
    """The case the whole feature is for: A and B on different links.

    The rule is: close the part of the first link AFTER A, every link wholly
    between, and the part of the last link BEFORE B. Everything outside that
    stays open, including the rest of the two end links.

    A sits 100 m along WEST and B sits 100 m into EAST, so 400 m of Coast Road
    is shut and the bypass is 100 + 1200 + 100 = 1400 m.
    """

    def build(self, net, mode="both"):
        west, mid, east = (net.link_id("WEST"), net.link_id("MID"),
                           net.link_id("EAST"))
        return vsplit.build(
            net.snapshot_id,
            [
                LinkInterval(west, 0.5, 1.0, "forward", length_of(net, west)),
                LinkInterval(mid, 0.0, 1.0, "forward", length_of(net, mid)),
                LinkInterval(east, 0.0, 0.5, "forward", length_of(net, east)),
            ],
            handle_a=(west, 0.5), handle_b=(east, 0.5),
            direction_mode=mode,
        )

    def test_the_closed_length_is_the_road_between_the_handles(self, synthetic):
        net = synthetic(CHAIN)
        s = self.build(net)

        assert s.closed_length_m == pytest.approx(400.0, abs=1e-6)

    def test_the_middle_link_needs_no_split(self, synthetic):
        """A link wholly inside the span is closed by excluding its real arcs.
        Cutting it would be work with no effect."""
        net = synthetic(CHAIN)
        s = self.build(net)
        mid = net.link_id("MID")

        assert all(a.link_id != mid for a in s.overlay.arcs)
        mid_arcs = db.query("SELECT arc_id FROM arcs WHERE snapshot_id=%s AND "
                            "link_id=%s", (net.snapshot_id, mid))
        assert {int(r["arc_id"]) for r in mid_arcs} <= set(s.excluded_arc_ids)

    def test_the_replacement_path_is_the_bypass(self, synthetic):
        net = synthetic(CHAIN)
        s = self.build(net)

        r = drive(net, s)

        assert r.status == "OK"
        assert r.distance_m == pytest.approx(1400.0, abs=1e-6)
        assert set(r.arc_ids).isdisjoint(s.closed_piece_ids)
        assert set(r.arc_ids).isdisjoint(s.excluded_arc_ids)

    def test_the_road_outside_the_span_stays_open(self, synthetic):
        """The first 100 m of WEST and the last 100 m of EAST are not part of
        the outage and must still carry traffic. This is the property a
        whole-link closure cannot express: it would have shut all 600 m."""
        net = synthetic(CHAIN)
        s = self.build(net)
        west_start = db.query_one(
            "SELECT source_node FROM links WHERE snapshot_id=%s AND link_id=%s",
            (net.snapshot_id, net.link_id("WEST")))["source_node"]

        # From the western end of the network to handle A: 100 m of open road.
        r = drive(net, s, u=int(west_start), v=s.node_at_a)

        assert r.status == "OK"
        assert r.distance_m == pytest.approx(100.0, abs=1e-6)

    def test_closing_more_road_than_the_span_would_be_visible_here(
            self, synthetic):
        """Guards the stop condition directly: the total closed must equal the
        span the handles describe, not the links they happen to sit on."""
        net = synthetic(CHAIN)
        s = self.build(net)

        whole_links = sum(length_of(net, net.link_id(n))
                          for n in ("WEST", "MID", "EAST"))
        assert whole_links == pytest.approx(600.0, abs=1e-6)
        assert s.closed_length_m == pytest.approx(400.0, abs=1e-6)
        assert s.closed_length_m < whole_links

    def test_a_directional_span_leaves_the_return_running(self, synthetic):
        net = synthetic(CHAIN)
        s = self.build(net, mode="a_to_b")

        assert drive(net, s).distance_m == pytest.approx(1400.0, abs=1e-6)
        assert drive(net, s, u=s.node_at_b, v=s.node_at_a).distance_m == \
            pytest.approx(400.0, abs=1e-6)


class TestHandlesOnJunctions:
    """A handle at an end is already a node; it must not grow a virtual one."""

    def test_a_handle_at_fraction_zero_uses_the_real_node(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        link = link_of(net)
        row = db.query_one("SELECT source_node FROM links WHERE snapshot_id=%s "
                           "AND link_id=%s", (net.snapshot_id, link))

        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.0, 0.5, "forward", length_of(net, link))],
            handle_a=(link, 0.0), handle_b=(link, 0.5),
        )

        assert s.node_at_a == int(row["source_node"])
        assert s.node_at_a >= 0

    def test_a_whole_link_closure_needs_no_overlay_at_all(self, synthetic):
        """Both handles on junctions is the old whole-link closure, and it must
        reduce to exactly that: real arcs excluded, nothing virtual."""
        net = synthetic(MAIN_BYPASS)
        link = link_of(net)

        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.0, 1.0, "forward", length_of(net, link))],
            handle_a=(link, 0.0), handle_b=(link, 1.0),
        )

        assert s.overlay.arcs == ()
        assert s.closed_piece_ids == []
        assert len(s.excluded_arc_ids) == 2
        assert not s.splits_a_link
        assert s.closed_length_m == pytest.approx(400.0, abs=1e-6)

    def test_the_whole_link_closure_still_routes_round(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        link = link_of(net)
        s = vsplit.build(
            net.snapshot_id,
            [LinkInterval(link, 0.0, 1.0, "forward", length_of(net, link))],
            handle_a=(link, 0.0), handle_b=(link, 1.0),
        )

        r = drive(net, s)
        assert r.status == "OK"
        assert r.distance_m == pytest.approx(800.0, abs=1e-6)


class TestOneWayHosts:
    """A one-way link has one arc, so it has one set of pieces."""

    def test_only_the_permitted_direction_is_split(self, synthetic):
        net = synthetic(ONEWAY_MAIN)
        s = span(net, 0.25, 0.5)

        # One arc, cut into three: two open, one closed.
        assert len(s.overlay.arcs) == 2
        assert len(s.closed_piece_ids) == 1
        assert len(s.excluded_arc_ids) == 1

    def test_a_vehicle_caught_between_the_handles_has_nowhere_legal_to_go(
            self, synthetic):
        """DISCONNECTED here is correct, and it is the one-way phenomenon
        `ports.py` documents rather than a defect in the split.

        A is 100 m along a one-way eastbound carriageway and the next 100 m is
        shut. Nothing may reverse, and no other road touches A, so there is no
        represented path to B. The endpoint measure is undefined for exactly
        this reason - which is why the port measure exists, and why an outage
        on a one-way road has to be reported from its boundary rather than
        from its handles.
        """
        net = synthetic(ONEWAY_MAIN)
        s = span(net, 0.25, 0.5)

        r = drive(net, s)
        assert r.status == "DISCONNECTED"

    def test_the_bypass_is_reachable_from_before_the_outage(self, synthetic):
        """The road either side of it is not stranded: entering the corridor
        from its start, the bypass still gets you past."""
        net = synthetic(ONEWAY_MAIN)
        s = span(net, 0.25, 0.5)
        row = db.query_one("SELECT source_node, target_node FROM links WHERE "
                           "snapshot_id=%s AND link_id=%s",
                           (net.snapshot_id, link_of(net)))

        r = drive(net, s, u=int(row["source_node"]), v=int(row["target_node"]))

        assert r.status == "OK"
        assert r.distance_m == pytest.approx(800.0, abs=1e-6)


class TestDeterminism:
    """The same closure must produce the same graph, every time."""

    def test_ids_and_fingerprint_are_stable_across_rebuilds(self, synthetic):
        net = synthetic(MAIN_BYPASS)

        first = span(net, 0.25, 0.5)
        second = span(net, 0.25, 0.5)

        assert first.fingerprint == second.fingerprint
        assert first.node_at_a == second.node_at_a
        assert first.node_at_b == second.node_at_b
        assert first.open_piece_ids == second.open_piece_ids
        assert first.closed_piece_ids == second.closed_piece_ids
        assert [(a.arc_id, a.source, a.target, a.cost_distance_m)
                for a in first.overlay.arcs] == \
               [(a.arc_id, a.source, a.target, a.cost_distance_m)
                for a in second.overlay.arcs]

    def test_the_fingerprint_tracks_what_is_closed(self, synthetic):
        net = synthetic(MAIN_BYPASS)

        assert span(net, 0.25, 0.5).fingerprint != \
               span(net, 0.25, 0.6).fingerprint
        assert span(net, 0.25, 0.5, mode="both").fingerprint != \
               span(net, 0.25, 0.5, mode="a_to_b").fingerprint

    def test_virtual_ids_are_all_negative(self, synthetic):
        """The whole collision argument rests on this."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        assert all(a.arc_id < 0 for a in s.overlay.arcs)
        assert all(i < 0 for i in s.closed_piece_ids)
        assert s.node_at_a < 0 and s.node_at_b < 0
        assert all(n < 0 for n in s.overlay.component_anchor)
        assert all(real >= 0 for real in s.overlay.component_anchor.values())

    def test_nothing_virtual_is_ever_numbered_minus_one(self, synthetic):
        """Regression. pgRouting marks the end of a path with `edge = -1`, so
        an arc numbered -1 is filtered out of its own route and its length
        disappears from the total with nothing reporting a problem. This
        fixture measured 1000 m instead of 1100 m until the id was reserved."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        minted = ([a.arc_id for a in s.overlay.arcs] + s.closed_piece_ids
                  + [s.node_at_a, s.node_at_b]
                  + list(s.overlay.component_anchor))
        assert routing.RESERVED_EDGE_SENTINEL not in minted

    def test_the_two_directions_of_one_closure_measure_the_same(self, synthetic):
        """The symmetry the sentinel collision broke: with both directions shut
        and a symmetric bypass, A -> B and B -> A are the same length."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        there = drive(net, s)
        back = drive(net, s, u=s.node_at_b, v=s.node_at_a)

        assert there.distance_m == pytest.approx(back.distance_m, abs=1e-6)


class TestTheSnapshotIsNotTouched:
    """A request-local graph is local to the request."""

    def test_no_rows_are_added_to_arcs_or_nodes(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        before = _counts(net.snapshot_id)

        s = span(net, 0.25, 0.5)
        drive(net, s)

        assert _counts(net.snapshot_id) == before

    def test_a_second_request_cannot_see_the_first_ones_pieces(self, synthetic):
        """Concurrency safety, stated as an observable property: a split built
        for one span has no effect on a search that does not carry it."""
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        link = link_of(net)
        row = db.query_one("SELECT source_node, target_node FROM links "
                           "WHERE snapshot_id=%s AND link_id=%s",
                           (net.snapshot_id, link))
        plain = routing.route(net.snapshot_id, int(row["source_node"]),
                              int(row["target_node"]))

        # Straight along MAIN: the other request's closure is invisible here.
        assert plain.status == "OK"
        assert plain.distance_m == pytest.approx(400.0, abs=1e-6)


class TestTimeMetric:
    """Pieces carry an apportioned time cost, so a time search still works."""

    def test_time_is_apportioned_by_fraction(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        s = span(net, 0.25, 0.5)

        r = drive(net, s, metric="time")
        assert r.status == "OK"
        # 1100 m at the fixture's uniform 50 km/h.
        assert r.time_s == pytest.approx(1100.0 / (50_000.0 / 3600.0), rel=1e-9)


class TestGuards:

    def test_an_empty_span_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        with pytest.raises(ValueError):
            vsplit.build(net.snapshot_id, [], handle_a=(0, 0.0),
                         handle_b=(0, 1.0))

    def test_an_inverted_interval_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        link = link_of(net)
        with pytest.raises(ValueError):
            vsplit.build(
                net.snapshot_id,
                [LinkInterval(link, 0.8, 0.2, "forward", 400.0)],
                handle_a=(link, 0.8), handle_b=(link, 0.2))

    def test_an_unknown_link_is_refused(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        with pytest.raises(KeyError):
            vsplit.build(
                net.snapshot_id,
                [LinkInterval(999_999, 0.2, 0.8, "forward", 400.0)],
                handle_a=(999_999, 0.2), handle_b=(999_999, 0.8))


def _is_forward_piece(s, arc):
    """A piece derived from the forward arc of its parent link."""
    row = db.query_one("SELECT direction FROM arcs WHERE snapshot_id=%s AND "
                       "arc_id=%s", (s.snapshot_id, arc.parent_arc_id))
    return row is not None and row["direction"] == "forward"


def _counts(snapshot_id):
    return (
        db.query_one("SELECT count(*) AS n FROM arcs WHERE snapshot_id=%s",
                     (snapshot_id,))["n"],
        db.query_one("SELECT count(*) AS n FROM nodes WHERE snapshot_id=%s",
                     (snapshot_id,))["n"],
        db.query_one("SELECT count(*) AS n FROM links WHERE snapshot_id=%s",
                     (snapshot_id,))["n"],
    )
