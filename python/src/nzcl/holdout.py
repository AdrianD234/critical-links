"""Collating a blinded review's verdicts, so a short review cannot pass.

THE LOOPHOLE THIS MODULE CLOSES
-------------------------------
350 AT_GRADE cards are drawn. The reviewer returns 340 verdicts, having
quietly skipped ten. A scorer that joins verdicts to the answer key and
evaluates what it finds computes a bound over n=340, and the ten missing cards
disappear out of the DENOMINATOR.

That is not a rounding error, it is the gate's own rule run backwards. The
predeclaration counts unreviewable cards as FAILURES, precisely because a card
the reviewer could not read is not evidence that the classifier was right. A
card the reviewer did not answer at all is, if anything, weaker evidence than
one they looked at and could not call. And the cards a reviewer skips are not
a random ten: they are the hard ones, which is where the errors live. So the
omission moves the number in the flattering direction, silently, by exactly
the mechanism the gate exists to forbid.

`nzcl.promotion.evaluate` cannot see this. It takes counts. By the time a
count reaches it the loss has already happened, and it would report `met` in
perfectly good faith. The check has to happen where verdicts meet the answer
key, which is here.

HOW IT FAILS
------------
`collate(..., strict=True)`, the default, RAISES on any of: a card with no
verdict, a card with two, a verdict for a code that is not in the pack, or a
label that is not one of the four. Nothing is dropped and nothing is guessed.

`strict=False` is the fallback for a review that genuinely cannot be
completed: every missing, duplicated or unparseable verdict is MATERIALISED as
`unclear` and therefore counted as a failure. It is not the lenient option -
it is the same refusal expressed as arithmetic instead of an exception - and
every substitution is listed in `Collated.materialised` so a reader can see
what was assumed. A verdict for a code that is not in the pack is fatal in
both modes, because it cannot be materialised into anything and it means the
verdict file does not describe this pack.

There is deliberately no third mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

#: The four verdicts a reviewer may return, and the single-letter forms the
#: instruction sheet asks for. Anything else is an error, not an "other".
LEGEND = {"a": "at_grade", "g": "grade_separated",
          "n": "not_a_junction", "u": "unclear"}
VALID = frozenset(LEGEND.values())

#: What a verdict is allowed to be, per disposition. AT_GRADE is the only
#: disposition that creates a graph node, so only "at grade" confirms it.
#: GRADE_SEPARATED and UNRESOLVED both leave the crossing severed, so anything
#: except "this is a plain at-grade junction" is consistent with them - which
#: is why UNRESOLVED cannot be contradicted and its precision is not a
#: meaningful number.
ACCEPTS = {
    "AT_GRADE": frozenset({"at_grade"}),
    "GRADE_SEPARATED": frozenset({"grade_separated", "not_a_junction"}),
    "UNRESOLVED": frozenset({"at_grade", "grade_separated", "not_a_junction"}),
}

#: The third holdout's predeclared AT_GRADE sample size. A pack that does not
#: carry exactly this many AT_GRADE cards is not the pack that was declared,
#: and scoring it would be scoring something else under its name.
DECLARED_AT_GRADE_N = 350


class ReviewNotComplete(Exception):
    """Raised instead of scoring a review that is missing or malformed.

    Carries the specifics rather than a message alone, so a caller can report
    exactly which cards are unaccounted for instead of "something was wrong".
    """

    def __init__(self, *, missing: Iterable[str] = (),
                 duplicated: Iterable[str] = (),
                 unknown: Iterable[str] = (),
                 invalid: Iterable[str] = (),
                 detail: str = "") -> None:
        self.missing = tuple(sorted(missing))
        self.duplicated = tuple(sorted(duplicated))
        self.unknown = tuple(sorted(unknown))
        self.invalid = tuple(sorted(invalid))
        self.detail = detail
        parts: list[str] = []
        if self.missing:
            parts.append(f"{len(self.missing)} card(s) with no verdict: "
                         + ", ".join(self.missing[:12])
                         + (" ..." if len(self.missing) > 12 else ""))
        if self.duplicated:
            parts.append(f"{len(self.duplicated)} card(s) with more than one "
                         "verdict: " + ", ".join(self.duplicated[:12]))
        if self.unknown:
            parts.append(f"{len(self.unknown)} verdict(s) for codes that are "
                         "not in this pack: " + ", ".join(self.unknown[:12]))
        if self.invalid:
            parts.append(f"{len(self.invalid)} verdict(s) whose label is not "
                         f"one of {sorted(VALID)}: "
                         + ", ".join(self.invalid[:12]))
        if detail:
            parts.append(detail)
        super().__init__("the review is not complete, so it was not scored. "
                         + " | ".join(parts))


@dataclass(frozen=True)
class Collated:
    """One row per card in the pack. Always. The length is the pack's."""

    rows: list[dict] = field(default_factory=list)
    #: Codes whose verdict was manufactured as `unclear` under strict=False.
    materialised: tuple[str, ...] = ()

    def of_disposition(self, disposition: str) -> list[dict]:
        return [r for r in self.rows if r["disposition"] == disposition]

    def counts(self, disposition: str = "AT_GRADE") -> dict:
        """The five numbers `promotion.evaluate` takes, over one disposition.

        `confirmed + contradicted + unreviewable` is n by construction, because
        every card in the pack has a row here and no row was ever dropped.
        """
        sub = self.of_disposition(disposition)
        accepts = ACCEPTS[disposition]
        confirmed = sum(1 for r in sub if r["verdict"] in accepts)
        unreviewable = sum(1 for r in sub if r["verdict"] == "unclear")
        at_grade = disposition == "AT_GRADE"
        return {
            "confirmed": confirmed,
            "contradicted": len(sub) - confirmed - unreviewable,
            "unreviewable": unreviewable,
            "grade_separated_false_positives":
                sum(1 for r in sub if r["verdict"] == "grade_separated")
                if at_grade else 0,
            "not_a_junction_false_positives":
                sum(1 for r in sub if r["verdict"] == "not_a_junction")
                if at_grade else 0,
        }


