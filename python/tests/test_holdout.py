"""A short review must not be able to pass the gate.

The defect this file exists for has not happened yet, and that is the point of
writing it down before the third holdout is scored rather than after. The
loophole is arithmetic, not malice: 350 AT_GRADE cards are drawn, a reviewer
returns 340 verdicts, and a scorer that joins on what came back computes a
bound over n=340. The ten missing cards leave the DENOMINATOR instead of
counting as failures - and they are not a random ten, they are the ones that
were hard to read, which is where the errors are.

`promotion.evaluate` cannot catch it. It takes counts, and by then the loss
has happened. So every test here checks the same property from a different
angle: **the denominator is a property of the pack, never of the verdict
file.**
"""

from __future__ import annotations

import pytest

from nzcl.holdout import (ACCEPTS, DECLARED_AT_GRADE_N, Collated,
                          ReviewNotComplete, collate, normalise)
from nzcl.promotion import evaluate


def pack(n_at_grade: int = DECLARED_AT_GRADE_N, n_decoy: int = 90):
    """An answer key of the declared shape, with nothing else in it."""
    cards = [{"code": f"T{i:03d}", "disposition": "AT_GRADE",
              "reason": "ORDINARY_CROSSROADS", "cell": "urban"}
             for i in range(1, n_at_grade + 1)]
    cards += [{"code": f"D{i:03d}", "disposition": "GRADE_SEPARATED",
               "reason": "STRUCTURE_MAPPED", "cell": "gs_structure"}
              for i in range(1, n_decoy + 1)]
    return cards


def verdicts_for(cards, label="a", **overrides):
    out = [{"code": c["code"], "verdict": overrides.get(c["code"], label)}
           for c in cards]
    return [v for v in out if v["verdict"] is not None]


class TestACompleteReviewScoresNormally:
    """Otherwise every test below could pass for the wrong reason."""

    def test_every_card_gets_exactly_one_row(self):
        cards = pack()
        c = collate(cards, verdicts_for(cards))
        assert len(c.rows) == len(cards)
        assert {r["code"] for r in c.rows} == {x["code"] for x in cards}
        assert c.materialised == ()

    def test_counts_reach_the_gate_and_it_passes(self):
        cards = pack()
        c = collate(cards, verdicts_for(cards))
        counts = c.counts("AT_GRADE")
        assert counts["confirmed"] == DECLARED_AT_GRADE_N
        assert counts["contradicted"] == 0
        assert counts["unreviewable"] == 0
        assert evaluate(**counts).met is True

    def test_letters_and_long_forms_are_the_same_verdict(self):
        assert normalise("a") == normalise("at_grade") == "at_grade"
        assert normalise(" G ") == "grade_separated"
        assert normalise("maybe") is None
        assert normalise(None) is None


class TestAMissingVerdictCannotShrinkTheDenominator:
    """The headline loophole, from both ends."""

    def test_strict_refuses_to_score_at_all(self):
        cards = pack()
        short = [v for v in verdicts_for(cards)
                 if v["code"] not in {"T001", "T002", "T003"}]
        with pytest.raises(ReviewNotComplete) as e:
            collate(cards, short)
        assert e.value.missing == ("T001", "T002", "T003")

    def test_the_fallback_counts_them_as_failures_not_as_absences(self):
        cards = pack()
        short = [v for v in verdicts_for(cards)
                 if v["code"] not in {"T001", "T002", "T003"}]
        c = collate(cards, short, strict=False)
        counts = c.counts("AT_GRADE")
        n = counts["confirmed"] + counts["contradicted"] + counts["unreviewable"]
        assert n == DECLARED_AT_GRADE_N, "the denominator moved"
        assert counts["unreviewable"] == 3
        assert counts["confirmed"] == DECLARED_AT_GRADE_N - 3
        assert c.materialised == ("T001", "T002", "T003")

    def test_omitting_the_hard_ones_cannot_beat_answering_them_unclear(self):
        """The loophole is only worth using if skipping beats admitting.

        A reviewer who marks ten cards `u` and one who silently omits the same
        ten must land on the same numbers. If omission ever scores better, the
        incentive points the wrong way.
        """
        cards = pack()
        hard = {f"T{i:03d}" for i in range(1, 11)}
        admitted = collate(cards, [{"code": c["code"],
                                    "verdict": "u" if c["code"] in hard else "a"}
                                   for c in cards])
        omitted = collate(cards, [{"code": c["code"], "verdict": "a"}
                                  for c in cards if c["code"] not in hard],
                          strict=False)
        assert admitted.counts("AT_GRADE") == omitted.counts("AT_GRADE")

    def test_a_wholly_absent_review_is_all_failures_not_an_empty_pack(self):
        cards = pack()
        c = collate(cards, [], strict=False)
        counts = c.counts("AT_GRADE")
        assert counts["unreviewable"] == DECLARED_AT_GRADE_N
        assert counts["confirmed"] == 0
        assert evaluate(**counts).met is False


