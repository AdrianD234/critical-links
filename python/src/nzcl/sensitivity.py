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


class Untestable(Exception):
    """A Runner could not test this assumption at all.

    Raised INSTEAD of returning an answer. The distinction it protects is the
    one this project keeps having to relearn: an untested thing must never
    surface as a tested negative. If a candidate cannot be materialised - the
    bounded copy omitted a source feature, a feature split differently, a
    catalogue does not match the snapshot - then returning the canonical
    answer would make `individually_changes_answer` come out False and the
    crossing would be reported non-material on no evidence whatever.

    Same shape as a promotion gate that tested one condition and reported
    four, and as a copy missing physical_access where absent read identically
    to measured-and-zero.
    """

    def __init__(self, reason: str, detail: dict | None = None) -> None:
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


#: Given a set of crossing ids to ASSUME are junctions, what is the answer?
#: The empty set must return the canonical answer.
Runner = Callable[[frozenset[int]], Answer]


@dataclass(frozen=True)
class Counterfactual:
    """One bounded what-if. Never the answer; always an assumption."""

    assumed: tuple[int, ...]
    answer: Answer
    #: Assuming THIS, and nothing else, moves the published answer. It is the
    #: fact that decides whether a crossing is worth a reviewer's time.
    individually_changes_answer: bool
    changed: tuple[str, ...] = ()
    #: Other crossings whose assumption, alone, produces an EQUIVALENT changed
    #: answer. Recorded because "either of these explains it" is useful, and
    #: deliberately NOT used to demote anything.
    #:
    #: An earlier version treated this as making neither crossing decisive.
    #: That does not follow. If assuming A alone, or B alone, shortens the
    #: route from 8 km to 5 km, each is individually SUFFICIENT: they are
    #: non-unique, not immaterial, and dropping both from the queue would hide
    #: two real candidates. Non-uniqueness is a property of the explanation,
    #: not a reason to stop looking at the crossing.
    #:
    #: The genuinely non-decisive case is a different one: a crossing the
    #: route USES where an equal-cost way round exists, so removing it changes
    #: nothing. Here that shows up as `individually_changes_answer` being
    #: False, which is exactly right.
    equivalent_alternatives: tuple[int, ...] = ()
    #: The assumption could not be RUN. Not an answer, and never a
    #: negative: `individually_changes_answer` is False here because
    #: nothing was measured, which is a different fact and is reported as
    #: one.
    untested: bool = False
    untested_reason: str = ""
    untested_detail: dict = field(default_factory=dict)

    @property
    def unique_explanation(self) -> bool:
        """Changes the answer, and nothing else does the same job.

        Reported ALONGSIDE the other two facts, never instead of them.
        """
        return (self.individually_changes_answer
                and not self.equivalent_alternatives)

    @property
    def tested(self) -> bool:
        return not self.untested

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
    def untested(self) -> list[Counterfactual]:
        """Candidates that could not be run. NOT non-material."""
        return [c for c in self.counterfactuals if c.untested]

    @property
    def partial(self) -> bool:
        """At least one candidate could not be tested, so 'nothing else
        changes this answer' is not a claim this analysis can make."""
        return bool(self.untested)

    @property
    def material(self) -> list[Counterfactual]:
        """Assumptions that, alone, move the published answer.

        Every one of these is worth reviewing, whether or not some other
        assumption would have done the same job.
        """
        return [c for c in self.counterfactuals
                if c.individually_changes_answer]

    @property
    def changing(self) -> list[Counterfactual]:
        """The older name for `material`, kept so callers do not break."""
        return self.material

    @property
    def topology_sensitive(self) -> bool:
        """Would ANY single tested assumption change the published answer?"""
        return bool(self.material)

    @property
    def unique_explanations(self) -> list[Counterfactual]:
        """Material assumptions nothing else replicates.

        A narrower list than `material`, and NOT a filter on review priority.
        A crossing with an equivalent alternative is still material and still
        queued; this says only that the explanation is unique, which matters
        when deciding what to tell a user, not whether to look at the road.
        """
        return [c for c in self.material if c.unique_explanation]

    @property
    def jointly_required(self) -> list[Counterfactual]:
        return [c for c in self.material if len(c.assumed) > 1]

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
        try:
            answer = run(frozenset({cand.crossing_id}))
        except Untestable as e:
            # NOT an answer. Recorded as untested so it cannot be read as
            # "assumed, and it made no difference".
            out.runs += 1
            singles.append(Counterfactual(
                assumed=(cand.crossing_id,), answer=canonical,
                individually_changes_answer=False,
                untested=True, untested_reason=e.reason,
                untested_detail=dict(e.detail)))
            continue
        out.runs += 1
        differs = answer.differs_from(canonical)
        singles.append(Counterfactual(
            assumed=(cand.crossing_id,), answer=answer,
            individually_changes_answer=differs,
            changed=tuple(answer.what_changed(canonical)) if differs else ()))

    # Which material assumptions land on an EQUIVALENT changed answer. Each is
    # then a sufficient explanation on its own, and they are recorded as
    # alternatives to one another. Nothing is demoted by this - see the note
    # on `Counterfactual.equivalent_alternatives`.
    changed_singles = [c for c in singles
                       if c.individually_changes_answer and c.tested]
    for c in changed_singles:
        others = tuple(sorted(
            o.assumed[0] for o in changed_singles
            if o is not c and not c.answer.differs_from(o.answer)))
        if others:
            singles[singles.index(c)] = replace(
                c, equivalent_alternatives=others)
    out.counterfactuals.extend(singles)

    # Pairs, only where nothing single moved it. A junction that needs three
    # assumptions is not a finding, it is the possible graph again.
    if test_pairs and not out.topology_sensitive and len(tested) > 1:
        for n, (a, b) in enumerate(itertools.combinations(tested, 2)):
            if n >= max_pairs:
                out.truncated = True
                break
            assumed = frozenset({a.crossing_id, b.crossing_id})
            try:
                answer = run(assumed)
            except Untestable:
                out.runs += 1
                continue
            out.runs += 1
            if answer.differs_from(canonical):
                out.counterfactuals.append(Counterfactual(
                    assumed=tuple(sorted(assumed)), answer=answer,
                    individually_changes_answer=True,
                    changed=tuple(answer.what_changed(canonical))))
    return out


