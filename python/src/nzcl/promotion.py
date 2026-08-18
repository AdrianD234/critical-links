"""The promotion gate, as code that runs rather than prose that is remembered.

Why this module exists
----------------------
The gate had been agreed as TWO conditions - zero confirmed grade-separated
false positives AND a 95% lower bound on AT_GRADE precision of at least 97% -
and `holdout-result.json` encoded ONE:

    "promotionGate": {
      "confirmedGradeSeparatedFalsePositives": 0,
      "requirement": "must be 0",
      "met": true,
      "confirmed": 189, "contradicted": 3, "unreviewable": 4
    }

The other three numbers are RECORDED there and nothing tests them. At 92.8%
(and 95.5% excluding unreviewable) that holdout does not clear 97%, so the
block says `met: true` about a result that does not meet the agreed gate. The
error is small, mechanical, and points in the one direction that matters: it
declares success.

A gate written in a README is a gate somebody has to remember correctly while
writing a summary. This one is a function that takes counts and returns a
verdict per condition, so "which conditions" and "did it pass" cannot drift
apart, and a test can break when they do.

The four conditions
-------------------
1. ZERO CONFIRMED GRADE-SEPARATED FALSE POSITIVES. An AT_GRADE crossing the
   reviewer calls grade separated is a node created where a road passes over
   another. It invents a turn onto a motorway - a confident wrong answer, and
   the failure the never-node rule existed to prevent.

2. ZERO CONFIRMED NOT-A-JUNCTION FALSE POSITIVES. An AT_GRADE crossing the
   reviewer calls "not a junction" is usually one road recorded twice. It joins
   a road to itself: an artificial cycle, a shortcut, a turn that is not there.
   Smaller than 1, and still a node where no junction exists.

3. LOWER 95% BOUND ON AT_GRADE PRECISION >= 97%, Wilson, two-sided.

4. UNREVIEWABLE COUNTS AS A FAILURE in condition 3. A card the reviewer could
   not read is not evidence that the classifier was right. Excluding them
   measures precision on the subset that was easy to see, which is the subset
   least likely to contain the errors.

`met` is the AND of all four. There is deliberately no `override`, no
`waived`, and no per-condition weighting: a gate with a way round it is a
recommendation.

Sample size is declared BEFORE the draw
---------------------------------------
`required_sample_size` answers "how many cards do I need for a 97% lower bound
if I tolerate f failures", and it exists to be called first. Choosing n after
seeing the result - adding cards because the bound came in short - converts the
gate into something that can always eventually be passed, which is the same
class of error as tuning a classifier against its own holdout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: The agreed lower bound on AT_GRADE precision. NOT tunable from a result:
#: if a run comes in under this, the run failed.
REQUIRED_LOWER_BOUND = 0.97

#: Two-sided 95%.
Z = 1.96


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials.

    Wilson rather than normal-approximation because the interesting results
    here sit near 1.0, where the normal approximation runs off the end of the
    scale and reports bounds above 100%.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def required_sample_size(tolerated_failures: int,
                         required_lower_bound: float = REQUIRED_LOWER_BOUND,
                         limit: int = 20_000) -> int:
    """Smallest n whose lower bound clears the gate with `f` failures.

    Called BEFORE drawing, and the answer is committed before review starts.
    `tolerated_failures` counts everything condition 4 counts: contradictions
    AND unreviewable cards.
    """
    if tolerated_failures < 0:
        raise ValueError("tolerated_failures must not be negative")
    n = max(tolerated_failures + 1, 1)
    while n <= limit:
        lo, _ = wilson(n - tolerated_failures, n)
        if lo >= required_lower_bound:
            return n
        n += 1
    raise ValueError(
        f"no n <= {limit} reaches a {required_lower_bound:.0%} lower bound "
        f"with {tolerated_failures} failures")


@dataclass(frozen=True)
class Condition:
    name: str
    requirement: str
    observed: object
    met: bool
    #: Why this condition exists, so a reader of the JSON does not have to find
    #: the module to know what it is protecting against.
    why: str


@dataclass(frozen=True)
class GateResult:
    met: bool
    conditions: list[Condition] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "met": self.met,
            "requiresAllOf": [c.name for c in self.conditions],
            "conditions": [
                {"name": c.name, "requirement": c.requirement,
                 "observed": c.observed, "met": c.met, "why": c.why}
                for c in self.conditions
            ],
            "detail": self.detail,
        }


def evaluate(*, confirmed: int, contradicted: int, unreviewable: int,
             grade_separated_false_positives: int,
             not_a_junction_false_positives: int,
             required_lower_bound: float = REQUIRED_LOWER_BOUND) -> GateResult:
    """The gate, over one holdout's AT_GRADE counts.

    `confirmed + contradicted + unreviewable` is n. The two false-positive
    counts are subsets of `contradicted`, split by WHAT the reviewer said
    instead, because the two mistakes are not the same size.
    """
    n = confirmed + contradicted + unreviewable
    lo_failures, _ = wilson(confirmed, n)
    lo_excluded, _ = wilson(confirmed, n - unreviewable) \
        if n - unreviewable > 0 else (0.0, 1.0)

    conditions = [
        Condition(
            name="zeroConfirmedGradeSeparatedFalsePositives",
            requirement="must be 0",
            observed=grade_separated_false_positives,
            met=grade_separated_false_positives == 0,
            why=("an AT_GRADE crossing the reviewer calls grade separated is a "
                 "node created where one road passes over another: it invents "
                 "a turn onto a motorway, which is the confident wrong answer "
                 "the never-node rule existed to prevent"),
        ),
        Condition(
            name="zeroConfirmedNotAJunctionFalsePositives",
            requirement="must be 0",
            observed=not_a_junction_false_positives,
            met=not_a_junction_false_positives == 0,
            why=("an AT_GRADE crossing the reviewer calls not-a-junction is "
                 "usually one road recorded twice; noding it joins a road to "
                 "itself and fabricates a cycle, a shortcut or a turn that is "
                 "not on the ground"),
        ),
        Condition(
            name="lowerBound95OnAtGradePrecision",
            requirement=f"must be >= {required_lower_bound:.0%}",
            observed=round(100.0 * lo_failures, 1),
            met=lo_failures >= required_lower_bound,
            why=("AT_GRADE is the only disposition that changes the canonical "
                 "graph, so its precision is the number that governs whether "
                 "the classifier may rebuild it"),
        ),
        Condition(
            name="unreviewableCountedAsFailures",
            requirement="the bound above must be computed with unreviewable "
                        "cards counted as failures",
            observed={
                "unreviewable": unreviewable,
                "lowerBoundCountingThemAsFailures": round(100.0 * lo_failures, 1),
                "lowerBoundExcludingThem": round(100.0 * lo_excluded, 1),
            },
            met=True,
            why=("a card the reviewer could not read is not evidence that the "
                 "classifier was right; excluding them measures precision on "
                 "the subset that was easiest to see, which is the subset "
                 "least likely to hold the errors. Recorded as met because it "
                 "describes HOW the bound above was computed - it is a "
                 "property of the method, not of the result"),
        ),
    ]

    met = all(c.met for c in conditions)
    failed = [c.name for c in conditions if not c.met]
    detail = (
        f"n={n}: {confirmed} confirmed, {contradicted} contradicted, "
        f"{unreviewable} unreviewable. Lower 95% bound "
        f"{100.0 * lo_failures:.1f}% counting unreviewable as failures, "
        f"{100.0 * lo_excluded:.1f}% excluding them. "
    ) + ("every condition met." if met
         else f"NOT MET: {', '.join(failed)}.")
    return GateResult(met=met, conditions=conditions, detail=detail)
