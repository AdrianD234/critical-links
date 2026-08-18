"""Evidence-backed overrides: the only thing that may create a canonical node.

WHY THE CLASSIFIER NO LONGER DECIDES
------------------------------------
`crossings.classify` was measured against a gate declared before the
measurement existed - 350 AT_GRADE cards, at most 4 failures, unreviewable
counted among them - and it failed in both directions at the same settings:

  * 32 of 350 AT_GRADE crossings are not junctions at all. Five of those came
    from JUNCTION_WITNESS, the ONLY rule backed by positive evidence rather
    than by the absence of contrary evidence.
  * 11 of 25 sampled DUPLICATE_GEOMETRY withdrawals are junctions a reviewer
    can see, on a rule that fires 9,830 times nationally.

There is no threshold between those two. See the audit README sections 13 and
14, and PIVOT.md.

So the rule is now simple enough to state in one line, and this module is what
enforces it:

    A canonical junction exists where an override says so, and nowhere else.

WHAT AN OVERRIDE HAS TO CARRY
-----------------------------
A decision, one of four evidence kinds, a reference that can be checked, a
named reviewer, and a date. An override missing any of those is not an
override and is refused at construction rather than at use, because a bad row
that only fails when a graph is being built is a bad row that gets committed.

FAIL CLOSED, TWICE
------------------
1. No override -> NOT noded. Not "probably", not "the classifier thought so".
2. Overrides that DISAGREE -> NOT noded, and the conflict is reported.

The second is the one worth being careful about. The tempting alternatives -
most recent wins, highest evidence kind wins, AT_GRADE wins - all silently
resolve a disagreement between two humans by a rule neither of them agreed to.
When two reviewers looked at one place and reached opposite conclusions, the
honest state of knowledge is "unresolved", and the graph should show what it
can defend.

MATCHING IS ON THE UNORDERED PAIR
---------------------------------
Which side of a crossing is `source_a` is not stable between ingests: the
source features are read without an ORDER BY, so it is whatever the database
returned that day. Matching on the ordered pair silently lost 567 of 22,062
crossings when that was tried on the national record. Everything here keys on
`frozenset({a, b})`.
"""

from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import crossings as crossings_mod

#: The only admissible kinds of evidence. Deliberately short, and deliberately
#: does not include anything the machine can produce on its own.
EVIDENCE_KINDS = frozenset({
    # An intersection or topology dataset that states this is a junction.
    "AUTHORITATIVE_SOURCE",
    # A named human confirmed it from imagery, on a date.
    "MANUAL_AERIAL_REVIEW",
    # A correction accepted upstream in the source data.
    "SOURCE_DATA_CORRECTION",
    # A durable project decision, carrying reviewer, evidence and date.
    "PROJECT_OVERRIDE",
})

#: An override may assert separation as well as connection. Recording "we
#: looked, it is a bridge" stops the same crossing being queued for review
#: every quarter, and it is evidence in exactly the same sense.
DECISIONS = frozenset({crossings_mod.AT_GRADE, crossings_mod.GRADE_SEPARATED})

#: How far an override may sit from a detected crossing and still be about it.
#: The detector re-derives the intersection point from re-merged geometry each
#: ingest, so it moves by millimetres; 5 m is loose enough to absorb that and
#: far tighter than the 25 m at which this project considers two crossings to
#: be one place - an override must not drift onto its neighbour.
MATCH_TOLERANCE_M = 5.0


class InvalidOverride(ValueError):
    """An override that is missing what makes it an override."""


class OverrideConflict(Exception):
    """Live overrides that disagree about one crossing.

    Raised only by `decide_strict`. The normal path reports the conflict in
    `Decision.conflict` and leaves the crossing disconnected, because one bad
    pair of rows should not stop a national ingest - it should stop that one
    crossing.
    """

    def __init__(self, key, decisions, ids) -> None:
        self.key = key
        self.decisions = tuple(sorted(decisions))
        self.ids = tuple(sorted(ids))
        super().__init__(
            f"overrides {list(self.ids)} disagree about crossing {sorted(key)}: "
            f"{list(self.decisions)}. The crossing stays disconnected.")


