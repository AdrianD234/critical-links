"""Per-analysis topology sensitivity: bounded counterfactuals, one at a time.

WHAT THIS REPLACES, AND WHY
---------------------------
There used to be a national "possible" graph: one snapshot with EVERY
unresolved crossing connected, routed against to see whether an answer was
robust. It is gone, and it should be.

A graph with 16,138 speculative junctions all connected at once produces
routes that turn at a chain of them - each individually plausible, jointly
absurd. "Your replacement route is 4.9 km, assuming these nine crossings we
could not resolve are all junctions" is not a useful sentence, and the version
that omits the qualifier is worse.

So sensitivity is measured against the CANONICAL graph, one assumption at a
time, and the assumption is named:

    Topology-sensitive. The canonical represented route is 7.94 km, but falls
    to 4.92 km if the unresolved Clintons Road x McLaughlins Road crossing is
    an at-grade junction.

That is more useful than confidently publishing 7.94 km, and far more honest
than silently inventing a junction and publishing 4.92 km.

THE SHAPE OF THE THING
----------------------
`analyse` takes candidates and a `Runner` - a callable that answers "what does
this movement come out as, if these crossings were junctions?" - and calls it
a bounded number of times:

    once with nothing assumed          the CANONICAL answer, always first
    once per candidate                 the one-at-a-time counterfactuals
    at most `max_pairs` pairs          only where no single candidate moved it

The Runner is injected rather than imported, for two reasons. It is the seam
that lets every behaviour below be tested on a small synthetic network without
a national snapshot; and it keeps the counterfactual mechanism - an isolated
snapshot copy, noded, routed and dropped - out of a module whose job is to
decide what to ask rather than how to ask it.

WHAT THIS MODULE MUST NEVER DO
------------------------------
Return a counterfactual where a caller could mistake it for the canonical
answer. `Sensitivity.canonical` is the answer. Everything else is inside
`counterfactuals`, marked, and the serialiser keeps them apart. Both halves
are mutation-tested: break the separation and tests fail.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Sequence

#: How many single-candidate counterfactuals one analysis may run. Sensitivity
#: is a diagnostic, not a search: past a couple of dozen the right answer is
#: "this area's topology is unreviewed", not another hundred route calls.
MAX_SINGLE = 24

#: Pairs are tested only where no single candidate changed the answer, and
#: only a handful. Two crossings jointly required is a real case - a route
#: that needs both halves of a staggered junction - but three is a chain, and
#: a chain is what the national possible graph was rejected for.
MAX_PAIRS = 12

#: Distances closer than this are the same distance. Routing costs are sums of
#: floats over hundreds of arcs.
COST_EPSILON_M = 1e-6


@dataclass(frozen=True)
class Candidate:
    """An unresolved crossing near enough to the analysis to matter.

    `classifier_*` is carried for RANKING and for display. It is not evidence
    and nothing here treats it as any: the classifier was measured and
    rejected as a decider. See PIVOT.md.
    """

    crossing_id: int
    source_a: str
    source_b: str
    x: float
    y: float
    classifier_disposition: str | None = None
    classifier_reason: str | None = None
    classifier_confidence: str | None = None
    name_a: str | None = None
    name_b: str | None = None

    @property
    def label(self) -> str:
        a = self.name_a or "unnamed road"
        b = self.name_b or "unnamed road"
        return f"{a} x {b}"


@dataclass(frozen=True)
class Answer:
    """What one run of the analysis came out as.

    Deliberately small, and deliberately covers the four things a missing
    junction can change: how far the replacement goes, whether one exists at
    all, whether the closed link is a bridge, and how much of the network is
    cut off.
    """

    status: str
    distance_m: float | None = None
    is_bridge: bool | None = None
    isolated_link_count: int | None = None

    def differs_from(self, other: "Answer") -> bool:
        if self.status != other.status:
            return True
        if self.is_bridge != other.is_bridge:
            return True
        if self.isolated_link_count != other.isolated_link_count:
            return True
        if (self.distance_m is None) != (other.distance_m is None):
            return True
        if self.distance_m is not None and other.distance_m is not None:
            return abs(self.distance_m - other.distance_m) > COST_EPSILON_M
        return False

    def what_changed(self, base: "Answer") -> list[str]:
        out: list[str] = []
        if self.status != base.status:
            out.append(f"status {base.status} -> {self.status}")
        if self.is_bridge != base.is_bridge:
            out.append(f"bridge {base.is_bridge} -> {self.is_bridge}")
        if self.isolated_link_count != base.isolated_link_count:
            out.append(f"isolated links {base.isolated_link_count} -> "
                       f"{self.isolated_link_count}")
        if base.distance_m is not None and self.distance_m is not None \
                and abs(self.distance_m - base.distance_m) > COST_EPSILON_M:
            out.append(f"distance {base.distance_m:.1f} m -> "
                       f"{self.distance_m:.1f} m")
        elif (base.distance_m is None) != (self.distance_m is None):
            out.append(f"distance {base.distance_m} -> {self.distance_m}")
        return out


#: Given a set of crossing ids to ASSUME are junctions, what is the answer?
#: The empty set must return the canonical answer.
Runner = Callable[[frozenset[int]], Answer]


@dataclass(frozen=True)
class Counterfactual:
    """One bounded what-if. Never the answer; always an assumption."""

    assumed: tuple[int, ...]
    answer: Answer
    changes_the_answer: bool
    changed: tuple[str, ...] = ()
    #: True when some OTHER assumption produces the same changed answer, so
    #: this one is not individually required. The equal-cost-way-round case.
    has_equal_alternative: bool = False

    @property
    def is_counterfactual(self) -> bool:
        """Structural marker. `Sensitivity.canonical` has no such property,
        which is what the separation guard test asserts on."""
        return True


@dataclass
class Sensitivity:
    """The canonical answer, and what would have to be true to change it."""

    canonical: Answer
    candidates: list[Candidate] = field(default_factory=list)
    counterfactuals: list[Counterfactual] = field(default_factory=list)
    runs: int = 0
    truncated: bool = False

    @property
    def changing(self) -> list[Counterfactual]:
        return [c for c in self.counterfactuals if c.changes_the_answer]

    @property
    def topology_sensitive(self) -> bool:
        """Would ANY single tested assumption change the published answer?"""
        return bool(self.changing)

    @property
    def decisive(self) -> list[Counterfactual]:
        """Changing assumptions with no equal alternative.

        A crossing that changes the answer, where nothing else does the same
        job, is the one worth a reviewer's time. One where an equal-cost way
        round exists is not decisive, and saying so was a P0 once already.
        """
        return [c for c in self.changing if not c.has_equal_alternative]

    @property
    def jointly_required(self) -> list[Counterfactual]:
        return [c for c in self.changing if len(c.assumed) > 1]

    def candidate(self, crossing_id: int) -> Candidate | None:
        for c in self.candidates:
            if c.crossing_id == crossing_id:
                return c
        return None


def analyse(candidates: Sequence[Candidate], run: Runner, *,
            max_single: int = MAX_SINGLE, max_pairs: int = MAX_PAIRS,
            test_pairs: bool = True) -> Sensitivity:
    """Canonical answer first, then one assumption at a time.

    `run(frozenset())` is called before anything else and its result is THE
    answer. Every later call is an assumption, and none of them can replace it.
    """
    canonical = run(frozenset())
    out = Sensitivity(canonical=canonical, candidates=list(candidates), runs=1)

    tested = list(candidates)[:max_single]
    out.truncated = len(candidates) > len(tested)

    singles: list[Counterfactual] = []
    for cand in tested:
        answer = run(frozenset({cand.crossing_id}))
        out.runs += 1
        differs = answer.differs_from(canonical)
        singles.append(Counterfactual(
            assumed=(cand.crossing_id,), answer=answer,
            changes_the_answer=differs,
            changed=tuple(answer.what_changed(canonical)) if differs else ()))

    # An equal alternative means another single assumption lands on the SAME
    # changed answer. Neither is then individually required, and calling
    # either "decisive" would be the defect this project already fixed once.
    changed_singles = [c for c in singles if c.changes_the_answer]
    for i, c in enumerate(changed_singles):
        for j, other in enumerate(changed_singles):
            if i != j and not c.answer.differs_from(other.answer):
                singles[singles.index(c)] = replace(c, has_equal_alternative=True)
                break
    out.counterfactuals.extend(singles)

    # Pairs, only where nothing single moved it. A junction that needs three
    # assumptions is not a finding, it is the possible graph again.
    if test_pairs and not out.topology_sensitive and len(tested) > 1:
        for n, (a, b) in enumerate(itertools.combinations(tested, 2)):
            if n >= max_pairs:
                out.truncated = True
                break
            assumed = frozenset({a.crossing_id, b.crossing_id})
            answer = run(assumed)
            out.runs += 1
            if answer.differs_from(canonical):
                out.counterfactuals.append(Counterfactual(
                    assumed=tuple(sorted(assumed)), answer=answer,
                    changes_the_answer=True,
                    changed=tuple(answer.what_changed(canonical))))
    return out


def headline(s: Sensitivity, *, unit: str = "m") -> str:
    """The sentence a user should read. Canonical first, always."""
    if not s.topology_sensitive:
        return ""
    best = min(s.changing,
               key=lambda c: (c.answer.distance_m
                              if c.answer.distance_m is not None else float("inf")))
    names = " and ".join(
        (s.candidate(cid).label if s.candidate(cid) else f"crossing {cid}")
        for cid in best.assumed)
    plural = "crossings are at-grade junctions" if len(best.assumed) > 1 \
        else "crossing is an at-grade junction"
    if s.canonical.distance_m is not None and best.answer.distance_m is not None:
        return (f"Topology-sensitive. The canonical represented route is "
                f"{s.canonical.distance_m:.0f} {unit}, but falls to "
                f"{best.answer.distance_m:.0f} {unit} if the unresolved "
                f"{names} {plural}.")
    return (f"Topology-sensitive. The canonical answer is "
            f"{s.canonical.status}, but becomes {best.answer.status} if the "
            f"unresolved {names} {plural}.")


def as_dict(s: Sensitivity) -> dict:
    """Serialised so a consumer cannot confuse the two kinds of answer.

    `canonicalAnswer` is the answer. `counterfactuals` are assumptions, each
    carrying what it assumed. There is deliberately no top-level distance
    field: a reader reaching for `["distanceM"]` should not find anything, so
    that they have to say which of the two they meant.
    """
    return {
        "topologySensitive": s.topology_sensitive,
        "canonicalAnswer": {
            "isCanonical": True,
            "status": s.canonical.status,
            "distanceM": s.canonical.distance_m,
            "isBridge": s.canonical.is_bridge,
            "isolatedLinkCount": s.canonical.isolated_link_count,
        },
        "counterfactuals": [
            {
                "isCanonical": False,
                "assumedJunctionCrossingIds": list(c.assumed),
                "assumedJunctions": [
                    {"crossingId": cid,
                     "label": (s.candidate(cid).label if s.candidate(cid)
                               else None),
                     "classifierDisposition": (
                         s.candidate(cid).classifier_disposition
                         if s.candidate(cid) else None),
                     "classifierReason": (s.candidate(cid).classifier_reason
                                          if s.candidate(cid) else None)}
                    for cid in c.assumed],
                "status": c.answer.status,
                "distanceM": c.answer.distance_m,
                "isBridge": c.answer.is_bridge,
                "isolatedLinkCount": c.answer.isolated_link_count,
                "changesTheAnswer": c.changes_the_answer,
                "whatChanged": list(c.changed),
                "hasEqualAlternative": c.has_equal_alternative,
            }
            for c in s.counterfactuals],
        "decisiveCrossingIds": sorted(
            {cid for c in s.decisive for cid in c.assumed}),
        "candidatesConsidered": len(s.candidates),
        "counterfactualRuns": s.runs,
        "truncated": s.truncated,
        "headline": headline(s),
        "graph": "canonical",
        "why": (
            "Every figure under counterfactuals assumes one or two unresolved "
            "crossings are junctions, and is NOT the answer. The answer is "
            "canonicalAnswer. No national possible graph is consulted: "
            "assumptions are tested one at a time against the canonical "
            "graph, because a graph with every unresolved crossing connected "
            "produces routes relying on chains of speculative turns."),
    }


def rank(candidates: Iterable[Candidate], s: Sensitivity) -> list[Candidate]:
    """Which crossings a human should look at first.

    This is mechanism 3 of the pivot, and the only surviving use of the
    classifier: order the queue by how much a crossing changes a real finding,
    breaking ties with the classifier's opinion. It turns "review 22,062
    crossings" into "review the ones that change an answer".
    """
    by_id = {c.crossing_id: c for c in candidates}
    score: dict[int, float] = {cid: 0.0 for cid in by_id}
    for cf in s.changing:
        # A status change outranks any distance change: a DISCONNECTED that
        # becomes OK, or a bridge that stops being one, is a different finding
        # rather than a smaller number.
        weight = 1e9 if any(w.startswith(("status", "bridge", "isolated"))
                            for w in cf.changed) else 0.0
        if s.canonical.distance_m is not None \
                and cf.answer.distance_m is not None:
            weight += max(0.0, s.canonical.distance_m - cf.answer.distance_m)
        if not cf.has_equal_alternative:
            weight *= 2.0     # nothing else does this job
        for cid in cf.assumed:
            score[cid] = max(score.get(cid, 0.0), weight)
    return sorted(by_id.values(),
                  key=lambda c: (-score.get(c.crossing_id, 0.0),
                                 c.crossing_id))
