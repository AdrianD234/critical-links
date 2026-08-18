"""End-to-end known answers for a two-point outage.

The fixture is arithmetic anyone can check:

    MAIN    (0,0) ------------------------------------ (400,0)   400 m
    BYPASS  (0,0) - (0,-200) - (400,-200) - (400,0)               800 m

Handles at x=100 and x=200 shut 100 m of Main Road. The only way across is
100 m back west, 800 m round, and 200 m back east: 1100 m. So the replacement
is 1100 m against 100 m along the outage, added 1000 m, ratio 11.0.

The permalink tests are the ones that would rot silently. A shared span must
reopen onto the SAME road, and where the corridor was a genuine choice the
restoring request must not be free to rank it differently.
"""

from __future__ import annotations

import pytest

from nzcl import outage, snap
from nzcl.outage import HandleRef

from conftest import requires_db

pytestmark = requires_db


MAIN_BYPASS = [
    {"id": "MAIN", "pts": [(0, 0), (400, 0)], "road_name": "Main Road"},
    {"id": "BYPASS", "pts": [(0, 0), (0, -200), (400, -200), (400, 0)],
     "road_name": "Bypass Road"},
]

#: Two identical ways round, so the corridor is a real choice.
DIAMOND = [
    {"id": "STUB_A", "pts": [(-100, 0), (0, 0)]},
    {"id": "NORTH", "pts": [(0, 0), (0, 100), (200, 100), (200, 0)]},
    {"id": "SOUTH", "pts": [(0, 0), (0, -100), (200, -100), (200, 0)]},
    {"id": "STUB_B", "pts": [(200, 0), (300, 0)]},
]

#: One road, no way round at all.
LONE = [{"id": "ONLY", "pts": [(0, 0), (400, 0)], "road_name": "Only Road"}]


def refs(net, amds="MAIN", a=0.25, b=0.5):
    link = net.link_id(amds)
    return HandleRef(link, a), HandleRef(link, b)


def analyse(net, amds="MAIN", a=0.25, b=0.5, **kw):
    ra, rb = refs(net, amds, a, b)
    return outage.analyse(net.snapshot_id, ra, rb, **kw)


