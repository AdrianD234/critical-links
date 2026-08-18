"""What `span_corridor` relies on `corridor` to mean.

This branch deliberately does NOT reimplement the continuity hierarchy. It
imports `corridor.Continuity` and `corridor.HEADING_TOLERANCE_DEG` read-only,
so there is one definition of what counts as a road carrying on and one place
where the argument for the ordering is written down.

The cost of that choice is a silent coupling. If a later change reorders
`Continuity.rank`, renames a field, or moves the heading tolerance, every
two-point corridor in this feature changes meaning - and nothing here would
fail, because the import still resolves and the booleans are still booleans.
A corridor would simply start preferring a different road.

So the contract is asserted rather than assumed. These tests are the thing that
turns "corridor.py was not edited in this merge" from an observation into a
guarantee that survives the next one.

If any of these fail, that is not necessarily a bug upstream: it may be a
deliberate improvement. The required response is to adapt `span_corridor`
deliberately and record the before/after meaning - not to loosen the test, and
not to copy the hierarchy into a second implementation.
"""

from __future__ import annotations

import random

import pytest

from nzcl import corridor, span_corridor

from conftest import requires_db


class TestTheContinuityTypeStillHasTheFieldsWeRead:

    def test_every_field_span_corridor_sets_exists(self):
        c = corridor.Continuity(route_designation_match=True,
                                road_name_match=True)

        assert c.route_designation_match is True
        assert c.road_name_match is True

    def test_the_fields_we_leave_unevidenced_still_default_to_false(self):
        """`span_corridor` evidences designation and name only. The rest must
        default to absent rather than to true, or a corridor would inherit
        evidence nobody produced."""
        c = corridor.Continuity()

        assert c.route_designation_match is False
        assert c.road_name_match is False
        assert c.state_highway_continues is False
        assert c.road_class_match is False
        assert c.degree_two is False
        assert c.heading_continuous is False
        assert c.turn_angle_deg is None

    def test_the_codes_we_surface_are_still_spelled_the_same(self):
        """These strings reach the API as `evidence.codes`, so a rename
        upstream is a client-visible change here."""
        c = corridor.Continuity(route_designation_match=True,
                                road_name_match=True)

        assert set(c.codes) == {"ROUTE_DESIGNATION_CONTINUES",
                                "ROAD_NAME_CONTINUES"}


class TestTheRankingHierarchyStillMeansWhatWeAssume:
    """Order matters more than values: this is a lexicographic comparison."""

    def test_rank_is_six_tiers_in_the_expected_order(self):
        designation = corridor.Continuity(route_designation_match=True)
        name = corridor.Continuity(road_name_match=True)
        degree_two = corridor.Continuity(degree_two=True)
        highway = corridor.Continuity(state_highway_continues=True)
        road_class = corridor.Continuity(road_class_match=True)
        heading = corridor.Continuity(heading_continuous=True)

        assert len(designation.rank) == 6
        assert designation.rank == (1, 0, 0, 0, 0, 0)
        assert name.rank == (0, 1, 0, 0, 0, 0)
        assert degree_two.rank == (0, 0, 1, 0, 0, 0)
        assert highway.rank == (0, 0, 0, 1, 0, 0)
        assert road_class.rank == (0, 0, 0, 0, 1, 0)
        assert heading.rank == (0, 0, 0, 0, 0, 1)

    def test_designation_still_outranks_name(self):
        assert corridor.Continuity(route_designation_match=True).rank > \
               corridor.Continuity(road_name_match=True).rank

    def test_name_still_outranks_heading(self):
        assert corridor.Continuity(road_name_match=True).rank > \
               corridor.Continuity(heading_continuous=True).rank

    def test_the_heading_tolerance_is_still_a_usable_angle(self):
        assert isinstance(corridor.HEADING_TOLERANCE_DEG, (int, float))
        assert 0 < corridor.HEADING_TOLERANCE_DEG < 180


