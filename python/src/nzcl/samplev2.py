"""A deterministic stratified sample, and what the three measures say about it.

    python -m nzcl.samplev2 run [snapshotId] [size] [outputDir]

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is a SAMPLE. Five hundred links out of 375,696 chosen to span the situations
the engine has to handle - both islands, every road-controlling authority,
state highway and local, urban and rural, one-way and two-way, bridge and not,
short and long source features, single and multi-child groups.

It is NOT a national estimate and nothing in the output may be read as one. The
sample is deliberately stratified to over-represent awkward cases, which is
exactly what makes it useful for finding defects and exactly what makes any
percentage taken from it meaningless as a description of the country. Every
proportion this module prints is a proportion OF THE SAMPLE and is labelled so.

No human has reviewed these results. The review pack is a list of the largest
disagreements for someone to look at; it is not evidence that anyone has.

THREE MEASURES, COMPARED
------------------------
    v1          the shipped engine: whole AMDS source feature, endpoint measure
    endpoint    V2 `detourv2`: explicit scope, still the endpoint measure
    boundary    V2 `impactv2`: explicit scope, through-movement measure

v1 and `endpoint` are compared under `source_feature` scope, because that is
the only scope under which they ask the same question. `boundary` asks a
DIFFERENT question and a difference from it is not evidence that either other
engine is wrong - it is the point of the change. The output says so on every
row rather than trusting the reader to remember.

DETERMINISM
-----------
Selection is by md5 of (snapshot, seed, link id) within each stratum. It does
not depend on row order, on the query plan, or on anything the database chose
to return first, so the same command reproduces the same sample on any machine.
The replay digest at the end covers the selected ids AND every reported figure,
so a rerun that differs anywhere says so in one line.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import db, detourv2, impactv2, physical
from .detour import compute as v1_compute

#: Changing this changes which links are drawn. It is recorded in the output.
SAMPLE_SEED = "detour-v2-pr2-boundary-sample-1"

#: Cook Strait, roughly. Only ever used to label a row, never to compute one.
NORTH_ISLAND_LAT = -41.45

TARGET_SIZE = 500

#: Coverage the brief requires. Each is a predicate over the frame row; the
#: allocator guarantees a floor for every one that the network can supply, then
#: spreads whatever is left across the road-controlling authorities.
CELL_FLOOR = 8


def _frame(snapshot_id: str) -> list[dict]:
    """Every candidate link with the attributes the strata are built from.

    One query. `physical_access_links` supplies `is_bridge` from the Gu
    precompute rather than recomputing it here - that is the exact undirected
    answer and there is no reason for a second opinion.
    """
    return db.query(
        """
        SELECT l.link_id, l.amds_id, l.closure_group_id, l.length_m,
               l.rca_code, l.rca_name, l.urban_rural, l.oneway,
               ST_Y(ST_Transform(ST_PointOnSurface(l.geom_2193), 4326)) AS lat,
               coalesce(pa.is_bridge, false) AS is_bridge,
               g.child_count, g.group_length_m,
               dn.name_status, dn.display_name
          FROM links l
          LEFT JOIN physical_access_links pa
                 ON pa.snapshot_id = l.snapshot_id AND pa.link_id = l.link_id
                AND pa.profile = 'car'
          LEFT JOIN link_display_names dn
                 ON dn.snapshot_id = l.snapshot_id AND dn.link_id = l.link_id
          JOIN (SELECT snapshot_id, closure_group_id, count(*) AS child_count,
                       sum(length_m) AS group_length_m
                  FROM links WHERE snapshot_id = %s GROUP BY 1, 2) g
            ON g.snapshot_id = l.snapshot_id
           AND g.closure_group_id = l.closure_group_id
         WHERE l.snapshot_id = %s AND l.in_analysis_area
           AND l.mode_vehicle AND l.source_node <> l.target_node
        """,
        (snapshot_id, snapshot_id))


def _label(r: dict) -> dict[str, str]:
    """The stratum labels for one link. Pure function of the row."""
    lat = r["lat"]
    length = float(r["group_length_m"] or 0.0)
    return {
        "island": ("north" if lat is not None and lat > NORTH_ISLAND_LAT
                   else "south"),
        "rca": r["rca_name"] or "unknown authority",
        "road_class": "state_highway" if r["rca_code"] == 1 else "local",
        "urban_rural": r["urban_rural"] or "unknown",
        "direction": "one_way" if r["oneway"] == 1 else "two_way",
        "bridge": "bridge" if r["is_bridge"] else "not_bridge",
        "feature_size": ("short" if length < 500 else
                         "long" if length > 2000 else "medium"),
        "children": ("single_child" if (r["child_count"] or 1) <= 1
                     else "multi_child"),
        "naming": ("named" if (r["name_status"] or "") not in
                   ("", "unresolved") else "unresolved"),
    }


def _rank(snapshot_id: str, link_id: int) -> str:
    """Deterministic ordering key. A hash of identifiers, nothing else."""
    return hashlib.md5(
        f"{SAMPLE_SEED}|{snapshot_id}|{int(link_id)}".encode()).hexdigest()


#: The cells the brief names, as (dimension, value) pairs. Every one that the
#: network can supply must appear in the sample.
REQUIRED_CELLS = [
    ("island", "north"), ("island", "south"),
    ("road_class", "state_highway"), ("road_class", "local"),
    ("urban_rural", "urban"), ("urban_rural", "rural"),
    ("direction", "one_way"), ("direction", "two_way"),
    ("bridge", "bridge"), ("bridge", "not_bridge"),
    ("feature_size", "short"), ("feature_size", "medium"),
    ("feature_size", "long"),
    ("children", "single_child"), ("children", "multi_child"),
    ("naming", "named"), ("naming", "unresolved"),
]


def select(snapshot_id: str, size: int = TARGET_SIZE) -> tuple[list[dict], dict]:
    """Draw the sample. Same inputs, same links, on any machine."""
    frame = _frame(snapshot_id)
    for r in frame:
        r["_strata"] = _label(r)
        r["_rank"] = _rank(snapshot_id, r["link_id"])
    frame.sort(key=lambda r: r["_rank"])

    chosen: dict[int, dict] = {}

    # 1. Floors for every required cell.
    for dim, val in REQUIRED_CELLS:
        have = sum(1 for r in chosen.values() if r["_strata"][dim] == val)
        for r in frame:
            if have >= CELL_FLOOR:
                break
            if r["link_id"] in chosen or r["_strata"][dim] != val:
                continue
            chosen[r["link_id"]] = r
            have += 1

    # 2. Every road-controlling authority present, so "every major region" is
    #    a fact about the sample and not a hope about the random draw.
    by_rca: dict[str, list[dict]] = defaultdict(list)
    for r in frame:
        by_rca[r["_strata"]["rca"]].append(r)
    for rca in sorted(by_rca):
        if any(r["_strata"]["rca"] == rca for r in chosen.values()):
            continue
        for r in by_rca[rca]:
            if r["link_id"] not in chosen:
                chosen[r["link_id"]] = r
                break

    # 3. Top up, round-robin across authorities so no one region dominates.
    order = sorted(by_rca)
    cursor = {k: 0 for k in order}
    while len(chosen) < size:
        progressed = False
        for rca in order:
            if len(chosen) >= size:
                break
            bucket = by_rca[rca]
            while cursor[rca] < len(bucket):
                r = bucket[cursor[rca]]
                cursor[rca] += 1
                if r["link_id"] not in chosen:
                    chosen[r["link_id"]] = r
                    progressed = True
                    break
        if not progressed:
            break

    sample = sorted(chosen.values(), key=lambda r: r["_rank"])
    coverage = {
        f"{dim}={val}": sum(1 for r in sample if r["_strata"][dim] == val)
        for dim, val in REQUIRED_CELLS
    }
    coverage["distinct_authorities"] = len(
        {r["_strata"]["rca"] for r in sample})
    coverage["frame_size"] = len(frame)
    return sample, coverage


# ------------------------------------------------------------------ compare
def _v1_wording(d) -> str:
    """The sentence V1's client shows, rebuilt from V1's own fields."""
    if d is None:
        return "not computed"
    if d.status == "OK":
        return "Through route found"
    if d.status != "DISCONNECTED":
        return "Analysis unresolved"
    iso = getattr(d, "isolation", None)
    if iso is not None and getattr(iso, "pocket_link_count", 0) > 0:
        return "Road cut off"
    return "No endpoint route"


def compare_one(snapshot_id: str, link_id: int) -> dict[str, Any]:
    """Run all three measures over one link and record every difference."""
    row: dict[str, Any] = {"linkId": int(link_id)}

    t = time.perf_counter()
    try:
        v1 = v1_compute(snapshot_id, link_id, closure_scope="physical")
        v1_dir = (v1.forward or v1.reverse) if hasattr(v1, "forward") else None
        row["v1"] = {
            "wording": _v1_wording(v1_dir),
            "status": getattr(v1_dir, "status", None),
            "alternativeDistanceM": getattr(v1_dir, "alternative_distance_m", None),
            "normalDistanceM": getattr(v1_dir, "normal_path_distance_m", None),
            "removedLinkCount": len(getattr(v1, "removed_link_ids", []) or []),
        }
    except Exception as exc:  # noqa: BLE001
        row["v1"] = {"error": f"{type(exc).__name__}: {exc}"}
    row["v1Ms"] = int((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    try:
        ep = detourv2.analyse(snapshot_id, link_id, scope="source_feature",
                              use_cache=False)
        d = ep.forward or ep.reverse
        row["endpoint"] = {
            "headline": ep.headline,
            "isolationStatement": ep.isolation_statement,
            "status": getattr(d, "status", None),
            "alternativeDistanceM": getattr(d, "alternative_distance_m", None),
            "normalDistanceM": getattr(d, "normal_path_distance_m", None),
            "networkPenaltyM": getattr(d, "network_penalty_m", None),
            "removedLinkCount": ep.closure.removed_link_count,
            "physicallyIsolates": ep.isolation.physically_isolates,
            "separatedLinkCount": ep.isolation.separated_link_count,
            "topologyConfidence": ep.isolation.topology_confidence,
        }
    except Exception as exc:  # noqa: BLE001
        row["endpoint"] = {"error": f"{type(exc).__name__}: {exc}"}
    row["endpointMs"] = int((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    try:
        b = impactv2.analyse(snapshot_id, link_id, scope="source_feature",
                             with_geometry=True)
        p = b.principal
        gaps = sum(1 for g in (b.replacement_geometry, b.intact_geometry,
                               b.selected_geometry, b.closure_geometry)
                   if g is not None and g.has_gaps)
        chosen_up = chosen_down = None
        if b.corridor is not None and b.corridor.chosen is not None:
            chosen_up = round(b.corridor.chosen.upstream_outward_m, 1)
            chosen_down = round(b.corridor.chosen.downstream_outward_m, 1)
        row["boundary"] = {
            "headline": b.headline,
            "status": None if p is None else p.status,
            "intactDistanceM": None if p is None else p.intact_distance_m,
            "replacementDistanceM": None if p is None else p.replacement_distance_m,
            "networkPenaltyM": None if p is None else p.network_penalty_m,
            "ratio": None if p is None else p.ratio,
            "movementsConsidered": b.movement_set.candidate_pairs,
            "movementsIncluded": len(b.movement_set.included),
            "movementsTruncated": b.movement_set.truncated,
            "removedLinkCount": b.closure.removed_link_count,
            "entryPorts": len(b.boundary.entry_ports),
            "exitPorts": len(b.boundary.exit_ports),
            "reducesToEndpoints": b.boundary.reduces_to_endpoints,
            "corridorUpstreamOutwardM": chosen_up,
            "corridorDownstreamOutwardM": chosen_down,
            "corridorAdmissibility": (None if b.corridor is None
                                      else b.corridor.admissibility_level),
            "corridorTruncated": (None if b.corridor is None
                                  else b.corridor.truncated),
            "geometryGapped": gaps > 0,
            "physicallyIsolates": (None if b.isolation is None
                                   else b.isolation.physically_isolates),
            "topologyConfidence": (None if b.isolation is None
                                   else b.isolation.topology_confidence),
            "qualityFlags": b.quality_flags,
            "stageMs": b.stage_ms,
        }
    except Exception as exc:  # noqa: BLE001
        row["boundary"] = {"error": f"{type(exc).__name__}: {exc}"}
    row["boundaryMs"] = int((time.perf_counter() - t) * 1000)

    row["differences"] = _differences(row)
    return row


def _differences(row: dict) -> dict:
    v1, ep, b = row.get("v1", {}), row.get("endpoint", {}), row.get("boundary", {})
    out: dict[str, Any] = {}

    # v1 vs endpoint: SAME question, so a difference here is an engine
    # difference and is worth acting on.
    out["classificationChanged"] = (
        v1.get("wording") != ep.get("headline")
        if "error" not in v1 and "error" not in ep else None)
    out["closureSetChanged"] = (
        v1.get("removedLinkCount") != ep.get("removedLinkCount")
        if "error" not in v1 and "error" not in ep else None)

    # endpoint vs boundary: DIFFERENT questions. Recorded, never scored.
    out["endpointVsBoundaryStatus"] = (
        f"{ep.get('status')} -> {b.get('status')}"
        if "error" not in ep and "error" not in b else None)
    a, c = ep.get("networkPenaltyM"), b.get("networkPenaltyM")
    out["penaltyDeltaM"] = (
        None if a is None or c is None else round(c - a, 1))
    out["isolationChanged"] = (
        None if ep.get("physicallyIsolates") is None
        or b.get("physicallyIsolates") is None
        else ep["physicallyIsolates"] != b["physicallyIsolates"])
    out["endpointSaysDisconnectedBoundaryDiverts"] = (
        ep.get("status") == "DISCONNECTED" and b.get("status") == "OK")
    out["endpointDivertsBoundarySaysDisconnected"] = (
        ep.get("status") == "OK" and b.get("status") == "DISCONNECTED")
    return out


# -------------------------------------------------------------------- report
def _pct(values, p):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(p / 100 * len(s)))], 1)


def summarise(rows: list[dict], coverage: dict, snapshot_id: str) -> dict:
    n = len(rows)
    stage_times: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for k, v in (r.get("boundary", {}).get("stageMs") or {}).items():
            stage_times[k].append(float(v))

    unresolved = sum(1 for r in rows
                     if r.get("boundary", {}).get("headline") == "Analysis unresolved")
    errored = sum(1 for r in rows if "error" in r.get("boundary", {}))
    gapped = sum(1 for r in rows if r.get("boundary", {}).get("geometryGapped"))
    truncated = sum(1 for r in rows
                    if r.get("boundary", {}).get("movementsTruncated"))

    penalties = [r["differences"]["penaltyDeltaM"] for r in rows
                 if r["differences"].get("penaltyDeltaM") is not None]
    corridor_up = [r["boundary"]["corridorUpstreamOutwardM"] for r in rows
                   if r.get("boundary", {}).get("corridorUpstreamOutwardM")
                   is not None]

    return {
        "snapshotId": snapshot_id,
        "sampleSeed": SAMPLE_SEED,
        "sampleSize": n,
        "isNationalEstimate": False,
        "note": ("A stratified sample chosen to span awkward cases. Every "
                 "proportion below is a proportion OF THIS SAMPLE and is not "
                 "an estimate of the national network. No human has reviewed "
                 "these results."),
        "coverage": coverage,
        "v1VsEndpoint": {
            "classificationChanged": sum(
                1 for r in rows if r["differences"].get("classificationChanged")),
            "closureSetChanged": sum(
                1 for r in rows if r["differences"].get("closureSetChanged")),
            "v1Wordings": dict(Counter(
                r.get("v1", {}).get("wording", "error") for r in rows)),
            "endpointHeadlines": dict(Counter(
                r.get("endpoint", {}).get("headline", "error") for r in rows)),
        },
        "endpointVsBoundary": {
            "note": ("These two measure DIFFERENT quantities. A difference is "
                     "not evidence that either is wrong."),
            "boundaryHeadlines": dict(Counter(
                r.get("boundary", {}).get("headline", "error") for r in rows)),
            "statusTransitions": dict(Counter(
                r["differences"].get("endpointVsBoundaryStatus") for r in rows)),
            "endpointDisconnectedButBoundaryDiverts": sum(
                1 for r in rows
                if r["differences"].get("endpointSaysDisconnectedBoundaryDiverts")),
            "endpointDivertsButBoundaryDisconnected": sum(
                1 for r in rows
                if r["differences"].get("endpointDivertsBoundarySaysDisconnected")),
            "isolationChanged": sum(
                1 for r in rows if r["differences"].get("isolationChanged")),
            "penaltyDeltaM": {
                "n": len(penalties),
                "p50": _pct(penalties, 50), "p95": _pct(penalties, 95),
                "min": round(min(penalties), 1) if penalties else None,
                "max": round(max(penalties), 1) if penalties else None,
            },
        },
        "corridorPortDistanceFromClosureM": {
            "n": len(corridor_up),
            "p50": _pct(corridor_up, 50), "p95": _pct(corridor_up, 95),
            "max": round(max(corridor_up), 1) if corridor_up else None,
        },
        "runtimeMsByStage": {
            k: {"p50": _pct(v, 50), "p95": _pct(v, 95), "max": round(max(v), 1)}
            for k, v in sorted(stage_times.items())
        },
        "unresolvedOrTimeout": unresolved,
        "errored": errored,
        "geometryGapped": gapped,
        "movementCandidatesTruncated": truncated,
        "topologyConfidence": dict(Counter(
            r.get("boundary", {}).get("topologyConfidence") for r in rows)),
    }


def replay_digest(rows: list[dict]) -> str:
    """One hash over the selected ids AND every reported figure.

    Runtimes are excluded: they are properties of the machine, not of the
    answer, and including them would make every rerun differ for no reason.
    """
    payload = []
    for r in sorted(rows, key=lambda x: x["linkId"]):
        stripped = {k: v for k, v in r.items()
                    if k not in ("v1Ms", "endpointMs", "boundaryMs")}
        b = dict(stripped.get("boundary") or {})
        b.pop("stageMs", None)
        stripped["boundary"] = b
        payload.append(stripped)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def review_pack(rows: list[dict], limit: int = 25) -> list[dict]:
    """The largest disagreements, for a person to look at.

    Ordered by how much the answer moved, because that is what is worth
    somebody's time. This is a WORKLIST. Producing it is not review.
    """
    def weight(r):
        d = r["differences"]
        score = 0.0
        if d.get("endpointSaysDisconnectedBoundaryDiverts"):
            score += 1e9
        if d.get("endpointDivertsBoundarySaysDisconnected"):
            score += 1e9
        if d.get("classificationChanged"):
            score += 1e6
        if d.get("isolationChanged"):
            score += 1e6
        score += abs(d.get("penaltyDeltaM") or 0.0)
        return score

    ranked = sorted(rows, key=lambda r: (-weight(r), r["linkId"]))
    return [
        {
            "linkId": r["linkId"],
            "weight": round(weight(r), 1),
            "v1": r.get("v1", {}).get("wording"),
            "endpoint": r.get("endpoint", {}).get("headline"),
            "boundary": r.get("boundary", {}).get("headline"),
            "endpointStatus": r.get("endpoint", {}).get("status"),
            "boundaryStatus": r.get("boundary", {}).get("status"),
            "penaltyDeltaM": r["differences"].get("penaltyDeltaM"),
            "differences": r["differences"],
        }
        for r in ranked[:limit] if weight(r) > 0
    ]


def run(snapshot_id: str, size: int, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"snapshot {snapshot_id}: drawing {size} links "
          f"(seed {SAMPLE_SEED!r})", flush=True)
    sample, coverage = select(snapshot_id, size)
    print(f"  frame {coverage['frame_size']} links, "
          f"{coverage['distinct_authorities']} authorities", flush=True)

    # Build Gu once. Doing it inside the loop would time the precompute into
    # the first link and make every stage figure a lie.
    physical.get(snapshot_id, "car")

    rows: list[dict] = []
    started = time.perf_counter()
    for i, r in enumerate(sample, 1):
        rows.append({**compare_one(snapshot_id, int(r["link_id"])),
                     "strata": r["_strata"]})
        if i % 25 == 0 or i == len(sample):
            rate = (time.perf_counter() - started) / i
            print(f"  {i}/{len(sample)}  {rate:.2f} s/link  "
                  f"eta {rate * (len(sample) - i) / 60:.1f} min", flush=True)

    digest = replay_digest(rows)
    summary = summarise(rows, coverage, snapshot_id)
    summary["replayDigest"] = digest
    pack = review_pack(rows)

    (out_dir / "sample-rows.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows)
        + "\n", encoding="utf-8")
    (out_dir / "sample-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    (out_dir / "review-pack.json").write_text(
        json.dumps(pack, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    print(f"\nreplay digest: {digest}")
    print(f"review pack: {len(pack)} link(s) for someone to look at "
          f"- NOBODY HAS")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] != "run":
        print(__doc__)
        return 2
    snapshot = argv[1] if len(argv) > 1 else db.query_one(
        "SELECT snapshot_id FROM network_snapshots WHERE coverage_kind='national'"
        " ORDER BY retrieved_at_utc DESC LIMIT 1")["snapshot_id"]
    size = int(argv[2]) if len(argv) > 2 else TARGET_SIZE
    out = Path(argv[3]) if len(argv) > 3 else Path("docs/audits/detour-v2")
    return run(snapshot, size, out)


if __name__ == "__main__":
    raise SystemExit(main())