@dataclass(frozen=True)
class Override:
    """One human decision about one place on the ground.

    Validated in `__post_init__`, so an invalid override cannot exist as an
    object at all. The alternative - validating where it is used - means the
    bad row is already in the database by the time anything complains.
    """

    source_a: str
    source_b: str
    x: float
    y: float
    decision: str
    evidence_kind: str
    evidence_ref: str
    reviewer: str
    decided_on: _dt.date
    note: str = ""
    retired_on: _dt.date | None = None
    override_id: int | None = None

    def __post_init__(self) -> None:
        if not str(self.source_a).strip() or not str(self.source_b).strip():
            raise InvalidOverride("an override needs both source feature ids")
        if self.source_a == self.source_b:
            raise InvalidOverride(
                "both sides name the same source feature: that is a road "
                "crossing itself, not two roads meeting")
        if self.decision not in DECISIONS:
            raise InvalidOverride(
                f"decision must be one of {sorted(DECISIONS)}, not "
                f"{self.decision!r}. UNRESOLVED is what a crossing already is "
                f"without an override; recording it as one says nothing.")
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise InvalidOverride(
                f"evidence_kind must be one of {sorted(EVIDENCE_KINDS)}, not "
                f"{self.evidence_kind!r}")
        if not str(self.evidence_ref).strip():
            raise InvalidOverride(
                "evidence_ref is blank. An override with nothing to check is "
                "an assertion, which is what this mechanism replaced.")
        if not str(self.reviewer).strip():
            raise InvalidOverride(
                "reviewer is blank. An override nobody is named on cannot be "
                "asked about.")
        if not isinstance(self.decided_on, _dt.date):
            raise InvalidOverride("decided_on must be a date")
        if self.retired_on is not None:
            if not isinstance(self.retired_on, _dt.date):
                raise InvalidOverride("retired_on must be a date or None")
            if self.retired_on < self.decided_on:
                raise InvalidOverride(
                    "retired_on is before decided_on")

    @property
    def key(self) -> frozenset[str]:
        """The unordered source-feature pair. See the module docstring."""
        return frozenset({self.source_a, self.source_b})

    def is_live(self, on: _dt.date | None = None) -> bool:
        """Retirement is a new fact, not an erasure."""
        if self.retired_on is None:
            return True
        return (on or _dt.date.today()) < self.retired_on

    def matches(self, x: float, y: float,
                tolerance_m: float = MATCH_TOLERANCE_M) -> bool:
        return math.hypot(self.x - x, self.y - y) <= tolerance_m


@dataclass(frozen=True)
class Decision:
    """What the overrides say about one crossing, and why.

    `disposition` is None when nothing decided it - which is the common case
    and the safe one. `conflict` being True means overrides matched and
    disagreed; the disposition is None then too, and the reason says so.
    """

    disposition: str | None
    conflict: bool = False
    matched: tuple[Override, ...] = ()
    reason: str = ""

    @property
    def nodes(self) -> bool:
        """Whether the canonical graph creates a shared node here."""
        return self.disposition == crossings_mod.AT_GRADE


