"""A two-point outage, end to end: where, which road, how far round.

WHAT THIS MEASURES, STATED ONCE
-------------------------------
Two numbers, and the difference between them.

    along the outage    the road that is shut, measured along itself. This is
                        the trip the closure prevents.
    replacement         the shortest represented path from one end of the
                        outage to the other, with the outage shut.

    added distance      replacement - along the outage
    ratio               replacement / along the outage

The endpoints are the OUTAGE's own ends, not a link's. That distinction is the
whole reason `ports.py` exists: measuring between a closed link's endpoints
asks a question nobody is asking, and returns DISCONNECTED 44% of the time on
ordinary two-way roads because of it. Here the endpoints are exactly where the
user put the handles, so the measure is the one a reader means - how much
further you have to go because this stretch is shut.

NONE OF THIS IS A TRAFFIC MODEL
-------------------------------
It computes one shortest path between two points. It does not know how many
vehicles used the road, how trips redistribute, or whether anyone would take
the replacement. Same caveat as every other measure in this system, and it is
carried in the payload rather than left to the reader to remember.

WHAT IS DELIBERATELY NOT COMPUTED
---------------------------------
Isolation. `physical.py` answers "what is cut off" on Gu, an UNDIRECTED graph
with one edge per LINK - it has no way to represent half a link, so a partial
span cannot be posed to it without either mutating the national graph or
rounding the outage up to whole links. Rounding up is exactly the error this
feature exists to remove, and it would be silent.

So `isolation` is None, always, with the reason attached rather than the field
quietly missing. Extending Gu to carry request-local split edges is a real
piece of work and belongs in the integration pass, not smuggled in here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Literal, Sequence

from . import config, db, snap, span_corridor, vsplit
from .routing import Metric, Profile, RouteResult, VirtualOverlay, route
from .span_corridor import CorridorChoice, HandleOption, SpanCandidate
from .vsplit import DEFAULT_DIRECTION_MODE, DirectionMode, VirtualSplit

#: Bump when the ANALYSIS changes shape. Result fingerprints embed it.
ANALYSIS_VERSION = "1.0.0-dev"
ALGORITHM = "outage-span-v1"
ENGINE = "v2-outage-span"

#: How settled THIS engine is, in the same form `config.ENGINE_STABILITY` uses
#: - a sentence rendered verbatim, never a grade.
#:
#: It deliberately does NOT reuse `config.ENGINE_STABILITY`. That string now
#: reads "production", which is true of the closure engine and false of this
#: one: the span engine is disabled by default and is a foundation. Borrowing
#: it would tell a reader the figures in front of them are the product's
#: answer, which is exactly the misstatement the closure engine's own string
#: was rewritten to remove.
#:
#: It carries the closure engine's outstanding caveat as well as its own,
#: because both apply to a span.
STABILITY = (
    "foundation - disabled by default. Turn restrictions are post-validated "
    "rather than enforced during routing, and across a link split by a handle "
    "they cannot be validated at all, so a route that crosses one is withheld "
    "rather than offered."
)

#: Why a partial span carries no topology-sensitivity answer.
#:
#: The staged sensitivity engine asks what an unresolved crossing would change,
#: by re-running a CANONICAL analysis under counterfactual topologies. Its
#: runner is built around whole-link closures: it pins a closure by link id and
#: replays the analysis for each candidate crossing.
#:
#: A partial span is not addressable that way - it is a request-local graph
#: that exists only for the life of one call - so wiring it in means giving the
#: runner a closure it can carry, not calling the existing entry point with
#: different arguments. That is real work and it is not this branch's.
#:
#: Reported as unavailable WITH the reason, never omitted and never softened
#: into robustness. "No sensitivity reported" and "not sensitive" are opposite
#: claims and a reader cannot tell them apart from an absent field.
SENSITIVITY_UNAVAILABLE_REASON = (
    "Topology sensitivity for request-local partial-link closures is not "
    "implemented in this foundation. This is NOT a finding that the span is "
    "topology-robust: the question has not been asked."
)

#: The complete set of headlines this analysis may produce. Nothing else is
#: ever assembled at a call site - the same discipline `detourv2.HEADLINES`
#: enforces, and for the same reason: a headline is the sentence a reader
#: quotes, so it must not be improvised.
HEADLINES = (
    "Replacement route found",
    "No replacement route in the represented network",
    "Replacement route found in one direction only",
    "Analysis unresolved",
)

#: Statuses that mean the search did not conclude. Never a finding about a road.
UNRESOLVED_STATUSES = frozenset({
    "UNRESOLVED_TIMEOUT", "API_ERROR", "INVALID_GRAPH", "SOURCE_DATA_ERROR",
    "UNSUPPORTED_PROFILE", "TURN_RESTRICTION_UNSUPPORTED",
})

Direction = Literal["a_to_b", "b_to_a"]


@dataclass(frozen=True)
class HandleRef:
    """A handle as it is stored, shared and restored: a linear reference.

    Not a click. A click is an event; this is a position, and a permalink that
    carried the click would re-snap it against whatever the map looked like
    when it was reopened.
    """

    link_id: int
    fraction: float


@dataclass
class DirectedMeasure:
    """One direction's replacement path, and what it cost."""

    direction: Direction
    status: str
    replacement_distance_m: float | None = None
    replacement_time_s: float | None = None
    added_distance_m: float | None = None
    ratio: float | None = None
    arc_ids: list[int] = field(default_factory=list)
    detail: str | None = None
    runtime_ms: int = 0

    @property
    def resolved(self) -> bool:
        return self.status not in UNRESOLVED_STATUSES

    @property
    def routed(self) -> bool:
        return self.status == "OK"


