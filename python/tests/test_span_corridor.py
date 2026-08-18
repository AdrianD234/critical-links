"""Known-answer tests for corridor selection between two handles.

The test that matters most is `TestTheShortestPathIsNotTheCorridor`. Two
handles fix two points, not a road, and between any two points the shortest way
through is frequently not the one the user drew. A plain shortest-path search
would close a side street and report a confident number about a road nobody
selected - so the fixture is built with a genuine short cut, and the corridor
that stays on the named road has to win despite being 76 m longer.

The second is ambiguity. Where two corridors really are equally well
evidenced, the engine must offer both rather than break the tie on length and
present the result as though it were the only reading.
"""

from __future__ import annotations

import pytest

from nzcl import snap, span_corridor
from nzcl.span_corridor import HandleOption

from conftest import requires_db

pytestmark = requires_db


#: Church Street turns a corner; Short Cut chops it off.
#:
#:      (0,0)---A---+--------------(300,0)
#:                  |                  |
#:                  |  CUT             | CHURCH_N
#:                  +--------------(300,100)
#:                                     |
#:                                     B
#:                                 (300,300)
#:
#: Along Church from A to B is 450 m. Across the cut it is 373.6 m. The user
#: drew along Church Street.
CORNER = [
    {"id": "CHURCH_W", "pts": [(0, 0), (300, 0)], "road_name": "Church Street"},
    {"id": "CHURCH_N", "pts": [(300, 0), (300, 300)],
     "road_name": "Church Street"},
    {"id": "CUT", "pts": [(100, 0), (300, 100)], "road_name": "Short Cut"},
]

#: Two unnamed ways round, exactly the same length. Nothing separates them.
DIAMOND = [
    {"id": "STUB_A", "pts": [(-100, 0), (0, 0)]},
    {"id": "NORTH", "pts": [(0, 0), (0, 100), (200, 100), (200, 0)]},
    {"id": "SOUTH", "pts": [(0, 0), (0, -100), (200, -100), (200, 0)]},
    {"id": "STUB_B", "pts": [(200, 0), (300, 0)]},
]

#: One straight road, for the same-link case.
STRAIGHT = [
    {"id": "LONG", "pts": [(0, 0), (1000, 0)], "road_name": "Long Road"},
]


def opt(net, amds, fraction):
    return HandleOption(net.link_id(amds), fraction)


def child_at(net, prefix, x, y):
    """The child of `prefix` whose geometry covers (x, y)."""
    from nzcl import db
    row = db.query_one(
        "SELECT link_id, amds_id FROM links WHERE snapshot_id=%s "
        "  AND closure_group_id=%s "
        "  AND ST_DWithin(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), 0.001) "
        " ORDER BY link_id LIMIT 1",
        (net.snapshot_id, prefix, x, y))
    assert row is not None, f"no child of {prefix} covers ({x}, {y})"
    return int(row["link_id"])


def handle(net, x, y):
    """Snap a click and hand back every host it could sit on."""
    return span_corridor.options_from_snap(snap.snap(net.snapshot_id, x, y))


class TestASpanOnOneLink:

    def test_one_candidate_covering_the_stretch_between(self, synthetic):
        net = synthetic(STRAIGHT)
        choice = span_corridor.select(
            net.snapshot_id, [opt(net, "LONG", 0.2)], [opt(net, "LONG", 0.6)])

        assert choice.found
        assert len(choice.candidates) == 1
        c = choice.chosen
        assert c.origin == "same_link"
        assert c.length_m == pytest.approx(400.0, abs=1e-6)
        assert len(c.steps) == 1
        assert c.steps[0].from_fraction == pytest.approx(0.2)
        assert c.steps[0].to_fraction == pytest.approx(0.6)

    def test_handle_order_sets_the_traversal(self, synthetic):
        net = synthetic(STRAIGHT)
        onward = span_corridor.select(
            net.snapshot_id, [opt(net, "LONG", 0.2)], [opt(net, "LONG", 0.6)])
        backward = span_corridor.select(
            net.snapshot_id, [opt(net, "LONG", 0.6)], [opt(net, "LONG", 0.2)])

        assert onward.chosen.steps[0].traversal == "forward"
        assert backward.chosen.steps[0].traversal == "reverse"
        # Same road either way.
        assert onward.chosen.length_m == pytest.approx(
            backward.chosen.length_m, abs=1e-9)

    def test_a_single_link_span_is_never_ambiguous(self, synthetic):
        net = synthetic(STRAIGHT)
        choice = span_corridor.select(
            net.snapshot_id, [opt(net, "LONG", 0.2)], [opt(net, "LONG", 0.6)])

        assert not choice.ambiguous