def normalise(label) -> str | None:
    """One of the four verdicts, or None. Never a guess."""
    if label is None:
        return None
    s = str(label).strip().lower()
    if s in VALID:
        return s
    return LEGEND.get(s)


def assert_pack_is_the_declared_one(
        cards: Sequence[dict], *,
        expected_at_grade: int = DECLARED_AT_GRADE_N) -> int:
    """The answer key must hold exactly the declared number of AT_GRADE cards.

    Checked before anything is scored. If the pack has drifted - a card
    dropped because it would not render, a decoy miscounted as AT_GRADE - the
    arithmetic of the declaration no longer describes it, and the right
    response is to stop rather than to report a bound over whatever survived.
    """
    n = sum(1 for c in cards if c["disposition"] == "AT_GRADE")
    if n != expected_at_grade:
        raise ReviewNotComplete(
            detail=f"the answer key holds {n} AT_GRADE cards and the "
                   f"predeclaration says {expected_at_grade}. This is not the "
                   f"pack that was declared.")
    codes = [c["code"] for c in cards]
    if len(set(codes)) != len(codes):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        raise ReviewNotComplete(
            detail=f"the answer key repeats {len(dupes)} card code(s): "
                   + ", ".join(dupes[:12]))
    return n


def collate(cards: Sequence[dict], verdicts: Iterable[dict], *,
            strict: bool = True,
            expected_at_grade: int = DECLARED_AT_GRADE_N) -> Collated:
    """Join verdicts to the answer key without ever losing a card.

    `cards` is the answer key's card list; `verdicts` is a sequence of
    ``{"code": ..., "verdict": ..., "note": ...}``.

    Returns one row per card in `cards` - not one per verdict. That is the
    whole point: the denominator is a property of the pack, not of what came
    back.
    """
    assert_pack_is_the_declared_one(cards, expected_at_grade=expected_at_grade)
    by_code = {c["code"]: c for c in cards}

    seen: dict[str, str] = {}
    notes: dict[str, str] = {}
    duplicated: set[str] = set()
    unknown: set[str] = set()
    invalid: set[str] = set()
    invalid_codes: set[str] = set()
    for v in verdicts:
        code = str(v.get("code", "")).strip()
        if code not in by_code:
            unknown.add(code or "<blank>")
            continue
        label = normalise(v.get("verdict"))
        if label is None:
            invalid.add(f"{code}={v.get('verdict')!r}")
            invalid_codes.add(code)
            continue
        if code in seen:
            duplicated.add(code)
            continue
        seen[code] = label
        notes[code] = str(v.get("note", "") or "")

    # A card whose only verdict was unparseable has no usable verdict. It is
    # reported under `invalid` and not counted a second time under `missing`.
    missing = set(by_code) - set(seen) - invalid_codes

    # An unknown code cannot be materialised into anything, and it means the
    # verdict file does not describe this pack. Fatal in both modes.
    if unknown:
        raise ReviewNotComplete(missing=missing, duplicated=duplicated,
                                unknown=unknown, invalid=invalid)
    if strict and (missing or duplicated or invalid):
        raise ReviewNotComplete(missing=missing, duplicated=duplicated,
                                invalid=invalid)

    # A card with two conflicting verdicts is not a card with one answer. It
    # is unreviewed, and unreviewed is a failure.
    materialised = tuple(sorted(missing | duplicated | invalid_codes))
    for code in materialised:
        seen[code] = "unclear"
        notes.setdefault(code, "")

    rows = [{**by_code[code], "verdict": seen[code],
             "note": notes.get(code, ""), "materialised": code in materialised}
            for code in sorted(by_code)]
    if len(rows) != len(by_code):
        raise ReviewNotComplete(
            detail=f"collation produced {len(rows)} rows for {len(by_code)} "
                   f"cards, which must never happen")
    return Collated(rows=rows, materialised=materialised)