class TestOurOwnRankKeyOrder:
    """The span ranking is stated in the brief's order and pinned here.

    Smaller is better. Designation, then name, then heading, then road
    changes, then length, then a stable identity tie-break.
    """

    def _candidate(self, *, designation: bool, name: bool, changes: int,
                   length: float) -> span_corridor.SpanCandidate:
        steps = [
            span_corridor.SpanStep(
                link_id=i, amds_id=f"L{i}", road_name="A" if name else f"R{i}",
                route_designation="SH1" if designation else None,
                traversal="forward", from_fraction=0.0, to_fraction=1.0,
                length_m=length / 2)
            for i in (1, 2)
        ]
        c = span_corridor._candidate(steps, "shortest")
        # Override the joints so each tier can be isolated.
        c.joints = [corridor.Continuity(
            route_designation_match=designation, road_name_match=name)]
        c.length_m = length
        if changes:
            c.joints = [corridor.Continuity()] * changes
        return c

    def test_designation_beats_a_shorter_corridor(self):
        evidenced = self._candidate(designation=True, name=True, changes=0,
                                    length=10_000.0)
        short = self._candidate(designation=False, name=False, changes=1,
                                length=100.0)

        assert evidenced.rank_key < short.rank_key

    def test_length_only_decides_once_the_evidence_ties(self):
        long_one = self._candidate(designation=True, name=True, changes=0,
                                   length=900.0)
        short_one = self._candidate(designation=True, name=True, changes=0,
                                    length=100.0)

        assert short_one.rank_key < long_one.rank_key

    def test_the_last_tier_is_a_stable_identity_not_a_row_order(self):
        c = self._candidate(designation=True, name=True, changes=0,
                            length=100.0)

        assert c.rank_key[-1] == c.candidate_id
        assert c.candidate_id == span_corridor.candidate_key(c.steps)


#: The corner fixture from the corridor suite, used here for determinism.
CORNER = [
    {"id": "CHURCH_W", "pts": [(0, 0), (300, 0)], "road_name": "Church Street"},
    {"id": "CHURCH_N", "pts": [(300, 0), (300, 300)],
     "road_name": "Church Street"},
    {"id": "CUT", "pts": [(100, 0), (300, 100)], "road_name": "Short Cut"},
]


@requires_db
class TestRowOrderDeterminism:
    """Shuffling the input must not move the corridor.

    `stableid.py` records what this costs when it goes wrong: shuffling a
    nine-link fixture flipped a corridor choice on three seeds out of eight,
    purely because ids are handed out in ingest order. The same class of defect
    is possible here, so it is tested the same way - by loading the same
    network under several orderings and comparing the ANSWER, not the ids.
    """

    @pytest.mark.parametrize("seed", [1, 5, 11, 23, 97])
    def test_the_same_corridor_is_chosen_whatever_order_the_links_load_in(
            self, synthetic, seed):
        from nzcl import db

        def chosen_for(spec):
            net = synthetic(spec)
            a = self._child(net, "CHURCH_W", 50, 0)
            b = self._child(net, "CHURCH_N", 300, 200)
            choice = span_corridor.select(
                net.snapshot_id, [span_corridor.HandleOption(a, 0.5)],
                [span_corridor.HandleOption(b, 0.5)])
            return choice.chosen

        baseline = chosen_for(CORNER)

        shuffled = list(CORNER)
        random.Random(seed).shuffle(shuffled)
        other = chosen_for(shuffled)

        # The candidate id is hashed from AMDS ids and positions, so it is
        # comparable across two loads that assigned different link_ids.
        assert other.candidate_id == baseline.candidate_id
        assert other.length_m == pytest.approx(baseline.length_m, abs=1e-6)
        assert [s.road_name for s in other.steps] == \
               [s.road_name for s in baseline.steps]

    @staticmethod
    def _child(net, prefix, x, y):
        from nzcl import db
        row = db.query_one(
            "SELECT link_id FROM links WHERE snapshot_id=%s "
            "  AND closure_group_id=%s "
            "  AND ST_DWithin(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), 0.001) "
            " ORDER BY link_id LIMIT 1", (net.snapshot_id, prefix, x, y))
        assert row is not None
        return int(row["link_id"])


@requires_db
class TestTheKnownCorridorHasNotMoved:
    """The corner case, asserted again here against the merged tree.

    Duplicated from the corridor suite on purpose: this file is what a reviewer
    reads to see whether the merge changed meaning, and a cross-reference to
    another file is weaker evidence than the assertion itself.
    """

    def test_the_named_road_still_wins_over_the_short_cut(self, synthetic):
        net = synthetic(CORNER)
        a = TestRowOrderDeterminism._child(net, "CHURCH_W", 50, 0)
        b = TestRowOrderDeterminism._child(net, "CHURCH_N", 300, 200)

        choice = span_corridor.select(
            net.snapshot_id, [span_corridor.HandleOption(a, 0.5)],
            [span_corridor.HandleOption(b, 0.5)])

        assert {s.road_name for s in choice.chosen.steps} == {"Church Street"}
        assert choice.chosen.length_m == pytest.approx(450.0, abs=1e-6)
        assert choice.chosen.road_changes == 0
