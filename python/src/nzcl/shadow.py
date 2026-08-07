"""Run V1 and V2 over the same closure and record every way they differ.

V2 does not earn the default by being newer. It earns it by being shown, case
by case, to differ from V1 only where V1 was wrong. This module is where that
evidence is produced and stored.

The comparison is only meaningful when both engines are answering the same
question, which means `scope='source_feature'` - V1 has no other behaviour.
Under `scope='segment'` the two engines close different amounts of road, and
every difference would be explained by scope rather than by engine. The
comparison still runs, because seeing the size of the scope effect is useful,
but the row records `scope_comparable=False` and nothing should read an engine
verdict off it.

Five differences are reported, and they are kept apart on purpose:

  classification  the headline changed - the sentence a reader acts on
  metric          a distance or time changed
  closure_set     the two engines removed different links
  isolation       what is reported cut off changed
  runtime         how long each took
"""

from __future__ import annotations

import json
import time
from typing import Literal

from . import db, detourv2
from .config import ALGORITHM_VERSION as V1_VERSION
from .detour import compute as v1_compute
from .detourv2 import ALGORITHM_VERSION as V2_VERSION
from .routing import Metric, Profile

Scope = Literal["segment", "direction", "source_feature"]

#: V1 scope names, which are not V2's. Mapping them here rather than at the
#: call site keeps the two vocabularies from leaking into each other.
_V1_SCOPE = {"source_feature": "physical", "segment": "physical",
             "direction": "directed"}


def _v1_wording(direction_result) -> str:
    """The sentence V1's client shows, reconstructed from V1's own fields.

    V1 has no wording vocabulary - the interface assembles a headline from the
    status and the isolation block. This reproduces that so the classification
    difference compares what a reader actually sees, not two enum values that
    were never shown to anyone.
    """
    if direction_result is None:
        return "not computed"
    if direction_result.status == "OK":
        return "Through route found"
    if direction_result.status != "DISCONNECTED":
        return "Analysis unresolved"
    iso = direction_result.isolation
    if iso is not None and iso.pocket_link_count > 0:
        return "Road cut off"
    return "No replacement path"


