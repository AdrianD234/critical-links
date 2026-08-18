"""A canonical node exists where evidence says so, and nowhere else.

These tests replace an assumption that was measured and found false: that the
classifier could decide, unsupervised, whether two crossing centrelines are a
junction. It cannot. 32 of 350 of its AT_GRADE crossings are not junctions at
all, five of those from its only HIGH-confidence rule, while 11 of 25 sampled
DUPLICATE_GEOMETRY withdrawals are junctions a reviewer can see. There is no
threshold between those two failures. See the audit README sections 13 and 14.

So the property under test here is a narrow one, and it is the whole pivot:

    the canonical graph creates a shared node at an interior crossing if and
    only if a live, non-conflicting, evidence-backed override says to.

Every test below is one way of getting that wrong.
"""

from __future__ import annotations

import datetime as dt

import pytest

from nzcl import crossings as crossings_mod
from nzcl import overrides as overrides_mod
from nzcl.overrides import (Decision, InvalidOverride, Override,
                            OverrideConflict, OverrideIndex)
from nzcl.topology import (CANONICAL_CROSSING_POLICY, assign_nodes,
                           audit_no_invented_movements, split_at_junctions)
from test_topology import components, src

TODAY = dt.date(2026, 8, 18)


def override(a="GREENDALE", b="CLINTONS", *, x=0.0, y=0.0,
             decision=crossings_mod.AT_GRADE, **kw):
    """A valid override, so each test can vary exactly one thing."""
    kw.setdefault("evidence_kind", "MANUAL_AERIAL_REVIEW")
    kw.setdefault("evidence_ref", "holdout3/T001")
    kw.setdefault("reviewer", "a.reviewer")
    kw.setdefault("decided_on", TODAY)
    return Override(source_a=a, source_b=b, x=x, y=y, decision=decision, **kw)


#: The Darfield case, reduced: two through roads crossing at (0,0), neither
#: terminating. This is the crossing that costs 3,028.9 m of replacement path
#: when it is missing, and it is exactly the case the classifier got right and
#: was still not allowed to act on.
CROSSROADS = [
    src("GREENDALE", [(-500, 0), (500, 0)], model_asset_type=1, oneway=2,
        rca_code=74, road_name="Greendale Road"),
    src("CLINTONS", [(0, -500), (0, 500)], model_asset_type=1, oneway=2,
        rca_code=74, road_name="Clintons Road"),
]


def node_at_origin(links) -> int | None:
    pairs, coords = assign_nodes(links)
    for nid, (x, y) in enumerate(coords):
        if abs(x) < 0.01 and abs(y) < 0.01:
            return nid
    return None


class TestNothingIsNodedWithoutEvidence:
    """The default has to be 'disconnected', or none of the rest matters."""

    def test_the_canonical_policy_is_the_default(self):
        import inspect
        sig = inspect.signature(split_at_junctions)
        assert sig.parameters["crossing_policy"].default == \
            CANONICAL_CROSSING_POLICY == "evidence"

    def test_a_crossroads_the_classifier_calls_at_grade_is_not_noded(self):
        """The single most important assertion in this file.

        The classifier is RIGHT about this crossing - it is a real rural
        crossroads, and it is the one that proved the whole defect. It is
        still not noded, because being right on a case someone checked is not
        the same as being trustworthy on 4,914 nobody did.
        """
        res = split_at_junctions(CROSSROADS)
        assert len(res.crossings) == 1
        assert res.crossings[0].disposition == crossings_mod.AT_GRADE
        assert res.crossing_cuts == 0
        assert node_at_origin(res.links) is None
        assert components(res.links) == 2

    def test_an_empty_override_index_changes_nothing(self):
        res = split_at_junctions(CROSSROADS, overrides=OverrideIndex.build([]))
        assert res.crossing_cuts == 0
        assert components(res.links) == 2

    def test_the_classifier_verdict_is_still_recorded(self):
        """It is the review queue's input. Suppressing it would be worse."""
        res = split_at_junctions(CROSSROADS)
        cls = res.crossings[0].classification
        assert cls.disposition == crossings_mod.AT_GRADE
        assert cls.reason == "ORDINARY_CROSSROADS"
        assert cls.confidence == "MEDIUM"


