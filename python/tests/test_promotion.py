"""The promotion gate has to be able to say no.

The defect this file exists for: `holdout-result.json` recorded four numbers
under `promotionGate` and tested one of them, then reported `met: true` about a
holdout whose lower bound was 92.8% against an agreed threshold of 97%. The
error was mechanical and it pointed at declaring success, which is the
direction to watch.

So every condition is pinned individually, and the one thing checked hardest is
that each of them can FAIL ON ITS OWN. A gate whose conditions cannot each veto
is a gate with decorative conditions.
"""

from __future__ import annotations

import pytest

from nzcl.promotion import (REQUIRED_LOWER_BOUND, evaluate,
                            required_sample_size, wilson)


PASSING = dict(confirmed=200, contradicted=0, unreviewable=0,
               grade_separated_false_positives=0,
               not_a_junction_false_positives=0)


def names(result):
    return {c.name: c.met for c in result.conditions}


class TestEveryConditionCanVetoOnItsOwn:
    def test_the_baseline_passes(self):
        """Otherwise every test below passes for the wrong reason."""
        r = evaluate(**PASSING)
        assert r.met is True
        assert all(names(r).values())

    def test_one_grade_separated_false_positive_fails_it(self):
        r = evaluate(**{**PASSING, "confirmed": 199, "contradicted": 1,
                        "grade_separated_false_positives": 1})
        assert r.met is False
        assert names(r)["zeroConfirmedGradeSeparatedFalsePositives"] is False

    def test_one_not_a_junction_false_positive_fails_it(self):
        """The condition the old block did not have at all. All three AT_GRADE
        contradictions in the 248-card holdout were this kind, and the gate as
        written called that a pass."""
        r = evaluate(**{**PASSING, "confirmed": 199, "contradicted": 1,
                        "not_a_junction_false_positives": 1})
        assert r.met is False
        assert names(r)["zeroConfirmedNotAJunctionFalsePositives"] is False

    def test_a_bound_below_the_threshold_fails_it_with_no_false_positives(self):
        """The half that was recorded and never tested: a result can have zero
        false positives of either kind and still be too small to support the
        claim."""
        r = evaluate(confirmed=40, contradicted=0, unreviewable=0,
                     grade_separated_false_positives=0,
                     not_a_junction_false_positives=0)
        assert names(r)["zeroConfirmedGradeSeparatedFalsePositives"] is True
        assert names(r)["zeroConfirmedNotAJunctionFalsePositives"] is True
        assert names(r)["lowerBound95OnAtGradePrecision"] is False
        assert r.met is False


class TestUnreviewableCountsAgainstIt:
    def test_unreviewable_cards_lower_the_bound(self):
        clean = evaluate(**{**PASSING, "confirmed": 200})
        with_unclear = evaluate(**{**PASSING, "confirmed": 190,
                                   "unreviewable": 10})
        assert _bound(with_unclear) < _bound(clean)

    def test_a_run_that_only_passes_by_excluding_them_is_not_a_pass(self):
        """The exact shape of the 248-card result: 95.5% excluding
        unreviewable, 92.8% counting them. The gate reads the second."""
        r = evaluate(confirmed=189, contradicted=3, unreviewable=4,
                     grade_separated_false_positives=0,
                     not_a_junction_false_positives=3)
        observed = [c.observed for c in r.conditions
                    if c.name == "unreviewableCountedAsFailures"][0]
        assert observed["lowerBoundExcludingThem"] > 95.0
        assert observed["lowerBoundCountingThemAsFailures"] < 95.0
        assert _bound(r) == observed["lowerBoundCountingThemAsFailures"]
        assert names(r)["lowerBound95OnAtGradePrecision"] is False


class TestTheHoldoutThatWasReportedAsPassing:
    """The 248-card holdout, re-scored against the gate as agreed.

    Written as a test rather than a note because the number in the audit was
    wrong once already, and a paragraph cannot fail a build.
    """

    RESULT = dict(confirmed=189, contradicted=3, unreviewable=4,
                  grade_separated_false_positives=0,
                  not_a_junction_false_positives=3)

    def test_it_does_not_meet_the_gate(self):
        assert evaluate(**self.RESULT).met is False

    def test_the_condition_it_did_meet_is_still_recorded_as_met(self):
        """Honesty runs both ways. Zero grade-separated false positives is a
        real result and the widened gate must not erase it."""
        assert names(evaluate(**self.RESULT))[
            "zeroConfirmedGradeSeparatedFalsePositives"] is True

    def test_exactly_two_conditions_fail(self):
        r = evaluate(**self.RESULT)
        assert sorted(k for k, v in names(r).items() if not v) == [
            "lowerBound95OnAtGradePrecision",
            "zeroConfirmedNotAJunctionFalsePositives",
        ]

    def test_the_detail_says_not_met_in_words(self):
        assert "NOT MET" in evaluate(**self.RESULT).detail


class TestTheSampleSizeIsDecidableInAdvance:
    """`required_sample_size` is called BEFORE the draw, and the answer is
    committed before review starts. Enlarging a sample after seeing the bound
    come in short is the same class of error as tuning against a holdout: it
    turns the gate into something that can always eventually be passed."""

    #: Independently stated by the coordinator, and reproduced here so a change
    #: to the interval maths cannot pass unnoticed.
    EXPECTED = {0: 125, 1: 185, 2: 239, 3: 290, 4: 339}

    @pytest.mark.parametrize("failures,n", sorted(EXPECTED.items()))
    def test_the_published_table_is_reproduced(self, failures, n):
        assert required_sample_size(failures) == n

    @pytest.mark.parametrize("failures", sorted(EXPECTED))
    def test_one_card_short_of_it_does_not_clear_the_gate(self, failures):
        n = self.EXPECTED[failures]
        assert wilson(n - failures, n)[0] >= REQUIRED_LOWER_BOUND
        assert wilson(n - 1 - failures, n - 1)[0] < REQUIRED_LOWER_BOUND

    def test_a_declared_size_that_is_met_exactly_passes_the_gate(self):
        """The declaration is only worth making if it is sufficient."""
        n = required_sample_size(2)
        r = evaluate(confirmed=n - 2, contradicted=2, unreviewable=0,
                     grade_separated_false_positives=0,
                     not_a_junction_false_positives=0)
        assert names(r)["lowerBound95OnAtGradePrecision"] is True

    def test_more_failures_need_more_cards(self):
        sizes = [required_sample_size(f) for f in range(6)]
        assert sizes == sorted(sizes)
        assert len(set(sizes)) == len(sizes)


class TestTheSerialisedBlockCannotHideAFailure:
    def test_every_condition_appears_with_its_own_verdict(self):
        d = evaluate(**PASSING).as_dict()
        assert set(d["requiresAllOf"]) == {c["name"] for c in d["conditions"]}
        assert len(d["conditions"]) == 4
        for c in d["conditions"]:
            assert set(c) == {"name", "requirement", "observed", "met", "why"}

    def test_met_is_the_and_of_the_conditions(self):
        for kw in (PASSING,
                   {**PASSING, "not_a_junction_false_positives": 1,
                    "confirmed": 199, "contradicted": 1},
                   {"confirmed": 10, "contradicted": 0, "unreviewable": 0,
                    "grade_separated_false_positives": 0,
                    "not_a_junction_false_positives": 0}):
            d = evaluate(**kw).as_dict()
            assert d["met"] == all(c["met"] for c in d["conditions"])


def _bound(result) -> float:
    return [c.observed for c in result.conditions
            if c.name == "lowerBound95OnAtGradePrecision"][0]