@dataclass
class OutageAnalysis:
    """Everything one span request produces."""

    snapshot_id: str
    profile: Profile
    metric: Metric
    direction_mode: DirectionMode

    handle_a: snap.SnapCandidate
    handle_b: snap.SnapCandidate

    corridor: SpanCandidate
    corridor_choice: CorridorChoice
    split: VirtualSplit

    #: The road that is shut, measured along itself.
    closed_length_m: float
    measures: list[DirectedMeasure]

    headline: str
    fingerprint: str
    runtime_ms: int

    #: What the SNAPSHOT was built with, not what this checkout would build.
    #: The two differ whenever a graph-shaping change has landed but the
    #: snapshot predates it, which is a fact about the answer and belongs in
    #: the answer.
    processing_version: str = ""

    #: Always None in this foundation, with the reason carried beside it.
    #: Never softened into a claim of robustness - see the constant.
    sensitivity: None = None
    sensitivity_unavailable_reason: str = SENSITIVITY_UNAVAILABLE_REASON

    #: Always None. See the module docstring - Gu cannot represent half a link.
    isolation: None = None
    isolation_unavailable_reason: str = (
        "Isolation is computed on an undirected graph with one edge per link, "
        "which cannot represent a partial closure. Reporting it would mean "
        "rounding the outage up to whole links, which is the error this "
        "measure exists to remove."
    )
    quality_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> DirectedMeasure | None:
        """The A -> B measure where it exists, else the only one requested."""
        for m in self.measures:
            if m.direction == "a_to_b":
                return m
        return self.measures[0] if self.measures else None