@dataclass
class OverrideIndex:
    """Live overrides, indexed by unordered source pair.

    Built once per ingest. Retired overrides are dropped at construction, so
    nothing downstream has to remember to check.
    """

    by_pair: dict[frozenset[str], list[Override]] = field(default_factory=dict)
    tolerance_m: float = MATCH_TOLERANCE_M
    retired: int = 0

    @classmethod
    def build(cls, overrides: Iterable[Override], *,
              tolerance_m: float = MATCH_TOLERANCE_M,
              on: _dt.date | None = None) -> "OverrideIndex":
        idx = cls(tolerance_m=tolerance_m)
        for o in overrides:
            if not o.is_live(on):
                idx.retired += 1
                continue
            idx.by_pair.setdefault(o.key, []).append(o)
        return idx

    def __len__(self) -> int:
        return sum(len(v) for v in self.by_pair.values())

    def decide(self, source_a: str, source_b: str,
               x: float, y: float) -> Decision:
        """What, if anything, the evidence says about this crossing."""
        candidates = [o for o in self.by_pair.get(frozenset({source_a, source_b}), ())
                      if o.matches(x, y, self.tolerance_m)]
        if not candidates:
            return Decision(
                None, reason="no evidence-backed override matches this crossing")

        decisions = {o.decision for o in candidates}
        if len(decisions) > 1:
            return Decision(
                None, conflict=True, matched=tuple(candidates),
                reason=(
                    "overrides disagree: "
                    + ", ".join(
                        f"{o.reviewer} said {o.decision} on "
                        f"{o.decided_on.isoformat()} ({o.evidence_kind} "
                        f"{o.evidence_ref})" for o in sorted(
                            candidates, key=lambda o: (o.decided_on, o.reviewer)))
                    + ". The crossing stays disconnected: resolving a "
                      "disagreement between two reviewers by a tie-break "
                      "neither of them agreed to is not evidence."))

        only = sorted(candidates, key=lambda o: (o.decided_on, o.reviewer))[0]
        return Decision(
            only.decision, matched=tuple(candidates),
            reason=(f"{only.reviewer} recorded {only.decision} on "
                    f"{only.decided_on.isoformat()} "
                    f"({only.evidence_kind} {only.evidence_ref})"))

    def decide_strict(self, source_a: str, source_b: str,
                      x: float, y: float) -> Decision:
        """`decide`, but raise on a conflict instead of reporting it.

        For tooling that is loading or validating overrides, where a
        disagreement is something to fix now rather than to route around.
        """
        d = self.decide(source_a, source_b, x, y)
        if d.conflict:
            raise OverrideConflict(
                frozenset({source_a, source_b}),
                {o.decision for o in d.matched},
                {o.override_id for o in d.matched})
        return d


def decide_all(found: Sequence["crossings_mod.DetectedCrossing"],
               index: OverrideIndex) -> list[Decision]:
    """One Decision per detected crossing, in the same order.

    Never raises on a conflict: a national ingest should lose the one crossing
    that two people disagree about, not the other twenty-two thousand.
    """
    return [index.decide(x.amds_a, x.amds_b, x.x, x.y) for x in found]


def summarise(decisions: Sequence[Decision]) -> dict:
    """Counts for the ingest log and the QA record."""
    nodes = sum(1 for d in decisions if d.nodes)
    separated = sum(1 for d in decisions
                    if d.disposition == crossings_mod.GRADE_SEPARATED)
    conflicts = [i for i, d in enumerate(decisions) if d.conflict]
    return {
        "crossings": len(decisions),
        "decidedAtGrade": nodes,
        "decidedGradeSeparated": separated,
        "undecided": len(decisions) - nodes - separated - len(conflicts),
        "conflicts": len(conflicts),
        "conflictIndices": conflicts,
        "why": ("Only decidedAtGrade creates a canonical node. Undecided and "
                "conflicting crossings stay disconnected - see PIVOT.md."),
    }


# --------------------------------------------------------------------------
def load(snapshot_id: str | None = None, *,
         on: _dt.date | None = None) -> OverrideIndex:
    """Every live override, from the database.

    Overrides are NOT snapshot-scoped: they are decisions about places on the
    ground and they must survive re-ingest, or every refresh throws away the
    review effort that is the whole point. `snapshot_id` is accepted and
    ignored so callers can pass one without pretending it filters.
    """
    from . import db

    rows = db.query(
        "SELECT override_id, source_a, source_b, "
        "       ST_X(geom_2193) AS x, ST_Y(geom_2193) AS y, "
        "       decision, evidence_kind, evidence_ref, reviewer, "
        "       decided_on, note, retired_on "
        "  FROM crossing_overrides")
    return OverrideIndex.build(
        (Override(
            source_a=r["source_a"], source_b=r["source_b"],
            x=float(r["x"]), y=float(r["y"]),
            decision=r["decision"], evidence_kind=r["evidence_kind"],
            evidence_ref=r["evidence_ref"], reviewer=r["reviewer"],
            decided_on=r["decided_on"], note=r["note"] or "",
            retired_on=r["retired_on"], override_id=r["override_id"])
         for r in rows), on=on)
