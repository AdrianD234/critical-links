"""Case-by-case review of the movements where the two measures disagree.

    python -m nzcl.reviewv2 disagreements <snapshotId> <rowsJsonl> <outDir>
    python -m nzcl.reviewv2 links <snapshotId> <id,id,...> <outDir>

WHY THESE CASES
---------------
The national sample recorded 26 links where the ENDPOINT measure finds a route
between the closed segment's own two nodes and the BOUNDARY measure finds no
replacement for the through movement. They are the highest-value rows in the
sample: either the boundary measure is wrong, or the endpoint measure has been
quietly reassuring people about closures that cut something off.

Each case is reported with everything needed to settle that without rerunning
the engine: the selected segment and the full closure, every boundary port, the
intact through movement, the replacement search, whether truncation could have
hidden a valid replacement, topology confidence, turn-restriction exposure, and
an INDEPENDENT oracle result.

THE ORACLE
----------
networkx over the raw `arcs` table, loaded once and reused. It shares no code
with the engine: not the port derivation, not the movement test, not pgRouting.
Its job here is narrow and decisive - given the closure arcs removed, is there
a path between the two nodes the engine measured, and if so how long. If the
engine says DISCONNECTED and the oracle finds a path, the engine is wrong.

NO HUMAN HAS REVIEWED THIS OUTPUT. It is the pack a person would need.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from . import closure as closure_mod
from . import db, detourv2, impactv2, movements, ports, replacement, turns


def load_oracle_graph(snapshot_id: str, profile_column: str = "mode_vehicle"):
    """Every arc, kept SEPARATELY, not collapsed to the cheapest per node pair.

    The first version stored one edge per (u, v) - the cheapest arc - and then
    deleted edges whose stored arc was in the closure. Where two arcs run
    between the same pair of nodes and only the cheaper one is closed, that
    deleted the connection entirely and the oracle reported "no path" while the
    engine correctly routed over the surviving parallel arc.

    It showed up immediately: on link 375011 the oracle said the endpoint pair
    was unreachable while the engine reported a 108 m route. Had the same thing
    happened on a MOVEMENT pair, engine and oracle would have agreed on None
    for opposite reasons, and the agreement would have been worthless.

    Parallel arcs are real in this data - AMDS carries them, and links 49791
    and 49792 near Tokoroa are a documented pair.
    """
    t0 = time.perf_counter()
    rows = db.query(
        f"SELECT arc_id, source, target, cost_distance_m FROM arcs "
        f" WHERE snapshot_id=%s AND {profile_column}", (snapshot_id,))
    parallel: dict[tuple[int, int], list[tuple[int, float]]] = {}
    for r in rows:
        parallel.setdefault((int(r["source"]), int(r["target"])), []).append(
            (int(r["arc_id"]), float(r["cost_distance_m"])))
    print(f"  oracle arcs: {len(rows):,} over {len(parallel):,} node pairs "
          f"in {time.perf_counter() - t0:.1f}s", flush=True)
    return parallel


def oracle_distance(parallel, u: int, v: int,
                    removed_arcs: set[int]) -> float | None:
    """Shortest distance u -> v with the closure's arcs removed. networkx only.

    The graph is rebuilt from the surviving arcs for each closure, so a node
    pair keeps its connection whenever ANY of its parallel arcs survives.
    """
    import networkx as nx

    g = nx.DiGraph()
    for (a, b), arcs in parallel.items():
        best = min((w for arc, w in arcs if arc not in removed_arcs),
                   default=None)
        if best is not None:
            g.add_edge(a, b, weight=best)
    if u not in g or v not in g:
        return None
    try:
        return nx.shortest_path_length(g, u, v, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def review_one(snapshot_id: str, link_id: int, g) -> dict[str, Any]:
    """Everything a person needs to settle one case."""
    out: dict[str, Any] = {"linkId": link_id}

    link = db.query_one(
        "SELECT l.link_id, l.amds_id, l.closure_group_id, l.length_m, "
        "       l.rca_name, l.oneway, l.urban_rural, "
        "       coalesce(dn.display_name, l.road_name) AS name, "
        "       l.source_node, l.target_node, "
        "       ST_Y(ST_Transform(ST_PointOnSurface(l.geom_2193),4326)) AS lat, "
        "       ST_X(ST_Transform(ST_PointOnSurface(l.geom_2193),4326)) AS lon "
        "  FROM links l LEFT JOIN link_display_names dn "
        "    ON dn.snapshot_id=l.snapshot_id AND dn.link_id=l.link_id "
        " WHERE l.snapshot_id=%s AND l.link_id=%s", (snapshot_id, link_id))
    out["selectedSegment"] = {
        "amdsId": link["amds_id"], "name": link["name"],
        "lengthM": round(float(link["length_m"]), 1),
        "rca": link["rca_name"], "oneway": link["oneway"],
        "urbanRural": link["urban_rural"],
        "sourceNode": int(link["source_node"]),
        "targetNode": int(link["target_node"]),
        "lonLat": [round(float(link["lon"]), 6), round(float(link["lat"]), 6)],
    }

    # --- the closure, in full ------------------------------------------
    c = closure_mod.resolve(snapshot_id, link_id, scope="source_feature")
    out["closure"] = {
        "scope": "source_feature",
        "removedLinkCount": c.removed_link_count,
        "totalClosureLengthM": round(c.total_closure_length_m, 1),
        "shape": c.shape,
        "removedLinkIds": c.removed_link_ids,
        "closureNodes": c.closure_nodes,
        "boundaryNodes": c.boundary_nodes,
    }

    # --- the endpoint measure, and why it says what it says --------------
    ep = detourv2.analyse(snapshot_id, link_id, scope="source_feature",
                          use_cache=False)
    d = ep.forward or ep.reverse
    out["endpointMeasure"] = {
        "headline": ep.headline,
        "measuredBetween": [getattr(d, "source_node", None),
                            getattr(d, "target_node", None)],
        "status": getattr(d, "status", None),
        "alternativeDistanceM": getattr(d, "alternative_distance_m", None),
        "normalDistanceM": getattr(d, "normal_path_distance_m", None),
    }

    # --- the boundary measure -------------------------------------------
    b = impactv2.analyse(snapshot_id, link_id, scope="source_feature",
                         with_geometry=False)
    out["boundaryMeasure"] = {
        "headline": b.headline,
        "qualityFlags": b.quality_flags,
        "movementsIncluded": len(b.movement_set.included),
        "movementsConsidered": b.movement_set.candidate_pairs,
        "exhaustive": b.movement_set.exhaustive,
        "omittedPairCount": b.movement_set.omitted_pair_count,
        "closureComponents": b.movement_set.closure_components,
        "replacementStatus": None if b.principal is None else b.principal.status,
        "intactDistanceM": None if b.principal is None
        else b.principal.intact_distance_m,
        "replacementDistanceM": None if b.principal is None
        else b.principal.replacement_distance_m,
    }
    out["boundaryPorts"] = [
        {
            "kind": p.kind, "closureNode": p.closure_node,
            "outsideNode": p.outside_node, "linkId": p.link_id,
            "direction": p.direction, "roadName": p.road_name,
            "distanceFromSelectedM": p.distance_from_selected_m,
            "closureComponentId": p.closure_component_id,
            "inSelectedComponent": p.in_selected_component,
        }
        for p in b.boundary.ports
    ]

    pm = b.principal_movement
    out["intactThroughMovement"] = None if pm is None else {
        "fromNode": pm.from_node, "toNode": pm.to_node,
        "entryNode": pm.entry_node, "exitNode": pm.exit_node,
        "intactDistanceM": pm.intact_distance_m,
        "removedArcIdsUsed": pm.removed_arc_ids_used,
        "evidence": pm.evidence, "confidence": pm.confidence,
    }

    # --- could truncation have hidden a valid replacement? ---------------
    out["truncationCouldHideAReplacement"] = bool(
        b.movement_set.omitted_pair_count) or b.movement_set.truncated

    # --- the INDEPENDENT oracle ------------------------------------------
    removed = set(int(a) for a in c.removed_arc_ids)
    oracle: dict[str, Any] = {"engine": "networkx, no shared code"}
    if pm is not None:
        od = oracle_distance(g, pm.from_node, pm.to_node, removed)
        oracle["movementFromNode"] = pm.from_node
        oracle["movementToNode"] = pm.to_node
        oracle["replacementDistanceM"] = None if od is None else round(od, 3)
        engine_status = b.principal.status if b.principal else None
        oracle["agreesWithEngine"] = (
            (od is None and engine_status == "DISCONNECTED")
            or (od is not None and engine_status == "OK"
                and abs(od - (b.principal.replacement_distance_m or -1)) < 1e-6))
    # And the endpoint pair, so the disagreement is explained rather than
    # merely recorded.
    eu, ev = int(link["source_node"]), int(link["target_node"])
    oed = oracle_distance(g, eu, ev, removed)
    oracle["endpointPair"] = [eu, ev]
    oracle["endpointReplacementDistanceM"] = (None if oed is None
                                              else round(oed, 3))
    out["oracle"] = oracle

    out["topologyConfidence"] = (None if b.isolation is None
                                 else b.isolation.topology_confidence)
    out["isolation"] = None if b.isolation is None else {
        "physicallyIsolates": b.isolation.physically_isolates,
        "separatedLinkCount": b.isolation.separated_link_count,
        "separatedLengthM": round(b.isolation.separated_length_m, 1),
    }

    # --- turn-restriction exposure ---------------------------------------
    seqs = turns.restricted_sequences(snapshot_id, "car")
    touching = []
    if b.principal is not None and b.principal.arc_ids:
        tc = turns.check(snapshot_id, b.principal.arc_ids, profile="car",
                         restrictions=seqs)
        touching = tc.violations
    out["turnRestrictions"] = {
        "applicableNationally": len(seqs),
        "violationsOnThisRoute": touching,
    }

    # --- the explanation --------------------------------------------------
    out["whyTheyDiffer"] = _explain(out)
    return out


def _explain(case: dict) -> str:
    ep, bm = case["endpointMeasure"], case["boundaryMeasure"]
    pm = case.get("intactThroughMovement")
    ends = ep.get("measuredBetween")
    if pm is None:
        return ("The endpoint measure routed between the selected segment's own "
                "two nodes; the boundary measure identified no through movement "
                "at all, so there is nothing to compare.")
    same_pair = ends and set(ends) == {pm["fromNode"], pm["toNode"]}
    if same_pair:
        return ("Both measures used the SAME node pair, so this is a genuine "
                "engine disagreement and needs settling by the oracle result "
                "above.")
    return (
        f"The two measures used DIFFERENT node pairs. The endpoint measure "
        f"asked about the selected segment's own nodes {ends}, which is a "
        f"{ep.get('alternativeDistanceM')} m hop; the boundary measure asked "
        f"about the crossing {pm['fromNode']} -> {pm['toNode']}, which is where "
        f"the {case['closure']['removedLinkCount']}-link closure actually meets "
        f"the open network. A closure of "
        f"{case['closure']['totalClosureLengthM']} m does not reduce to the "
        f"clicked segment's two ends, so the endpoint figure describes a trip "
        f"nobody was making.")


def run(snapshot_id: str, link_ids: list[int], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"reviewing {len(link_ids)} case(s) on {snapshot_id}", flush=True)
    g = load_oracle_graph(snapshot_id)

    cases = []
    for i, lid in enumerate(link_ids, 1):
        try:
            cases.append(review_one(snapshot_id, int(lid), g))
        except Exception as exc:  # noqa: BLE001
            cases.append({"linkId": int(lid),
                          "error": f"{type(exc).__name__}: {exc}"})
        print(f"  {i}/{len(link_ids)} link {lid}", flush=True)

    agree = sum(1 for c in cases
                if c.get("oracle", {}).get("agreesWithEngine") is True)
    disagree = sum(1 for c in cases
                   if c.get("oracle", {}).get("agreesWithEngine") is False)
    could_hide = sum(1 for c in cases
                     if c.get("truncationCouldHideAReplacement"))
    same_pair = sum(1 for c in cases
                    if "SAME node pair" in (c.get("whyTheyDiffer") or ""))

    summary = {
        "snapshotId": snapshot_id,
        "caseCount": len(cases),
        "oracleAgrees": agree,
        "oracleDisagrees": disagree,
        "truncationCouldHideAReplacement": could_hide,
        "sameNodePairSoGenuineEngineDisagreement": same_pair,
        "note": ("NO HUMAN HAS REVIEWED THIS. It is the pack a person would "
                 "need. The oracle is networkx over the raw arcs table and "
                 "shares no code with the engine."),
        "cases": cases,
    }
    (out_dir / "disagreement-review.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"},
                     indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 4:
        print(__doc__)
        return 2
    mode, snapshot, arg, out = argv[0], argv[1], argv[2], Path(argv[3])
    if mode == "disagreements":
        rows = [json.loads(l) for l in open(arg, encoding="utf-8")]
        ids = [r["linkId"] for r in rows
               if r["differences"].get("endpointDivertsBoundarySaysDisconnected")]
        return run(snapshot, ids, out)
    if mode == "links":
        return run(snapshot, [int(x) for x in arg.split(",") if x], out)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
