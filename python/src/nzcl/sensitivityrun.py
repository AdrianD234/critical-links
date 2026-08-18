"""The production Runner: canonical first, then one bounded assumption at a time.

This is the seam between `sensitivity` (which decides WHAT to ask) and the
database (which answers it). It exists as its own module so the engine stays
testable without a snapshot, and so the rule below has one place to live:

    THE CANONICAL ANALYSIS RUNS FIRST, IS RETURNED UNCHANGED, AND IS THE
    PRODUCT ANSWER. Everything else is an assumption in a separate block.

HOW A COUNTERFACTUAL IS ACTUALLY RUN
------------------------------------
1. The canonical analysis, on the real snapshot. Its `AnalysisPin` is frozen.
2. A bounded neighbourhood is extracted and must REPRODUCE that pin - not
   merely the same distance, the same question: same closure, movement, ports,
   profile, metric, restriction state and route. See `pinning`.
3. Per candidate: the validated neighbourhood is copied (small, so cheap), the
   one crossing is noded on the copy, and the SAME movement is re-run.
4. Every copy is dropped, on every path.

THE FROZEN MOVEMENT IS INJECTED, NOT JUST COMPARED
--------------------------------------------------
`pinned_movement` is passed into the counterfactual analysis so the copy
cannot select its own principal movement. That is the difference between
detecting a divergence after the fact and preventing it: a copy told which
movement to answer cannot answer a different one and return a coincidentally
equal distance. Where the pin cannot be honoured the counterfactual is
discarded with a reason rather than reported.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from . import candidates as candidates_mod
from . import db, neighbourhood, pinning, sensitivity, whatif


@dataclass
class Timing:
    """Measured, per stage. Extraction alone is not a response time."""

    canonical_ms: int = 0
    candidates_ms: int = 0
    extract_validate_ms: int = 0
    counterfactual_ms: list[int] = field(default_factory=list)
    cleanup_ms: int = 0

    @property
    def total_ms(self) -> int:
        return (self.canonical_ms + self.candidates_ms
                + self.extract_validate_ms + sum(self.counterfactual_ms)
                + self.cleanup_ms)

    def as_dict(self) -> dict:
        cf = sorted(self.counterfactual_ms)
        return {
            "canonicalMs": self.canonical_ms,
            "candidateSearchMs": self.candidates_ms,
            "neighbourhoodExtractAndValidateMs": self.extract_validate_ms,
            "singleCounterfactualMs": {
                "runs": len(cf),
                "p50": cf[len(cf) // 2] if cf else 0,
                "max": cf[-1] if cf else 0,
                "total": sum(cf),
            },
            "cleanupMs": self.cleanup_ms,
            "totalMs": self.total_ms,
            "note": ("Stage timings. The canonical analysis is the only one a "
                     "user waits for when sensitivity runs on its own "
                     "endpoint."),
        }


@dataclass
class SensitivityRun:
    """The canonical answer, the assumptions, and how it was all obtained."""

    sensitivity: sensitivity.Sensitivity | None
    search: candidates_mod.CandidateSearch
    timing: Timing
    canonical_pin: pinning.AnalysisPin
    validation: pinning.ValidationReport | None = None
    unavailable_reason: str | None = None
    extraction: neighbourhood.Extraction | None = None

    @property
    def available(self) -> bool:
        return self.sensitivity is not None

    def as_dict(self) -> dict:
        if not self.available:
            return {
                "available": False,
                "unavailableReason": self.unavailable_reason,
                "candidateSearch": self.search.as_dict(),
                "canonicalPin": self.canonical_pin.as_dict(),
                "validation": (self.validation.as_dict()
                               if self.validation else None),
                "timing": self.timing.as_dict(),
                "why": ("Topology sensitivity could not be established for "
                        "this analysis. That is NOT the same as the answer "
                        "being robust, and a high-consequence result must not "
                        "be presented as definitive on the strength of it."),
            }
        out = sensitivity.as_dict(self.sensitivity)
        out.update({
            "available": True,
            "analysisPartial": self.sensitivity.partial,
            "candidateSearch": self.search.as_dict(),
            "canonicalPin": self.canonical_pin.as_dict(),
            "validation": self.validation.as_dict() if self.validation else None,
            "neighbourhood": (self.extraction.as_dict()
                              if self.extraction else None),
            "timing": self.timing.as_dict(),
        })
        return out


def _crossing_links(snapshot_id: str, c: sensitivity.Candidate):
    """The two LINK ids at a crossing, in the snapshot being edited.

    `crossings` records AMDS source features; `whatif.node_crossings` cuts
    LINKS. A source feature is many links after splitting, so the pair has to
    be resolved against the geometry at the point, in the copy.
    """
    rows = db.query(
        "SELECT closure_group_id, link_id FROM links "
        " WHERE snapshot_id = %s AND closure_group_id = ANY(%s) "
        "   AND ST_DWithin(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), 1.0)",
        (snapshot_id, [c.source_a, c.source_b], c.x, c.y))
    a = next((r["link_id"] for r in rows if r["closure_group_id"] == c.source_a),
             None)
    b = next((r["link_id"] for r in rows if r["closure_group_id"] == c.source_b),
             None)
    return a, b


def run(snapshot_id: str, link_id: int, *, analyse_fn, pin_fn,
        route_link_ids=None, port_node_ids=None, force_near=(),
        max_single: int = sensitivity.MAX_SINGLE,
        max_pairs: int = sensitivity.MAX_PAIRS,
        should_cancel=None) -> SensitivityRun:
    """Canonical analysis, then bounded counterfactuals against a copy.

    `analyse_fn(snapshot_id, link_id, pinned_movement=...)` runs one analysis;
    `pin_fn(result)` turns it into an `AnalysisPin`. Both are injected so this
    module can be exercised without importing the whole impact stack, and so
    the impact stack does not have to know sensitivity exists.

    `should_cancel()` is polled between counterfactuals. A cancelled run drops
    everything and reports unavailability - it never returns a partial
    sensitivity as if it were complete.
    """
    timing = Timing()

    t = time.perf_counter()
    canonical_result = analyse_fn(snapshot_id, link_id)
    canonical_pin = pin_fn(canonical_result)
    timing.canonical_ms = int((time.perf_counter() - t) * 1000)
    canonical_answer = _answer_of(canonical_pin)

    t = time.perf_counter()
    search = candidates_mod.find(
        snapshot_id,
        closure_link_ids=[link_id],
        route_link_ids=list(route_link_ids if route_link_ids is not None
                            else getattr(canonical_result, "route_link_ids", ())
                            or ()),
        port_node_ids=list(port_node_ids if port_node_ids is not None
                           else getattr(canonical_result, "port_node_ids", ())
                           or ()),
        force_near=force_near)
    timing.candidates_ms = int((time.perf_counter() - t) * 1000)

    def unavailable(reason, validation=None):
        return SensitivityRun(None, search, timing, canonical_pin,
                              validation=validation, unavailable_reason=reason)

    if not search.candidates:
        return unavailable(
            "no unresolved crossing near this analysis could change it"
            + (" (the candidate search was TRUNCATED, so this is not a "
               "statement that the answer is robust)" if search.truncated
               else ""))

    def answer_of(sid: str):
        """Re-run the SAME movement on a copy, and pin what came back."""
        return pin_fn(analyse_fn(sid, link_id,
                                 pinned_movement=canonical_pin.movement))

    t = time.perf_counter()
    try:
        ex = neighbourhood.extract_validated(
            snapshot_id, link_id, canonical=canonical_pin,
            answer_of=answer_of)
    except neighbourhood.NeighbourhoodTooSmall as e:
        timing.extract_validate_ms = int((time.perf_counter() - t) * 1000)
        return unavailable(
            f"no bounded neighbourhood reproduced the canonical answer: {e}")
    except neighbourhood.DerivedStructuresMissing as e:
        timing.extract_validate_ms = int((time.perf_counter() - t) * 1000)
        return unavailable(f"the bounded copy was incomplete: {e}")
    timing.extract_validate_ms = int((time.perf_counter() - t) * 1000)

    validation = pinning.ValidationReport(
        agreed=True, canonical=canonical_pin, observed=canonical_pin,
        derived_inventory=neighbourhood.derived_inventory(ex.snapshot_id))

    made: list[str] = []
    try:
        def runner(assumed: frozenset) -> sensitivity.Answer:
            if not assumed:
                return canonical_answer
            if should_cancel is not None and should_cancel():
                raise _Cancelled()
            t0 = time.perf_counter()
            sid = f"cf-{uuid.uuid4().hex[:12]}"
            whatif.copy_snapshot(ex.snapshot_id, sid)
            made.append(sid)
            try:
                edits = []
                for cid in assumed:
                    c = next(x for x in search.candidates
                             if x.crossing_id == cid)
                    a, b = _crossing_links(sid, c)
                    missing = ([] if a is not None else ["source A"]) +                               ([] if b is not None else ["source B"])
                    if missing:
                        # NOT `return canonical_answer`. See
                        # CandidateMaterialisationError.
                        raise CandidateMaterialisationError(c, sid, missing)
                    edits.append(whatif.CrossingEdit(a, b, c.x, c.y))
                whatif.node_crossings(sid, edits)
                got = pin_fn(analyse_fn(sid, link_id,
                                        pinned_movement=canonical_pin.movement))
                return _answer_of(got)
            finally:
                whatif.drop_snapshot(sid)
                made.remove(sid)
                timing.counterfactual_ms.append(
                    int((time.perf_counter() - t0) * 1000))

        try:
            s = sensitivity.analyse(search.candidates, runner,
                                    max_single=max_single, max_pairs=max_pairs)
        except _Cancelled:
            return unavailable("the request was cancelled", validation)
    finally:
        t = time.perf_counter()
        for sid in list(made):
            whatif.drop_snapshot(sid)
        whatif.drop_snapshot(ex.snapshot_id)
        timing.cleanup_ms = int((time.perf_counter() - t) * 1000)

    return SensitivityRun(s, search, timing, canonical_pin,
                          validation=validation, extraction=ex)


class CandidateMaterialisationError(sensitivity.Untestable):
    """A candidate crossing could not be turned into an edit on the copy.

    Raised instead of returning the canonical answer. Returning it would make
    the crossing come out `individuallyChangesAnswer: False` and be reported
    non-material - on no evidence, because nothing was assumed and nothing was
    routed. An untested thing must never surface as a tested negative.

    Every cause is real: the bounded extraction omitted one of the two source
    features, a feature split differently in the copy, a stale crossing to
    source mapping, a failed local geometry lookup, or a candidate catalogue
    that does not match the snapshot. The detail says which side was missing
    so it can be told which.
    """

    def __init__(self, candidate, snapshot_id, missing) -> None:
        detail = {
            "crossingId": candidate.crossing_id,
            "sourceA": candidate.source_a,
            "sourceB": candidate.source_b,
            "x": candidate.x, "y": candidate.y,
            "boundedSnapshotId": snapshot_id,
            "missingSourceLinks": list(missing),
        }
        super().__init__(
            f"crossing {candidate.crossing_id} could not be materialised on "
            f"{snapshot_id}: no link found for {', '.join(missing)}. NOT "
            f"tested, and NOT non-material.", detail)


class _Cancelled(Exception):
    """Internal: a cancelled run must not return a partial sensitivity."""


def _answer_of(pin: pinning.AnalysisPin) -> sensitivity.Answer:
    return sensitivity.Answer(
        status=pin.status, distance_m=pin.distance_m,
        is_bridge=pin.is_bridge,
        isolated_link_count=pin.isolated_link_count)