class TestAConfirmedOverrideCreatesTheExpectedNode:
    """Required test 7."""

    def test_it_nodes_the_crossing(self):
        idx = OverrideIndex.build([override()], on=TODAY)
        res = split_at_junctions(CROSSROADS, overrides=idx)
        assert res.crossing_cuts == 1
        assert res.override_cuts == 1
        assert node_at_origin(res.links) is not None
        assert components(res.links) == 1

    def test_the_node_has_all_four_arms(self):
        idx = OverrideIndex.build([override()], on=TODAY)
        res = split_at_junctions(CROSSROADS, overrides=idx)
        nid = node_at_origin(res.links)
        pairs, _ = assign_nodes(res.links)
        degree = sum((a == nid) + (b == nid) for a, b in pairs)
        assert degree == 4

    def test_the_sides_may_be_named_the_other_way_round(self):
        """Which side is `source_a` is not stable between ingests.

        The links table is read without an ORDER BY, so the ordered pair
        silently lost 567 of 22,062 crossings when it was used as a key. An
        override written when the sides came out one way must still apply when
        they come out the other.
        """
        idx = OverrideIndex.build([override("CLINTONS", "GREENDALE")], on=TODAY)
        res = split_at_junctions(CROSSROADS, overrides=idx)
        assert res.crossing_cuts == 1

    def test_an_override_a_few_metres_off_still_matches(self):
        """The detector re-derives the point from re-merged geometry."""
        idx = OverrideIndex.build([override(x=2.0, y=-1.5)], on=TODAY)
        assert split_at_junctions(CROSSROADS, overrides=idx).crossing_cuts == 1

    def test_an_override_far_away_does_not(self):
        idx = OverrideIndex.build([override(x=40.0)], on=TODAY)
        assert split_at_junctions(CROSSROADS, overrides=idx).crossing_cuts == 0

    def test_an_override_for_a_different_pair_does_not(self):
        idx = OverrideIndex.build([override("GREENDALE", "SOMEWHERE_ELSE")],
                                  on=TODAY)
        assert split_at_junctions(CROSSROADS, overrides=idx).crossing_cuts == 0

    def test_a_retired_override_does_not(self):
        """Withdrawal is a new fact, not an erasure."""
        idx = OverrideIndex.build(
            [override(decided_on=dt.date(2026, 6, 1),
                      retired_on=dt.date(2026, 8, 1))], on=TODAY)
        assert len(idx) == 0 and idx.retired == 1
        assert split_at_junctions(CROSSROADS, overrides=idx).crossing_cuts == 0

    def test_a_grade_separated_override_does_not_node(self):
        """An override may assert separation. It must not connect anything."""
        idx = OverrideIndex.build(
            [override(decision=crossings_mod.GRADE_SEPARATED)], on=TODAY)
        res = split_at_junctions(CROSSROADS, overrides=idx)
        assert res.crossing_cuts == 0
        assert components(res.links) == 2

    def test_the_audit_does_not_accuse_the_override(self):
        idx = OverrideIndex.build([override()], on=TODAY)
        res = split_at_junctions(CROSSROADS, overrides=idx)
        assert audit_no_invented_movements(res) == []

    def test_the_audit_still_catches_an_unbacked_connection(self):
        """The safety net must not have been disabled by the new policy."""
        res = split_at_junctions(CROSSROADS)
        assert res.crossing_cuts == 0
        # Fabricate the connection the override would have made, without one.
        idx = OverrideIndex.build([override()], on=TODAY)
        noded = split_at_junctions(CROSSROADS, overrides=idx)
        res.links = noded.links          # links say connected...
        # ...and no decision authorised it
        assert not any(d.nodes for d in res.crossing_decisions)
        assert audit_no_invented_movements(res)