def analyse(
    snapshot_id: str,
    a: HandleRef,
    b: HandleRef,
    *,
    profile: Profile = "car",
    metric: Metric = "distance",
    direction_mode: DirectionMode = DEFAULT_DIRECTION_MODE,
    corridor_id: str | None = None,
    a_alternates: Sequence[HandleRef] = (),
    b_alternates: Sequence[HandleRef] = (),
    statement_timeout_ms: int = 20_000,
) -> OutageAnalysis:
    """Resolve, close and measure a two-point outage.

    `corridor_id` pins which corridor to use. A permalink carries it because
    corridor selection can be genuinely ambiguous, and a link that reopened
    onto a DIFFERENT road than the one it was shared about would be worse than
    one that failed to open: the reader would have no way to tell.
    """
    started = time.perf_counter()

    handle_a = snap.at_position(snapshot_id, a.link_id, fraction=a.fraction)
    handle_b = snap.at_position(snapshot_id, b.link_id, fraction=b.fraction)

    a_options = [HandleOption(a.link_id, a.fraction),
                 *(HandleOption(h.link_id, h.fraction) for h in a_alternates)]
    b_options = [HandleOption(b.link_id, b.fraction),
                 *(HandleOption(h.link_id, h.fraction) for h in b_alternates)]

    choice = span_corridor.select(
        snapshot_id, a_options, b_options, profile=profile,
        statement_timeout_ms=statement_timeout_ms)
    if choice.chosen is None:
        raise NoCorridor(
            "no corridor connects these two handles in the represented network")

    corridor, flags = _pin(choice, corridor_id)

    split = vsplit.build(
        snapshot_id, corridor.intervals,
        handle_a=(corridor.steps[0].link_id,
                  _entry_fraction(corridor.steps[0])),
        handle_b=(corridor.steps[-1].link_id,
                  _exit_fraction(corridor.steps[-1])),
        profile=profile, direction_mode=direction_mode)

    measures = [
        _measure(snapshot_id, split, d, metric, profile, statement_timeout_ms)
        for d in _directions(direction_mode)
    ]

    return OutageAnalysis(
        snapshot_id=snapshot_id,
        profile=profile,
        metric=metric,
        direction_mode=direction_mode,
        handle_a=handle_a,
        handle_b=handle_b,
        corridor=corridor,
        corridor_choice=choice,
        split=split,
        closed_length_m=split.closed_length_m,
        measures=measures,
        headline=_headline(measures),
        fingerprint=fingerprint(snapshot_id, profile, metric, direction_mode,
                                corridor.candidate_id, split.fingerprint),
        runtime_ms=int((time.perf_counter() - started) * 1000),
        processing_version=_processing_version(snapshot_id),
        quality_flags=flags,
    )


def _processing_version(snapshot_id: str) -> str:
    """The processing version the snapshot was actually built with."""
    row = db.query_one(
        "SELECT processing_version FROM network_snapshots WHERE snapshot_id=%s",
        (snapshot_id,))
    return str(row["processing_version"]) if row else ""


class NoCorridor(LookupError):
    """The handles do not describe a stretch of road in this network."""


class UnknownCorridor(LookupError):
    """A pinned corridor id is not among the candidates for these handles."""


def _pin(choice: CorridorChoice,
         corridor_id: str | None) -> tuple[SpanCandidate, list[str]]:
    """Honour a pinned corridor, or refuse - never silently substitute."""
    flags: list[str] = []
    if corridor_id is None:
        if choice.ambiguous:
            # The caller gets the best-evidenced corridor AND is told the
            # choice was not clear-cut. Suppressing the flag here is how an
            # ambiguous result becomes an unqualified one downstream.
            flags.append("CORRIDOR_AMBIGUOUS")
        return choice.chosen, flags

    for candidate in choice.candidates:
        if candidate.candidate_id == corridor_id:
            return candidate, flags
    raise UnknownCorridor(
        f"corridor {corridor_id!r} is not among the candidates for these "
        f"handles; the network or the snapshot may have changed since the "
        f"link was made")


def _directions(mode: DirectionMode) -> list[Direction]:
    if mode == "a_to_b":
        return ["a_to_b"]
    if mode == "b_to_a":
        return ["b_to_a"]
    return ["a_to_b", "b_to_a"]


def _entry_fraction(step: span_corridor.SpanStep) -> float:
    """Where handle A sits on the corridor's first link."""
    return (step.from_fraction if step.traversal == "forward"
            else step.to_fraction)


def _exit_fraction(step: span_corridor.SpanStep) -> float:
    """Where handle B sits on the corridor's last link."""
    return (step.to_fraction if step.traversal == "forward"
            else step.from_fraction)


def _measure(snapshot_id: str, split: VirtualSplit, direction: Direction,
             metric: Metric, profile: Profile,
             timeout_ms: int) -> DirectedMeasure:
    """One direction: route round the closure and compare with going through."""
    u, v = ((split.node_at_a, split.node_at_b) if direction == "a_to_b"
            else (split.node_at_b, split.node_at_a))

    result: RouteResult = route(
        snapshot_id, u, v, metric=metric, profile=profile,
        excluded_arcs=split.excluded_arc_ids, overlay=split.overlay,
        statement_timeout_ms=timeout_ms)

    measure = DirectedMeasure(
        direction=direction, status=result.status, detail=result.detail,
        runtime_ms=result.runtime_ms, arc_ids=list(result.arc_ids))
    if result.status != "OK":
        return measure

    measure.replacement_distance_m = result.distance_m
    measure.replacement_time_s = result.time_s
    if split.closed_length_m > 0 and result.distance_m is not None:
        measure.added_distance_m = result.distance_m - split.closed_length_m
        measure.ratio = result.distance_m / split.closed_length_m
    return measure