class TestMalformedInputFails:
    def test_a_duplicate_verdict_fails(self):
        cards = pack()
        v = verdicts_for(cards) + [{"code": "T007", "verdict": "g"}]
        with pytest.raises(ReviewNotComplete) as e:
            collate(cards, v)
        assert e.value.duplicated == ("T007",)

    def test_a_duplicate_is_unreviewed_under_the_fallback_not_first_wins(self):
        """Two answers is not one answer. It cannot resolve to the nicer one."""
        cards = pack()
        v = verdicts_for(cards) + [{"code": "T007", "verdict": "g"}]
        c = collate(cards, v, strict=False)
        row = next(r for r in c.rows if r["code"] == "T007")
        assert row["verdict"] == "unclear"
        assert c.counts("AT_GRADE")["unreviewable"] == 1

    def test_an_unknown_card_code_fails(self):
        cards = pack()
        v = verdicts_for(cards) + [{"code": "T999", "verdict": "a"}]
        with pytest.raises(ReviewNotComplete) as e:
            collate(cards, v)
        assert e.value.unknown == ("T999",)

    def test_an_unknown_card_code_fails_even_in_the_fallback(self):
        """It cannot be materialised into anything, and it means the verdict
        file is not describing this pack."""
        cards = pack()
        v = verdicts_for(cards) + [{"code": "T999", "verdict": "a"}]
        with pytest.raises(ReviewNotComplete):
            collate(cards, v, strict=False)

    def test_an_invalid_label_fails(self):
        cards = pack()
        v = [{"code": c["code"],
              "verdict": "probably at grade?" if c["code"] == "T042" else "a"}
             for c in cards]
        with pytest.raises(ReviewNotComplete) as e:
            collate(cards, v)
        assert any(x.startswith("T042=") for x in e.value.invalid)

    def test_an_invalid_label_is_a_failure_not_a_dropped_row(self):
        cards = pack()
        v = [{"code": c["code"],
              "verdict": "probably at grade?" if c["code"] == "T042" else "a"}
             for c in cards]
        c = collate(cards, v, strict=False)
        counts = c.counts("AT_GRADE")
        n = counts["confirmed"] + counts["contradicted"] + counts["unreviewable"]
        assert n == DECLARED_AT_GRADE_N
        assert counts["unreviewable"] == 1


class TestThePackItselfIsChecked:
    def test_a_pack_with_the_wrong_at_grade_count_is_refused(self):
        cards = pack(n_at_grade=349)
        with pytest.raises(ReviewNotComplete) as e:
            collate(cards, verdicts_for(cards))
        assert "349" in str(e.value) and "350" in str(e.value)

    def test_all_350_at_grade_cards_appear_in_the_scored_set(self):
        cards = pack()
        c = collate(cards, verdicts_for(cards))
        assert len(c.of_disposition("AT_GRADE")) == DECLARED_AT_GRADE_N

    def test_a_repeated_card_code_in_the_key_is_refused(self):
        """Two cards under one code means one of them can never be answered.

        The AT_GRADE total is left at 350 deliberately, so this proves the
        code check fires on its own rather than being masked by the count.
        """
        cards = pack()
        cards[1]["code"] = cards[0]["code"]
        with pytest.raises(ReviewNotComplete) as e:
            collate(cards, verdicts_for(cards))
        assert "repeats" in str(e.value)


class TestDispositionSemanticsAreNotQuietlyRestated:
    """These are the semantics the two previous packs were scored under."""

    def test_only_at_grade_confirms_at_grade(self):
        assert ACCEPTS["AT_GRADE"] == {"at_grade"}

    def test_unresolved_cannot_be_contradicted(self):
        assert "unclear" not in ACCEPTS["UNRESOLVED"]
        assert ACCEPTS["UNRESOLVED"] == {"at_grade", "grade_separated",
                                         "not_a_junction"}

    def test_false_positive_kinds_are_split_by_what_the_reviewer_said(self):
        cards = pack()
        v = [{"code": c["code"],
              "verdict": {"T001": "g", "T002": "n"}.get(c["code"], "a")}
             for c in cards]
        counts = collate(cards, v).counts("AT_GRADE")
        assert counts["grade_separated_false_positives"] == 1
        assert counts["not_a_junction_false_positives"] == 1
        assert evaluate(**counts).met is False