class TestTheShortestPathIsNotTheCorridor:
    """The defect this module exists to prevent."""

    def build(self, net):
        a = child_at(net, "CHURCH_W", 50, 0)
        b = child_at(net, "CHURCH_N", 300, 200)
        return span_corridor.select(
            net.snapshot_id,
            [HandleOption(a, 0.5)], [HandleOption(b, 0.5)])

    def test_the_named_road_wins_despite_being_longer(self, synthetic):
        net = synthetic(CORNER)
        choice = self.build(net)

        assert choice.found
        chosen = choice.chosen
        roads = {s.road_name for s in chosen.steps}
        assert roads == {"Church Street"}, (
            f"the corridor left the road the handles are on: "
            f"{span_corridor._describe(chosen)}")
        assert chosen.length_m == pytest.approx(450.0, abs=1e-6)

    def test_the_short_cut_really_is_shorter(self, synthetic):
        """Guards the fixture: if the cut were not shorter, the test above
        would pass for the wrong reason."""
        net = synthetic(CORNER)
        choice = self.build(net)

        via_cut = [c for c in choice.candidates
                   if any(s.road_name == "Short Cut" for s in c.steps)]
        assert via_cut, "the short cut was never generated as a candidate"
        assert via_cut[0].length_m < choice.chosen.length_m

    def test_the_evidence_is_what_separates_them(self, synthetic):
        net = synthetic(CORNER)
        choice = self.build(net)
        via_cut = next(c for c in choice.candidates
                       if any(s.road_name == "Short Cut" for s in c.steps))

        assert choice.chosen.name_continuous
        assert choice.chosen.road_changes == 0
        assert not via_cut.name_continuous
        assert via_cut.road_changes >= 1

    def test_the_alternative_is_still_offered(self, synthetic):
        """Ranked below, not hidden: the user may have meant the cut."""
        net = synthetic(CORNER)
        choice = self.build(net)

        assert len(choice.candidates) >= 2
        assert choice.candidates[0] is choice.chosen

    def test_it_is_not_reported_as_ambiguous(self, synthetic):
        """The evidence separates them cleanly, so nothing is asked."""
        net = synthetic(CORNER)
        choice = self.build(net)

        assert not choice.ambiguous


#: Two links of one road meeting at a junction, with a way round.
#:
#:     SEG_A (0,0)--A--(200,0)   SEG_B (200,0)--B--(400,0)   High Street
#:     LOOP  (0,0)-(0,80)-(400,80)-(400,0)                   Ring Road, 560 m
#:
#: The corridor through the shared junction is 200 m; round the loop it is
#: 100 + 560 + 100 = 760 m, which is 3.8x - inside the plausibility bound, so
#: it stays on the list as a genuine alternative.
ADJACENT = [
    {"id": "SEG_A", "pts": [(0, 0), (200, 0)], "road_name": "High Street"},
    {"id": "SEG_B", "pts": [(200, 0), (400, 0)], "road_name": "High Street"},
    {"id": "LOOP", "pts": [(0, 0), (0, 80), (400, 80), (400, 0)],
     "road_name": "Ring Road"},
]


class TestHandlesOnAdjacentLinks:
    """Regression: the most ordinary corridor of all.

    Two handles on links that meet at a junction. The corridor is simply the
    run from A to that junction and on to B - 200 m here - and it was never
    generated, because `pgr_dijkstra` returns no row for a pair whose start
    vertex IS its end vertex. Selection fell through to whatever long way
    round existed.

    On real data this turned a 185 m outage on State Highway 6 into an 813 m
    one across four other streets, with nothing indicating anything was wrong:
    the corridor was returned, drawn, closed and measured, and every number
    downstream of it was internally consistent.
    """

    def build(self, net):
        return span_corridor.select(
            net.snapshot_id, [opt(net, "SEG_A", 0.5)], [opt(net, "SEG_B", 0.5)])

    def test_the_corridor_runs_through_the_shared_junction(self, synthetic):
        net = synthetic(ADJACENT)
        choice = self.build(net)

        assert choice.found
        assert len(choice.chosen.steps) == 2
        assert choice.chosen.length_m == pytest.approx(200.0, abs=1e-6)

    def test_it_stays_on_the_road_the_handles_are_on(self, synthetic):
        net = synthetic(ADJACENT)
        choice = self.build(net)

        assert {s.road_name for s in choice.chosen.steps} == {"High Street"}
        assert choice.chosen.road_changes == 0
        assert choice.chosen.name_continuous

    def test_the_long_way_round_does_not_win(self, synthetic):
        """Ring Road was the answer before the shared-junction corridor was
        built explicitly."""
        net = synthetic(ADJACENT)
        choice = self.build(net)

        assert net.link_id("LOOP") not in choice.chosen.link_ids
        assert choice.chosen.length_m < 300.0

    def test_an_implausibly_long_alternative_is_dropped(self):
        """A corridor several times the length of the shortest is not a
        reading of what the user drew, and padding the choice with it would
        make a settled selection look like a decision."""
        best = _fake(200.0)
        near = _fake(600.0)
        far = _fake(5_000.0)

        kept = span_corridor._drop_implausible([best, near, far])

        assert best in kept and near in kept
        assert far not in kept

    def test_the_only_corridor_found_is_never_dropped(self):
        far = _fake(50_000.0)
        assert span_corridor._drop_implausible([far]) == [far]

    def test_the_long_way_round_is_still_offered_as_an_alternative(
            self, synthetic):
        net = synthetic(ADJACENT)
        choice = self.build(net)

        assert len(choice.candidates) >= 2
        assert any(net.link_id("LOOP") in c.link_ids
                   for c in choice.candidates[1:])

    def test_a_handle_exactly_on_the_shared_junction(self, synthetic):
        """One end contributes no road; the corridor is the other half only."""
        net = synthetic(ADJACENT)
        choice = span_corridor.select(
            net.snapshot_id, [opt(net, "SEG_A", 1.0)], [opt(net, "SEG_B", 0.5)])

        assert choice.found
        assert choice.chosen.length_m == pytest.approx(100.0, abs=1e-6)
        assert len(choice.chosen.steps) == 1