def _headline(measures: Sequence[DirectedMeasure]) -> str:
    """One sentence from the fixed vocabulary, never assembled at a call site."""
    if not measures or all(not m.resolved for m in measures):
        return "Analysis unresolved"
    resolved = [m for m in measures if m.resolved]
    routed = [m for m in resolved if m.routed]
    if not routed:
        return "No replacement route in the represented network"
    if len(routed) < len(measures):
        # Either a direction failed to route, or one did not resolve at all.
        # Both mean the same thing to a reader: do not read this as a full
        # answer for both ways.
        return "Replacement route found in one direction only"
    return "Replacement route found"


def route_geometry(snapshot_id: str, arc_ids: Sequence[int],
                   overlay: VirtualOverlay) -> dict:
    """The replacement path as drawable geometry, virtual pieces included.

    A route reported only as a number cannot be checked. The pieces at either
    end of an outage are exactly the parts a reader looks at - the run back out
    to the junction - so omitting them would leave the detour starting in mid
    air on the map.
    """
    pieces = overlay.by_id()
    real_ids = [a for a in arc_ids if a not in pieces]

    real_geom: dict[int, dict] = {}
    if real_ids:
        for r in db.query(
            """
            SELECT a.arc_id, a.direction,
                   ST_AsGeoJSON(ST_Transform(l.geom_2193, 4326))::json AS geojson
              FROM arcs a
              JOIN links l ON l.snapshot_id = a.snapshot_id
                          AND l.link_id = a.link_id
             WHERE a.snapshot_id = %s AND a.arc_id = ANY(%s)
            """, (snapshot_id, real_ids),
        ):
            real_geom[int(r["arc_id"])] = r

    piece_geom: dict[int, dict] = {}
    wanted = [a for a in arc_ids if a in pieces]
    if wanted:
        for arc_id in wanted:
            p = pieces[arc_id]
            row = db.query_one(
                """
                SELECT ST_AsGeoJSON(ST_Transform(
                         ST_LineSubstring(geom_2193, %s, %s), 4326))::json
                       AS geojson
                  FROM links WHERE snapshot_id=%s AND link_id=%s
                """,
                (min(p.from_fraction, p.to_fraction),
                 max(p.from_fraction, p.to_fraction), snapshot_id, p.link_id),
            )
            if row is not None:
                piece_geom[arc_id] = row

    features = []
    for order, arc_id in enumerate(arc_ids):
        row = piece_geom.get(arc_id) or real_geom.get(arc_id)
        if row is None:
            # Every arc of a path must be drawable, or the line has a hole in
            # it that nothing announces. Same reasoning as `routing._summarise`
            # raising on an arc it cannot account for.
            raise KeyError(
                f"arc {arc_id} in the replacement path has no geometry")
        features.append({
            "type": "Feature",
            "geometry": row["geojson"],
            "properties": {"arcId": arc_id, "order": order,
                           "virtual": arc_id in pieces},
        })
    return {"type": "FeatureCollection", "features": features}