class TestConflictingOverridesFailClosed:
    """Required test 8."""

    def two(self):
        return [
            override(reviewer="first.reviewer", evidence_ref="review/2026-07",
                     decision=crossings_mod.AT_GRADE,
                     decided_on=dt.date(2026, 7, 1)),
            override(reviewer="second.reviewer", evidence_ref="review/2026-08",
                     decision=crossings_mod.GRADE_SEPARATED,
                     decided_on=dt.date(2026, 8, 1)),
        ]

    def test_the_crossing_stays_disconnected(self):
        idx = OverrideIndex.build(self.two(), on=TODAY)
        res = split_at_junctions(CROSSROADS, overrides=idx)
        assert res.crossing_cuts == 0
        assert res.override_conflicts == 1
        assert components(res.links) == 2

    def test_the_later_override_does_not_win(self):
        """Every tempting tie-break resolves a disagreement between two people
        by a rule neither of them agreed to."""
        idx = OverrideIndex.build(self.two(), on=TODAY)
        d = idx.decide("GREENDALE", "CLINTONS", 0.0, 0.0)
        assert d.disposition is None and d.conflict is True

    def test_at_grade_does_not_win_either(self):
        idx = OverrideIndex.build(list(reversed(self.two())), on=TODAY)
        assert idx.decide("GREENDALE", "CLINTONS", 0.0, 0.0).nodes is False

    def test_the_conflict_names_both_reviewers(self):
        idx = OverrideIndex.build(self.two(), on=TODAY)
        why = idx.decide("GREENDALE", "CLINTONS", 0.0, 0.0).reason
        assert "first.reviewer" in why and "second.reviewer" in why

    def test_two_overrides_that_agree_are_not_a_conflict(self):
        idx = OverrideIndex.build(
            [override(reviewer="a"), override(reviewer="b")], on=TODAY)
        d = idx.decide("GREENDALE", "CLINTONS", 0.0, 0.0)
        assert d.conflict is False and d.nodes is True

    def test_retiring_one_side_resolves_it(self):
        a, b = self.two()
        idx = OverrideIndex.build(
            [a, Override(**{**b.__dict__, "retired_on": dt.date(2026, 8, 10)})],
            on=TODAY)
        assert idx.decide("GREENDALE", "CLINTONS", 0.0, 0.0).nodes is True

    def test_strict_mode_raises_for_tooling(self):
        idx = OverrideIndex.build(self.two(), on=TODAY)
        with pytest.raises(OverrideConflict):
            idx.decide_strict("GREENDALE", "CLINTONS", 0.0, 0.0)

    def test_one_conflict_does_not_stop_the_other_crossings(self):
        """A national ingest should lose the crossing two people disagree
        about, not the other twenty-two thousand."""
        sources = CROSSROADS + [
            src("NORTH", [(-500, 300), (500, 300)], model_asset_type=1,
                oneway=2, rca_code=74, road_name="North Road"),
            src("EAST", [(300, -500), (300, 500)], model_asset_type=1,
                oneway=2, rca_code=74, road_name="East Road"),
        ]
        idx = OverrideIndex.build(
            self.two() + [override("NORTH", "EAST", x=300.0, y=300.0)],
            on=TODAY)
        res = split_at_junctions(sources, overrides=idx)
        assert res.override_conflicts == 1
        assert res.override_cuts == 1


class TestAnUnresolvedMotorwayCrossingNeverBecomesCanonical:
    """Required test 6.

    `MOTORWAY_CARRIAGEWAY` is the rule the previous holdout found at 2 of 8
    and this one found at 11 of 16 called at-grade. It is UNRESOLVED, and the
    point of this test is that being probably-wrong in the cautious direction
    still does not let it into the canonical graph by any route.
    """

    MOTORWAY = [
        src("SH1", [(-500, 0), (500, 0)], model_asset_type=1, rca_code=1,
            oneway=1, road_name="State Highway 1"),
        src("LOCAL", [(0, -500), (0, 500)], model_asset_type=1, rca_code=74,
            oneway=2, road_name="Local Road"),
    ]

    def test_the_classifier_leaves_it_unresolved(self):
        res = split_at_junctions(self.MOTORWAY)
        assert res.crossings[0].disposition == crossings_mod.UNRESOLVED

    def test_it_is_not_noded_without_an_override(self):
        res = split_at_junctions(self.MOTORWAY)
        assert res.crossing_cuts == 0
        assert components(res.links) == 2

    def test_it_is_not_noded_by_asking_for_the_possible_policy(self):
        """The policy that would connect it now requires `research=True`,
        so no ingest or API path can reach it by threading a string."""
        with pytest.raises(ValueError, match="rejected"):
            split_at_junctions(self.MOTORWAY, crossing_policy="possible")

    def test_nor_by_asking_for_the_confirmed_policy(self):
        with pytest.raises(ValueError, match="rejected"):
            split_at_junctions(CROSSROADS, crossing_policy="confirmed")

    def test_it_IS_noded_when_a_reviewer_says_it_is_a_junction(self):
        """The mechanism has to be able to say yes, or it is just a refusal.

        Eleven of sixteen of these were called at-grade by reviewers. This is
        how that finding gets into the graph: one at a time, with a name on it.
        """
        idx = OverrideIndex.build(
            [override("SH1", "LOCAL", evidence_kind="MANUAL_AERIAL_REVIEW",
                      evidence_ref="holdout3/T014", reviewer="reviewer.a")],
            on=TODAY)
        res = split_at_junctions(self.MOTORWAY, overrides=idx)
        assert res.override_cuts == 1
        assert components(res.links) == 1


