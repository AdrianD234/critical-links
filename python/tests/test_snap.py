"""Known-answer tests for click-to-centreline snapping.

The properties that matter are geometric and can be asserted exactly, because
the fixtures are straight lines with known lengths: a click 30 m to the side of
a road running east at y=0 snaps to the point directly beside it, at a distance
along equal to its own easting. Nothing here is approximate, so nothing here is
asserted approximately.

The ambiguity tests are the ones with teeth. A junction MUST NOT report as
ambiguous - several links are equidistant, but they all snap to the same place,
so there is no decision to interrupt for. Parallel carriageways MUST report as
ambiguous, because choosing the nearer by a metre chooses by pointing noise and
closes a carriageway carrying traffic the other way.
"""

from __future__ import annotations

import pytest

from nzcl import snap

from conftest import requires_db

pytestmark = requires_db


#: A straight east-west road with one side road landing on it at x=100, which
#: splits the through road into two children.
TEE = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)], "road_name": "Main Road"},
    {"id": "SIDE", "pts": [(100, -150), (100, 0)], "road_name": "Side Street"},
]

#: Two carriageways of one road, 20 m apart, joined at both ends. A click in
#: the middle is 10 m from each and cannot separate them.
DIVIDED = [
    {"id": "NB", "pts": [(0, 0), (0, 300)], "oneway": True,
     "road_name": "Great North Road"},
    {"id": "SB", "pts": [(20, 300), (20, 0)], "oneway": True,
     "road_name": "Great North Road"},
    {"id": "XN", "pts": [(0, 300), (20, 300)], "road_name": "North Cross"},
    {"id": "XS", "pts": [(20, 0), (0, 0)], "road_name": "South Cross"},
]


class TestSnapsToTheCentrelineNotTheVertex:
    """A snap lands on the polyline, wherever that is - not on a vertex."""

    def test_offset_click_snaps_to_the_point_beside_it(self, synthetic):
        net = synthetic(TEE)
        # 30 m north of the through road, 250 m along it. The nearest point on
        # the centreline is (250, 0): not a vertex, not the link's midpoint.
        r = snap.snap(net.snapshot_id, 250.0, 30.0)

        assert r.found
        h = r.chosen
        assert h.x == pytest.approx(250.0, abs=1e-6)
        assert h.y == pytest.approx(0.0, abs=1e-6)
        assert h.offset_m == pytest.approx(30.0, abs=1e-6)

    def test_distance_along_is_measured_on_the_host_child(self, synthetic):
        """The through road is split at the junction, so 'along' is along the
        CHILD that hosts the click, and the child starts at x=100."""
        net = synthetic(TEE)
        r = snap.snap(net.snapshot_id, 250.0, 30.0)
        h = r.chosen

        assert h.closure_group_id == "MAIN"
        assert h.amds_id.startswith("MAIN")
        # Child runs x=100 -> x=400, so 250 is 150 m along a 300 m link.
        assert h.length_m == pytest.approx(300.0, abs=1e-6)
        assert h.distance_along_m == pytest.approx(150.0, abs=1e-6)
        assert h.fraction == pytest.approx(0.5, abs=1e-9)

    def test_click_beyond_the_end_clamps_to_the_end(self, synthetic):
        """Past the end of the network the nearest point is the terminus."""
        net = synthetic(TEE)
        r = snap.snap(net.snapshot_id, 460.0, 0.0)

        assert r.found
        assert r.chosen.x == pytest.approx(400.0, abs=1e-6)
        assert r.chosen.fraction == pytest.approx(1.0, abs=1e-9)
        assert r.chosen.at_end

    def test_nothing_within_the_radius_is_reported_as_not_found(self, synthetic):
        net = synthetic(TEE)
        r = snap.snap(net.snapshot_id, 5_000.0, 5_000.0, search_radius_m=100.0)

        assert not r.found
        assert r.chosen is None
        assert r.candidates == []
        # Not finding a road is not an ambiguity.
        assert not r.ambiguous


class TestRankingIsByTrueDistance:
    """Bounding-box order is not distance order; the shortlist is re-ranked."""

    def test_the_nearer_road_wins_even_though_the_other_is_longer(self, synthetic):
        net = synthetic(TEE)
        # Just north of the side road, well away from the through road.
        r = snap.snap(net.snapshot_id, 105.0, -100.0)

        assert r.chosen.closure_group_id == "SIDE"
        assert r.chosen.offset_m == pytest.approx(5.0, abs=1e-6)