class TestTheHeadlineNumbers:

    def test_the_outage_is_the_road_between_the_handles(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)

        assert result.closed_length_m == pytest.approx(100.0, abs=1e-6)

    def test_the_replacement_goes_round(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)
        m = result.primary

        assert m.status == "OK"
        assert m.replacement_distance_m == pytest.approx(1100.0, abs=1e-6)

    def test_added_distance_and_ratio(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        m = analyse(net).primary

        assert m.added_distance_m == pytest.approx(1000.0, abs=1e-6)
        assert m.ratio == pytest.approx(11.0, rel=1e-9)

    def test_the_headline_comes_from_the_fixed_vocabulary(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)

        assert result.headline == "Replacement route found"
        assert result.headline in outage.HEADLINES

    def test_both_directions_are_measured_by_default(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)

        assert {m.direction for m in result.measures} == {"a_to_b", "b_to_a"}
        assert all(m.status == "OK" for m in result.measures)


class TestDirectionMode:

    def test_a_contraflow_leaves_the_other_way_running(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net, direction_mode="a_to_b")

        assert len(result.measures) == 1
        assert result.measures[0].direction == "a_to_b"
        assert result.measures[0].replacement_distance_m == pytest.approx(
            1100.0, abs=1e-6)

    def test_the_reverse_contraflow_is_the_mirror(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net, direction_mode="b_to_a")

        assert result.measures[0].direction == "b_to_a"
        assert result.measures[0].replacement_distance_m == pytest.approx(
            1100.0, abs=1e-6)


class TestWhenThereIsNoWayRound:

    def test_it_says_so_without_claiming_isolation(self, synthetic):
        net = synthetic(LONE)
        result = analyse(net, "ONLY")

        assert result.headline == "No replacement route in the represented network"
        assert all(m.status == "DISCONNECTED" for m in result.measures)
        assert all(m.resolved for m in result.measures)

    def test_no_distance_is_reported_where_none_was_found(self, synthetic):
        net = synthetic(LONE)
        m = analyse(net, "ONLY").primary

        assert m.replacement_distance_m is None
        assert m.added_distance_m is None
        assert m.ratio is None


class TestIsolationIsNotGuessed:
    """Gu has one edge per link and cannot represent half of one."""

    def test_isolation_is_absent_with_the_reason_attached(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)

        assert result.isolation is None
        assert "partial closure" in result.isolation_unavailable_reason

    def test_the_payload_carries_the_reason_rather_than_omitting_the_field(
            self, synthetic):
        net = synthetic(MAIN_BYPASS)
        payload = outage.as_dict(analyse(net))

        assert payload["isolation"] is None
        assert payload["isolationUnavailableReason"]


class TestCorridorPinning:
    """A shared span must reopen onto the same road, or say it cannot."""

    def test_the_permalink_carries_the_corridor(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)
        state = outage.permalink_state(result)

        assert state["corridorId"] == result.corridor.candidate_id
        assert state["aLinkId"] == result.handle_a.link_id
        assert state["aFraction"] == pytest.approx(0.25)
        assert state["directionMode"] == "both"

    def test_restoring_from_the_permalink_reproduces_the_span(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        first = analyse(net)
        state = outage.permalink_state(first)

        restored = outage.analyse(
            net.snapshot_id,
            HandleRef(state["aLinkId"], state["aFraction"]),
            HandleRef(state["bLinkId"], state["bFraction"]),
            profile=state["profile"], metric=state["metric"],
            direction_mode=state["directionMode"],
            corridor_id=state["corridorId"])

        assert restored.fingerprint == first.fingerprint
        assert restored.corridor.candidate_id == first.corridor.candidate_id
        assert restored.closed_length_m == pytest.approx(
            first.closed_length_m, abs=1e-9)

    def test_an_ambiguous_corridor_restores_to_the_one_that_was_shared(
            self, synthetic):
        """The case that would rot silently. Two identical ways round: without
        the pin, the restoring request is free to rank them the other way and
        close a different road under the same URL."""
        net = synthetic(DIAMOND)
        a, b = HandleRef(net.link_id("STUB_A"), 0.5), \
            HandleRef(net.link_id("STUB_B"), 0.5)
        first = outage.analyse(net.snapshot_id, a, b)
        assert first.corridor_choice.ambiguous

        # Pin the RUNNER-UP: restoring must honour it, not re-rank.
        other = first.corridor_choice.candidates[1]
        restored = outage.analyse(net.snapshot_id, a, b,
                                  corridor_id=other.candidate_id)

        assert restored.corridor.candidate_id == other.candidate_id
        assert restored.corridor.link_ids == other.link_ids
        assert restored.fingerprint != first.fingerprint

    def test_an_unknown_corridor_is_refused_rather_than_substituted(
            self, synthetic):
        net = synthetic(MAIN_BYPASS)
        a, b = refs(net)

        with pytest.raises(outage.UnknownCorridor):
            outage.analyse(net.snapshot_id, a, b, corridor_id="not-a-corridor")

    def test_an_ambiguous_choice_is_flagged_when_nothing_is_pinned(
            self, synthetic):
        net = synthetic(DIAMOND)
        result = outage.analyse(
            net.snapshot_id, HandleRef(net.link_id("STUB_A"), 0.5),
            HandleRef(net.link_id("STUB_B"), 0.5))

        assert "CORRIDOR_AMBIGUOUS" in result.quality_flags
        assert result.corridor_choice.ambiguous


class TestGeometry:

    def test_the_closure_geometry_matches_the_closed_length(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        payload = outage.as_dict(analyse(net), with_geometry=True)

        assert payload["closureGeometry"]["measuredLengthM"] == pytest.approx(
            payload["closedLengthM"], abs=0.001)

    def test_the_replacement_route_is_drawn_in_full(self, synthetic):
        """Including the two partial links at either end - without them the
        detour would start in mid air on the map."""
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)

        geom = outage.route_geometry(
            net.snapshot_id, result.primary.arc_ids, result.split.overlay)

        assert len(geom["features"]) == len(result.primary.arc_ids)
        assert any(f["properties"]["virtual"] for f in geom["features"])
        assert all(f["geometry"]["type"] == "LineString"
                   for f in geom["features"])

    def test_route_geometry_refuses_an_arc_it_cannot_draw(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        result = analyse(net)

        with pytest.raises(KeyError):
            outage.route_geometry(net.snapshot_id,
                                  [*result.primary.arc_ids, -999_999_999],
                                  result.split.overlay)


class TestDeterminism:

    def test_the_same_request_fingerprints_the_same(self, synthetic):
        net = synthetic(MAIN_BYPASS)

        assert analyse(net).fingerprint == analyse(net).fingerprint

    def test_moving_a_handle_changes_the_fingerprint(self, synthetic):
        net = synthetic(MAIN_BYPASS)

        assert analyse(net, b=0.5).fingerprint != \
            analyse(net, b=0.6).fingerprint

    def test_the_direction_mode_changes_the_fingerprint(self, synthetic):
        net = synthetic(MAIN_BYPASS)

        assert analyse(net).fingerprint != \
            analyse(net, direction_mode="a_to_b").fingerprint


class TestThePayload:

    def test_it_carries_the_measurement_caveat(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        payload = outage.as_dict(analyse(net))

        assert "says nothing about how much traffic" in \
            payload["measurementCaveat"]

    def test_it_carries_the_corridor_and_its_alternatives(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        payload = outage.as_dict(analyse(net))

        assert payload["corridor"]["roads"] == "Main Road"
        assert payload["corridorCandidates"]
        assert payload["closure"]["closedLengthM"] == pytest.approx(100.0)

    def test_it_carries_a_restorable_permalink(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        payload = outage.as_dict(analyse(net))

        assert set(payload["permalink"]) == {
            "snapshotId", "aLinkId", "aFraction", "bLinkId", "bFraction",
            "corridorId", "directionMode", "profile", "metric"}


class TestClickToSpan:
    """Snapping and analysis compose, which is what the interface does."""

    def test_two_clicks_become_a_measured_outage(self, synthetic):
        net = synthetic(MAIN_BYPASS)
        a = snap.snap(net.snapshot_id, 100.0, 30.0)
        b = snap.snap(net.snapshot_id, 200.0, 30.0)

        result = outage.analyse(
            net.snapshot_id,
            HandleRef(a.chosen.link_id, a.chosen.fraction),
            HandleRef(b.chosen.link_id, b.chosen.fraction))

        assert result.closed_length_m == pytest.approx(100.0, abs=1e-6)
        assert result.primary.replacement_distance_m == pytest.approx(
            1100.0, abs=1e-6)