def headline(s: Sensitivity, *, unit: str = "m") -> str:
    """The sentence a user should read. Canonical first, always."""
    if not s.topology_sensitive:
        return ""
    best = min(s.material,
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
                # THREE distinct facts, not one collapsed into a verdict.
                "tested": c.tested,
                "untestedReason": c.untested_reason or None,
                "untestedDetail": dict(c.untested_detail) or None,
                "individuallyChangesAnswer": (
                    c.individually_changes_answer if c.tested else None),
                "uniqueExplanation": c.unique_explanation,
                "equivalentAlternativeExplanations":
                    list(c.equivalent_alternatives),
                "whatChanged": list(c.changed),
                "assumptionKind": (
                    "jointlyRequired" if len(c.assumed) > 1
                    else "equivalentAlternativeExists"
                    if c.equivalent_alternatives else "individual"),
            }
            for c in s.counterfactuals],
        # Every crossing that ALONE moves the answer. Non-uniqueness does not
        # remove one from here: two sufficient explanations are two real
        # candidates, and dropping both would hide them.
        "materialCrossingIds": sorted(
            {cid for c in s.material for cid in c.assumed}),
        "uniqueExplanationCrossingIds": sorted(
            {cid for c in s.unique_explanations for cid in c.assumed}),
        "analysisComplete": not s.partial,
        "untestedCrossingIds": sorted(
            {cid for c in s.untested for cid in c.assumed}),
        "candidatesConsidered": len(s.candidates),
        "counterfactualRuns": s.runs,
        "truncated": s.truncated,
        "headline": headline(s),
        "graph": "canonical",
        "ifPartial": (
            "analysisComplete false means at least one candidate could NOT be "
            "tested. Those crossings are listed in untestedCrossingIds and are "
            "NOT non-material - nothing was measured about them. A partial "
            "analysis cannot support the claim that nothing else changes this "
            "answer."),
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
    for cf in s.material:
        # A status change outranks any distance change: a DISCONNECTED that
        # becomes OK, or a bridge that stops being one, is a different finding
        # rather than a smaller number.
        weight = 1e9 if any(w.startswith(("status", "bridge", "isolated"))
                            for w in cf.changed) else 0.0
        if s.canonical.distance_m is not None \
                and cf.answer.distance_m is not None:
            weight += max(0.0, s.canonical.distance_m - cf.answer.distance_m)
        for cid in cf.assumed:
            score[cid] = max(score.get(cid, 0.0), weight)
    # Uniqueness is the LAST tiebreak - never a filter, never a multiplier. A
    # crossing with an equivalent alternative is exactly as material as the
    # one it duplicates, and both belong in the queue at the same priority.
    unique = {cid for c in s.unique_explanations for cid in c.assumed}
    return sorted(by_id.values(),
                  key=lambda c: (-score.get(c.crossing_id, 0.0),
                                 c.crossing_id not in unique,
                                 c.crossing_id))