class TestTheSearchIsBounded:
    """A corridor between two nearby points must not cost the whole country."""

    def test_a_corridor_beyond_the_box_is_still_found(self, synthetic):
        """The bound is a performance measure, so it must never lose a
        corridor: a search that finds nothing inside the box is retried
        without it."""
        net = synthetic(ADJACENT)
        # Force the bounded pass to find nothing by asking for a corridor that
        # only exists via the loop, then confirm one is returned anyway.
        choice = span_corridor.select(
            net.snapshot_id, [opt(net, "SEG_A", 0.5)], [opt(net, "LOOP", 0.5)])

        assert choice.found

    def test_the_envelope_grows_with_the_span(self, synthetic):
        net = synthetic(ADJACENT)
        links = span_corridor._links(
            net.snapshot_id, [net.link_id("SEG_A"), net.link_id("SEG_B")])
        minx, miny, maxx, maxy = span_corridor._envelope(
            links[net.link_id("SEG_A")], links[net.link_id("SEG_B")])

        # Both links sit inside, with the flat margin around them.
        assert minx <= -span_corridor.SEARCH_MARGIN_M
        assert maxx >= 400.0 + span_corridor.SEARCH_MARGIN_M
        assert miny < 0 < maxy


class TestAmbiguityIsSurfaced:
    """Equal evidence and equal length is a question, not a tie to break."""

    def build(self, net):
        return span_corridor.select(
            net.snapshot_id,
            [opt(net, "STUB_A", 0.5)], [opt(net, "STUB_B", 0.5)])

    def test_two_equal_corridors_are_ambiguous(self, synthetic):
        net = synthetic(DIAMOND)
        choice = self.build(net)

        assert choice.found
        assert choice.ambiguous
        assert choice.ambiguity_reason is not None
        assert "equally well evidenced" in choice.ambiguity_reason

    def test_both_ways_round_are_offered(self, synthetic):
        net = synthetic(DIAMOND)
        choice = self.build(net)

        north = net.link_id("NORTH")
        south = net.link_id("SOUTH")
        offered = {tuple(c.link_ids) for c in choice.candidates}
        assert any(north in links for links in offered)
        assert any(south in links for links in offered)

    def test_the_two_are_genuinely_different_roads(self, synthetic):
        net = synthetic(DIAMOND)
        choice = self.build(net)

        assert choice.candidates[0].link_ids != choice.candidates[1].link_ids
        assert choice.candidates[0].length_m == pytest.approx(
            choice.candidates[1].length_m, abs=1e-6)

    def test_a_decisively_shorter_rival_is_not_ambiguous(self, synthetic):
        """Same evidence but three times the road is not a close call."""
        net = synthetic(CORNER)
        a = child_at(net, "CHURCH_W", 50, 0)
        b = child_at(net, "CHURCH_N", 300, 200)
        choice = span_corridor.select(
            net.snapshot_id, [HandleOption(a, 0.5)], [HandleOption(b, 0.5)])

        assert not choice.ambiguous