def fingerprint(snapshot_id: str, profile: str, metric: str,
                direction_mode: str, corridor_id: str,
                split_fingerprint: str) -> str:
    """Deterministic identity of a whole request.

    Includes the corridor, because two spans that close the same road via
    different corridors are different questions, and the split fingerprint,
    because that is what identifies the road actually removed.
    """
    payload = "|".join((
        "outage-analysis", ANALYSIS_VERSION, snapshot_id, profile, metric,
        direction_mode, corridor_id, split_fingerprint,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def measure_as_dict(m: DirectedMeasure) -> dict:
    return {
        "direction": m.direction,
        "status": m.status,
        "resolved": m.resolved,
        "replacementDistanceM": (None if m.replacement_distance_m is None
                                 else round(m.replacement_distance_m, 1)),
        "replacementTimeS": (None if m.replacement_time_s is None
                             else round(m.replacement_time_s, 1)),
        "addedDistanceM": (None if m.added_distance_m is None
                           else round(m.added_distance_m, 1)),
        "ratio": None if m.ratio is None else round(m.ratio, 3),
        "detail": m.detail,
        "runtimeMs": m.runtime_ms,
    }


def as_dict(a: OutageAnalysis, *, with_geometry: bool = False) -> dict:
    payload = {
        "snapshotId": a.snapshot_id,
        # Same keys, same meanings, as the production boundary-analysis
        # payload. A client that already reads one should not have to learn a
        # second vocabulary to read the other.
        "engine": ENGINE,
        "algorithm": ALGORITHM,
        "algorithmVersion": ANALYSIS_VERSION,
        "stability": STABILITY,
        "processingVersion": a.processing_version,
        "codeProcessingVersion": config.PROCESSING_VERSION,
        # V1 is retired from the product and is never called here. Stated
        # rather than implied, on the same terms the V2 endpoints state it.
        "comparableToV1": False,
        "comparableToV1Detail": (
            "V1 is retired from the product and is not consulted by this "
            "engine under any condition. There is no fallback path."
        ),
        "request": {"metric": a.metric, "vehicle": a.profile,
                    "direction": a.direction_mode},
        "profile": a.profile,
        "metric": a.metric,
        "directionMode": a.direction_mode,
        "handleA": snap.candidate_as_dict(a.handle_a),
        "handleB": snap.candidate_as_dict(a.handle_b),
        "corridor": span_corridor.candidate_as_dict(a.corridor),
        "corridorCandidates": [span_corridor.candidate_as_dict(c)
                               for c in a.corridor_choice.candidates],
        "corridorAmbiguous": a.corridor_choice.ambiguous,
        "corridorAmbiguityReason": a.corridor_choice.ambiguity_reason,
        "closure": vsplit.as_dict(a.split),
        "closedLengthM": round(a.closed_length_m, 1),
        "measures": [measure_as_dict(m) for m in a.measures],
        "headline": a.headline,
        "isolation": a.isolation,
        "isolationUnavailableReason": a.isolation_unavailable_reason,
        # Structurally separate from the canonical answer, exactly as the
        # production topology-sensitivity endpoint is: null here, never a
        # counterfactual folded into the result the client draws.
        "sensitivity": a.sensitivity,
        "sensitivityUnavailableReason": a.sensitivity_unavailable_reason,
        "isSeparateFromCanonical": True,
        "canonicalRouteSlot": (
            "The replacement path in `measures` is the canonical answer. No "
            "counterfactual route is returned anywhere in this response, and "
            "no route is drawn for a measure that did not resolve."
        ),
        "qualityFlags": a.quality_flags,
        "fingerprint": a.fingerprint,
        "algorithm": ALGORITHM,
        "analysisVersion": ANALYSIS_VERSION,
        "runtimeMs": a.runtime_ms,
        # Carried, not left to the reader to remember.
        "measurementCaveat": (
            "This is a structural measure on the represented network. It "
            "computes one shortest path and says nothing about how much "
            "traffic uses either route."
        ),
        "permalink": permalink_state(a),
    }
    if with_geometry:
        payload["closureGeometry"] = vsplit.span_geometry(
            a.snapshot_id, a.split.intervals)
        payload["replacementGeometry"] = {
            m.direction: route_geometry(a.snapshot_id, m.arc_ids,
                                        a.split.overlay)
            for m in a.measures if m.routed
        }
    return payload


def permalink_state(a: OutageAnalysis) -> dict:
    """Everything needed to restore this exact span, and nothing else.

    Positions rather than clicks, and the corridor id rather than a promise
    that the ranking will come out the same way. Restoring must reproduce the
    span that was shared or say it cannot - never quietly close a different
    road.
    """
    return {
        "snapshotId": a.snapshot_id,
        "aLinkId": a.handle_a.link_id,
        "aFraction": round(a.handle_a.fraction, 9),
        "bLinkId": a.handle_b.link_id,
        "bFraction": round(a.handle_b.fraction, 9),
        "corridorId": a.corridor.candidate_id,
        "directionMode": a.direction_mode,
        "profile": a.profile,
        "metric": a.metric,
    }