def compare(snapshot_id: str, link_id: int, *, scope: Scope = "source_feature",
            direction: str = "both", metric: Metric = "distance",
            profile: Profile = "car", persist: bool = True) -> dict:
    """Run both engines and return the difference record."""
    v1_scope = _V1_SCOPE[scope]
    directions = None if direction == "both" else [direction]

    t0 = time.perf_counter()
    v1 = v1_compute(snapshot_id, link_id, metric=metric, profile=profile,
                    closure_scope=v1_scope, directions=directions)
    v1_ms = int((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    v2 = detourv2.analyse(snapshot_id, link_id, scope=scope,
                          direction=direction, metric=metric, profile=profile,
                          use_cache=False)
    v2_ms = int((time.perf_counter() - t1) * 1000)

    # Compare the direction the caller asked about; for 'both', prefer a
    # direction both engines actually computed.
    d = direction if direction in ("forward", "reverse") else (
        "forward" if v1.forward is not None and v2.forward is not None
        else "reverse")
    v1d = getattr(v1, d, None)
    v2d = getattr(v2, d, None)

    v1_closure_len = _v1_closure_length(snapshot_id, v1.removed_link_ids)
    v1_iso = v1d.isolation if v1d is not None else None

    v1_word = _v1_wording(v1d)
    v2_word = v2d.headline if v2d is not None else v2.headline

    kinds: list[str] = []
    if v1_word != v2_word:
        kinds.append("classification")
    if sorted(v1.removed_link_ids) != sorted(v2.closure.removed_link_ids):
        kinds.append("closure_set")

    v1_alt = v1d.alternative_distance_m if v1d else None
    v2_alt = v2d.alternative_distance_m if v2d else None
    delta = None
    if v1_alt is not None and v2_alt is not None:
        delta = v2_alt - v1_alt
        if abs(delta) > 0.05:
            kinds.append("metric")
    elif (v1_alt is None) != (v2_alt is None):
        kinds.append("metric")

    v1_iso_links = v1_iso.pocket_link_count if v1_iso else 0
    v1_iso_len = v1_iso.pocket_length_m if v1_iso else 0.0
    if (v1_iso_links != v2.isolation.separated_link_count
            or abs(v1_iso_len - v2.isolation.separated_length_m) > 0.05):
        kinds.append("isolation")
    if not kinds:
        kinds.append("none")

    detail = {
        "scopeComparable": scope == "source_feature",
        "directionCompared": d,
        "v1": {
            "status": v1d.status if v1d else None,
            "closureScope": v1_scope,
            "removedLinkIds": v1.removed_link_ids,
            "isolationSide": v1_iso.side if v1_iso else None,
            "isolationExactClaim": v1_iso.exact if v1_iso else None,
            "isolationMethod": "directed bounded reachability, smaller side",
        },
        "v2": {
            "status": v2d.status if v2d else None,
            "scope": v2.scope,
            "removedLinkIds": v2.closure.removed_link_ids,
            # Two claims where V1 published one. V1's `exact` is a claim about
            # a bounded directed walk; V2 separates "the partition was computed
            # exactly" from "the graph models the real network", and only the
            # first is ever true.
            "isolationCalculationExact": v2.isolation.calculation_exact,
            "isolationGraphExact": v2.isolation.graph_exact,
            "topologyConfidence": v2.isolation.topology_confidence,
            "principalSideAmbiguous": v2.isolation.principal_side_ambiguous,
            "isolationMethod": v2.isolation.method,
            "closureIsBridge": v2.isolation.closure_is_bridge,
            "resultingComponentCount": len(v2.isolation.components),
            "fingerprint": v2.closure.fingerprint,
        },
        "notes": _notes(v1_iso, v2),
    }

    row = {
        "snapshotId": snapshot_id,
        "linkId": link_id,
        "closureScope": scope,
        "vehicleProfile": profile,
        "metric": metric,
        "direction": direction,
        "v1AlgorithmVersion": V1_VERSION,
        "v2AlgorithmVersion": V2_VERSION,
        "v1Status": v1d.status if v1d else None,
        "v2Status": v2d.status if v2d else None,
        "differenceKinds": kinds,
        "v1RemovedLinkCount": len(v1.removed_link_ids),
        "v2RemovedLinkCount": len(v2.closure.removed_link_ids),
        "v1ClosureLengthM": v1_closure_len,
        "v2ClosureLengthM": v2.closure.total_closure_length_m,
        "v1AlternativeM": v1_alt,
        "v2AlternativeM": v2_alt,
        "metricDeltaM": delta,
        "v1IsolationLinkCount": v1_iso_links,
        "v2IsolationLinkCount": v2.isolation.separated_link_count,
        "v1IsolationLengthM": v1_iso_len,
        "v2IsolationLengthM": v2.isolation.separated_length_m,
        "v1Wording": v1_word,
        "v2Wording": v2_word,
        "v1RuntimeMs": v1_ms,
        "v2RuntimeMs": v2_ms,
        "runtimeDeltaMs": v2_ms - v1_ms,
        "detail": detail,
    }
    if persist:
        _persist(row)
    return row


def _notes(v1_iso, v2) -> list[str]:
    """Plain statements of what a difference means. No adjectives."""
    out: list[str] = []
    if v1_iso is not None and v1_iso.pocket_link_count > 0 and not v2.isolation.physically_isolates:
        out.append(
            "V1 reported a stranded pocket where V2 finds nothing separated "
            "from the principal connection. V1's pocket is a directed "
            "reachable set, not a component of the undirected graph.")
    if (v2.isolation.physically_isolates and v1_iso is not None
            and v2.isolation.separated_link_count > v1_iso.pocket_link_count):
        out.append(
            f"V2 finds {v2.isolation.separated_link_count} links separated "
            f"across {len(v2.isolation.components)} components; V1 reported "
            f"{v1_iso.pocket_link_count} because it takes the smaller of two "
            f"directed reachable sets and reports only that one.")
    if v2.closure.scope == "segment" and v2.closure.removed_link_count == 1:
        out.append(
            "Scope differs by construction: V1 closed the whole AMDS source "
            "feature, V2 closed only the selected segment. Differences below "
            "are explained by scope, not by engine.")
    return out


def _v1_closure_length(snapshot_id: str, link_ids: list[int]) -> float:
    if not link_ids:
        return 0.0
    row = db.query_one(
        "SELECT sum(length_m) AS m FROM links WHERE snapshot_id=%s "
        "AND link_id = ANY(%s)", (snapshot_id, link_ids))
    return float(row["m"] or 0.0)


def _persist(row: dict) -> None:
    db.execute(
        """
        INSERT INTO closure_shadow_comparisons (
            snapshot_id, link_id, closure_scope, vehicle_profile, metric,
            direction, v1_algorithm_version, v2_algorithm_version,
            v1_status, v2_status, difference_kinds,
            v1_removed_link_count, v2_removed_link_count,
            v1_closure_length_m, v2_closure_length_m,
            v1_alternative_m, v2_alternative_m, metric_delta_m,
            v1_isolation_link_count, v2_isolation_link_count,
            v1_isolation_length_m, v2_isolation_length_m,
            v1_wording, v2_wording, v1_runtime_ms, v2_runtime_ms, detail)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (snapshot_id, link_id, closure_scope, vehicle_profile,
                     metric, direction, v1_algorithm_version,
                     v2_algorithm_version)
        DO UPDATE SET
            v1_status = EXCLUDED.v1_status, v2_status = EXCLUDED.v2_status,
            difference_kinds = EXCLUDED.difference_kinds,
            v1_removed_link_count = EXCLUDED.v1_removed_link_count,
            v2_removed_link_count = EXCLUDED.v2_removed_link_count,
            v1_closure_length_m = EXCLUDED.v1_closure_length_m,
            v2_closure_length_m = EXCLUDED.v2_closure_length_m,
            v1_alternative_m = EXCLUDED.v1_alternative_m,
            v2_alternative_m = EXCLUDED.v2_alternative_m,
            metric_delta_m = EXCLUDED.metric_delta_m,
            v1_isolation_link_count = EXCLUDED.v1_isolation_link_count,
            v2_isolation_link_count = EXCLUDED.v2_isolation_link_count,
            v1_isolation_length_m = EXCLUDED.v1_isolation_length_m,
            v2_isolation_length_m = EXCLUDED.v2_isolation_length_m,
            v1_wording = EXCLUDED.v1_wording, v2_wording = EXCLUDED.v2_wording,
            v1_runtime_ms = EXCLUDED.v1_runtime_ms,
            v2_runtime_ms = EXCLUDED.v2_runtime_ms,
            detail = EXCLUDED.detail, compared_at_utc = now()
        """,
        (row["snapshotId"], row["linkId"], row["closureScope"],
         row["vehicleProfile"], row["metric"], row["direction"],
         row["v1AlgorithmVersion"], row["v2AlgorithmVersion"],
         row["v1Status"], row["v2Status"], row["differenceKinds"],
         row["v1RemovedLinkCount"], row["v2RemovedLinkCount"],
         row["v1ClosureLengthM"], row["v2ClosureLengthM"],
         row["v1AlternativeM"], row["v2AlternativeM"], row["metricDeltaM"],
         row["v1IsolationLinkCount"], row["v2IsolationLinkCount"],
         row["v1IsolationLengthM"], row["v2IsolationLengthM"],
         row["v1Wording"], row["v2Wording"],
         row["v1RuntimeMs"], row["v2RuntimeMs"], json.dumps(row["detail"])))