class TestAmbiguityIsReportedNotGuessed:

    def test_a_junction_is_not_ambiguous(self, synthetic):
        """Three links meet at (100, 0) and all snap there. Same place, so
        there is nothing for the user to decide."""
        net = synthetic(TEE)
        r = snap.snap(net.snapshot_id, 100.0, 4.0)

        assert r.found
        assert len(r.candidates) >= 2
        # Rivals really are equidistant - this is the case that would trip a
        # naive offset-only test.
        assert r.candidates[1].offset_m - r.candidates[0].offset_m < 1e-6
        assert not r.ambiguous
        assert r.ambiguity_reason is None

    def test_parallel_carriageways_are_ambiguous(self, synthetic):
        """A click midway between two carriageways is genuinely two answers."""
        net = synthetic(DIVIDED)
        r = snap.snap(net.snapshot_id, 10.0, 150.0)

        assert r.found
        assert r.ambiguous
        assert r.ambiguity_reason is not None
        # Both carriageways carry the same name, so the message says so.
        assert "Great North Road" in r.ambiguity_reason
        assert "carriageways" in r.ambiguity_reason

        rival = r.candidates[1]
        assert {r.chosen.closure_group_id, rival.closure_group_id} == {"NB", "SB"}

    def test_clearly_on_one_carriageway_is_not_ambiguous(self, synthetic):
        """Standing on the northbound line, the southbound is 20 m away - far
        outside pointing noise, so nothing is asked."""
        net = synthetic(DIVIDED)
        r = snap.snap(net.snapshot_id, 0.0, 150.0)

        assert r.found
        assert r.chosen.closure_group_id == "NB"
        assert not r.ambiguous

    def test_the_two_carriageways_run_opposite_ways(self, synthetic):
        """The reason the choice matters: they are not interchangeable."""
        net = synthetic(DIVIDED)
        r = snap.snap(net.snapshot_id, 10.0, 150.0)

        chosen, rival = r.chosen, r.candidates[1]
        assert chosen.forward_allowed and not chosen.reverse_allowed
        assert rival.forward_allowed and not rival.reverse_allowed
        # One runs north, the other south: their geometries disagree on which
        # way "along" points, which is exactly why the handle must name one.
        assert chosen.oneway == 1 and rival.oneway == 1


class TestRebuildingAHandle:
    """A permalink and a drag both reload a handle by position, not by click."""

    def test_at_position_round_trips_a_snap(self, synthetic):
        net = synthetic(TEE)
        clicked = snap.snap(net.snapshot_id, 250.0, 30.0).chosen

        rebuilt = snap.at_position(net.snapshot_id, clicked.link_id,
                                   distance_along_m=clicked.distance_along_m)

        assert rebuilt.link_id == clicked.link_id
        assert rebuilt.fraction == pytest.approx(clicked.fraction, abs=1e-9)
        assert rebuilt.x == pytest.approx(clicked.x, abs=1e-6)
        assert rebuilt.y == pytest.approx(clicked.y, abs=1e-6)
        assert rebuilt.stable_key == clicked.stable_key
        # A rebuilt handle is on the line by construction, so it has no offset.
        assert rebuilt.offset_m == 0.0

    def test_fraction_and_distance_are_the_same_position(self, synthetic):
        net = synthetic(TEE)
        link = snap.snap(net.snapshot_id, 250.0, 30.0).chosen.link_id

        by_distance = snap.at_position(net.snapshot_id, link,
                                       distance_along_m=75.0)
        by_fraction = snap.at_position(net.snapshot_id, link, fraction=0.25)

        assert by_distance.x == pytest.approx(by_fraction.x, abs=1e-6)
        assert by_distance.stable_key == by_fraction.stable_key

    def test_supplying_both_or_neither_is_rejected(self, synthetic):
        net = synthetic(TEE)
        link = snap.snap(net.snapshot_id, 250.0, 30.0).chosen.link_id

        with pytest.raises(ValueError):
            snap.at_position(net.snapshot_id, link)
        with pytest.raises(ValueError):
            snap.at_position(net.snapshot_id, link, distance_along_m=10.0,
                             fraction=0.1)

    def test_a_position_past_the_end_clamps(self, synthetic):
        net = synthetic(TEE)
        link = snap.snap(net.snapshot_id, 250.0, 30.0).chosen.link_id

        h = snap.at_position(net.snapshot_id, link, distance_along_m=99_999.0)
        assert h.fraction == 1.0


class TestHandleIdentity:
    """Keys are hashed from what the publisher chose, never from `link_id`."""

    def test_the_same_position_gives_the_same_key(self):
        a = snap.handle_key("ABC-123", 150.0)
        b = snap.handle_key("ABC-123", 150.0)
        assert a == b

    def test_a_millimetre_apart_is_a_different_handle(self):
        assert snap.handle_key("ABC-123", 150.000) != \
               snap.handle_key("ABC-123", 150.001)

    def test_different_roads_never_share_a_key(self):
        assert snap.handle_key("ABC-123", 150.0) != \
               snap.handle_key("DEF-456", 150.0)


class TestGuardsOnInput:

    def test_an_absurd_radius_is_refused(self, synthetic):
        net = synthetic(TEE)
        with pytest.raises(ValueError):
            snap.snap(net.snapshot_id, 0.0, 0.0, search_radius_m=1e9)
        with pytest.raises(ValueError):
            snap.snap(net.snapshot_id, 0.0, 0.0, search_radius_m=0.0)

    def test_an_unknown_profile_is_refused(self, synthetic):
        net = synthetic(TEE)
        with pytest.raises(ValueError):
            snap.snap(net.snapshot_id, 0.0, 0.0, profile="hovercraft")

    def test_an_unknown_link_is_refused(self, synthetic):
        net = synthetic(TEE)
        with pytest.raises(KeyError):
            snap.at_position(net.snapshot_id, 999_999, fraction=0.5)