class TestAnOverrideCannotOverrideRepresentability:
    """`safe_to_node` is not a verdict about the ground; it is about whether
    one shared node can express what is there. A mixed place cannot be, and an
    override saying AT_GRADE does not change that."""

    TANGENTIAL = [
        src("A", [(-500, 0), (500, 0)], model_asset_type=1, oneway=2,
            rca_code=74, road_name="A Road"),
        src("B", [(-500, -10), (500, 10)], model_asset_type=1, oneway=2,
            rca_code=74, road_name="B Road"),   # ~1 degree
    ]

    def test_a_tangential_graze_is_not_noded_even_with_an_override(self):
        res0 = split_at_junctions(self.TANGENTIAL)
        assert res0.crossings, "fixture must actually cross"
        x = res0.crossings[0]
        idx = OverrideIndex.build([override("A", "B", x=x.x, y=x.y)], on=TODAY)
        res = split_at_junctions(self.TANGENTIAL, overrides=idx)
        assert res.crossing_cuts == 0
        assert res.override_vetoed and "not representable" in res.override_vetoed[0]

    def test_the_veto_is_loud(self):
        res0 = split_at_junctions(self.TANGENTIAL)
        x = res0.crossings[0]
        idx = OverrideIndex.build([override("A", "B", x=x.x, y=x.y)], on=TODAY)
        res = split_at_junctions(self.TANGENTIAL, overrides=idx)
        assert len(res.override_vetoed) == 1


class TestAnOverrideMustCarryItsEvidence:
    """Refused at construction, not at use. A bad row that only fails when a
    graph is built is a bad row that is already committed."""

    @pytest.mark.parametrize("bad, match", [
        ({"reviewer": "   "}, "reviewer is blank"),
        ({"evidence_ref": ""}, "evidence_ref is blank"),
        ({"evidence_kind": "SEEMED_RIGHT"}, "evidence_kind must be"),
        ({"decision": "UNRESOLVED"}, "decision must be"),
        ({"decision": "MAYBE"}, "decision must be"),
        ({"decided_on": "2026-08-18"}, "decided_on must be a date"),
    ])
    def test_it_is_refused(self, bad, match):
        with pytest.raises(InvalidOverride, match=match):
            override(**bad)

    def test_a_road_crossing_itself_is_refused(self):
        with pytest.raises(InvalidOverride, match="crossing itself"):
            override("SAME", "SAME")

    def test_retiring_before_deciding_is_refused(self):
        with pytest.raises(InvalidOverride, match="before decided_on"):
            override(retired_on=dt.date(2020, 1, 1))

    def test_recording_unresolved_is_refused_because_it_says_nothing(self):
        """UNRESOLVED is what a crossing already is without an override."""
        with pytest.raises(InvalidOverride, match="says nothing"):
            override(decision="UNRESOLVED")


class TestTheResearchPoliciesStayReachableAndLabelled:
    """The evidence that rejected the strategy is only reproducible while the
    strategy still runs. It just cannot be reached by accident."""

    def test_research_true_unlocks_the_old_behaviour(self):
        res = split_at_junctions(CROSSROADS, crossing_policy="confirmed",
                                 research=True)
        assert res.crossing_cuts == 1
        assert res.override_cuts == 0

    def test_the_error_says_why_rather_than_just_no(self):
        with pytest.raises(ValueError) as e:
            split_at_junctions(CROSSROADS, crossing_policy="confirmed")
        msg = str(e.value)
        assert "32 of 350" in msg and "PIVOT.md" in msg

    def test_overrides_with_a_research_policy_are_refused(self):
        """Silently discarding reviewed evidence is noticed late."""
        with pytest.raises(ValueError, match="ignores them"):
            split_at_junctions(CROSSROADS, crossing_policy="confirmed",
                               research=True,
                               overrides=OverrideIndex.build([override()]))

    def test_an_unknown_policy_is_still_refused(self):
        with pytest.raises(ValueError, match="must be one of"):
            split_at_junctions(CROSSROADS, crossing_policy="whatever")


class TestTheSummaryCountsWhatMatters:
    def test_it_separates_decided_from_undecided_from_conflicting(self):
        idx = OverrideIndex.build([
            override("A", "B"),
            override("C", "D", decision=crossings_mod.GRADE_SEPARATED),
            override("E", "F", reviewer="x", decision=crossings_mod.AT_GRADE),
            override("E", "F", reviewer="y",
                     decision=crossings_mod.GRADE_SEPARATED),
        ], on=TODAY)
        decisions = [idx.decide("A", "B", 0, 0), idx.decide("C", "D", 0, 0),
                     idx.decide("E", "F", 0, 0), idx.decide("G", "H", 0, 0)]
        s = overrides_mod.summarise(decisions)
        assert s["decidedAtGrade"] == 1
        assert s["decidedGradeSeparated"] == 1
        assert s["conflicts"] == 1
        assert s["undecided"] == 1

    def test_a_decision_with_no_evidence_says_so(self):
        d = OverrideIndex.build([]).decide("A", "B", 0, 0)
        assert d == Decision(None, reason=d.reason)
        assert "no evidence-backed override" in d.reason