class TestEquivalentHostsReachTheSearch:
    """A crossroads handle must not have its road chosen for it by the snap."""

    def test_a_junction_click_offers_every_host(self, synthetic):
        net = synthetic(CORNER)
        options = handle(net, 100.0, 0.0)

        # Church Street and Short Cut both pass through (100, 0).
        assert len(options) >= 2

    def test_candidates_are_generated_from_each_host(self, synthetic):
        net = synthetic(CORNER)
        a_options = handle(net, 100.0, 0.0)
        b = child_at(net, "CHURCH_N", 300, 200)

        choice = span_corridor.select(
            net.snapshot_id, a_options, [HandleOption(b, 0.5)])

        assert choice.found
        # Whichever host wins, the corridor must reach B.
        assert choice.chosen.steps[-1].link_id == b


class TestDeterminism:
    """Same request, same corridors, same order - on any database."""

    def test_repeated_selection_is_identical(self, synthetic):
        net = synthetic(CORNER)
        a = child_at(net, "CHURCH_W", 50, 0)
        b = child_at(net, "CHURCH_N", 300, 200)

        def run():
            c = span_corridor.select(net.snapshot_id, [HandleOption(a, 0.5)],
                                     [HandleOption(b, 0.5)])
            return [(x.candidate_id, round(x.length_m, 6)) for x in c.candidates]

        assert run() == run()

    def test_candidate_ids_are_built_from_publisher_ids(self, synthetic):
        """Never from `link_id`, which the noding pass hands out in ingest
        order - see stableid.py."""
        net = synthetic(CORNER)
        a = child_at(net, "CHURCH_W", 50, 0)
        b = child_at(net, "CHURCH_N", 300, 200)
        choice = span_corridor.select(
            net.snapshot_id, [HandleOption(a, 0.5)], [HandleOption(b, 0.5)])

        key = span_corridor.candidate_key(choice.chosen.steps)
        assert key == choice.chosen.candidate_id

    def test_the_same_corridor_found_twice_is_one_candidate(self, synthetic):
        """The shortest and same-name searches converge on one corridor here.
        That is agreement, not two options, and presenting it twice would make
        a settled choice look like a tie."""
        net = synthetic(STRAIGHT)
        choice = span_corridor.select(
            net.snapshot_id, [opt(net, "LONG", 0.2)], [opt(net, "LONG", 0.6)])

        ids = [c.candidate_id for c in choice.candidates]
        assert len(ids) == len(set(ids))


class TestGuards:

    def test_a_handle_with_no_hosts_is_refused(self, synthetic):
        net = synthetic(STRAIGHT)
        with pytest.raises(ValueError):
            span_corridor.select(net.snapshot_id, [], [opt(net, "LONG", 0.5)])

    def test_an_unknown_profile_is_refused(self, synthetic):
        net = synthetic(STRAIGHT)
        with pytest.raises(ValueError):
            span_corridor.select(
                net.snapshot_id, [opt(net, "LONG", 0.2)],
                [opt(net, "LONG", 0.6)], profile="hovercraft")

    def test_an_unknown_link_is_refused(self, synthetic):
        net = synthetic(STRAIGHT)
        with pytest.raises(KeyError):
            span_corridor.select(
                net.snapshot_id, [HandleOption(999_999, 0.2)],
                [opt(net, "LONG", 0.6)])

    def test_two_handles_at_one_measure_produce_no_corridor(self, synthetic):
        net = synthetic(STRAIGHT)
        choice = span_corridor.select(
            net.snapshot_id, [opt(net, "LONG", 0.5)], [opt(net, "LONG", 0.5)])

        assert not choice.found
        assert choice.candidates == []


class TestTheApiShape:

    def test_a_candidate_serialises_with_its_evidence(self, synthetic):
        net = synthetic(CORNER)
        a = child_at(net, "CHURCH_W", 50, 0)
        b = child_at(net, "CHURCH_N", 300, 200)
        choice = span_corridor.select(
            net.snapshot_id, [HandleOption(a, 0.5)], [HandleOption(b, 0.5)])

        payload = span_corridor.as_dict(choice)

        assert payload["found"] is True
        assert payload["corridor"]["roads"] == "Church Street"
        assert payload["corridor"]["evidence"]["roadNameContinuous"] is True
        assert payload["corridor"]["evidence"]["roadChanges"] == 0
        assert payload["corridorModelVersion"] == \
            span_corridor.CORRIDOR_MODEL_VERSION
        assert len(payload["candidates"]) >= 2


def _fake(length_m: float) -> span_corridor.SpanCandidate:
    """A candidate with only the field the plausibility filter reads."""
    step = span_corridor.SpanStep(
        link_id=1, amds_id=f"X{length_m}", road_name=None,
        route_designation=None, traversal="forward", from_fraction=0.0,
        to_fraction=1.0, length_m=length_m)
    return span_corridor.SpanCandidate(
        candidate_id=span_corridor.candidate_key([step]), steps=[step],
        length_m=length_m, origin="shortest")
