"""Phase 1: the Greendale counterfactual, end to end.

  control    - copy the national snapshot, run the identical request, prove the
               copy answers exactly what the original answered
  treatment  - node the confirmed interior-interior crossings within 5 km of the
               clicked link, run the identical request again
  minimal    - repeat with ONLY the crossings the corrected route actually uses
  rollback   - drop the copy

The national snapshot is never written to.

  python -m greendale_counterfactual all    <outdir>
  python -m greendale_counterfactual drop
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from nzcl import closure, db, impactv2, physical, whatif

NATIONAL = "amds-national-2026-07-28-5b359d84"
COPY = "whatif-greendale"
LINK = 234872
RADIUS_M = 5000


# --------------------------------------------------------------------------
def local_crossings(snapshot: str, link_id: int, radius_m: float) -> list[dict]:
    """Every interior-interior crossing of vehicle links within `radius_m`.

    Restricted to single-point intersections between two DIFFERENT source
    features. A collinear overlap is duplicate geometry, not a crossing, and a
    source feature crossing itself is a different question.
    """
    return db.query(
        """
        WITH focus AS (
          SELECT ST_Buffer(geom_2193, %s) AS g FROM links
           WHERE snapshot_id = %s AND link_id = %s
        ), cand AS (
          SELECT l.link_id, l.closure_group_id, l.geom_2193, l.source_node, l.target_node
            FROM links l, focus f
           WHERE l.snapshot_id = %s AND l.mode_vehicle
             AND ST_Intersects(l.geom_2193, f.g)
        )
        SELECT a.link_id AS link_a, b.link_id AS link_b,
               ST_X(ST_Intersection(a.geom_2193, b.geom_2193)) AS x,
               ST_Y(ST_Intersection(a.geom_2193, b.geom_2193)) AS y,
               na.display_name AS name_a, nb.display_name AS name_b
          FROM cand a JOIN cand b ON b.link_id > a.link_id
          LEFT JOIN link_display_names na
                 ON na.snapshot_id = %s AND na.link_id = a.link_id
          LEFT JOIN link_display_names nb
                 ON nb.snapshot_id = %s AND nb.link_id = b.link_id
         WHERE ST_Intersects(a.geom_2193, b.geom_2193)
           AND ST_GeometryType(ST_Intersection(a.geom_2193, b.geom_2193)) = 'ST_Point'
           AND a.closure_group_id <> b.closure_group_id
           AND NOT (ARRAY[a.source_node, a.target_node] && ARRAY[b.source_node, b.target_node])
         ORDER BY a.link_id, b.link_id
        """,
        (radius_m, snapshot, link_id, snapshot, snapshot, snapshot),
    )


def route_rows(snapshot: str, arc_ids: list[int]) -> list[dict]:
    if not arc_ids:
        return []
    rows = db.query(
        "SELECT a.arc_id, a.link_id, a.source, a.target, a.direction, "
        "       a.cost_distance_m, coalesce(dn.display_name, l.road_name) AS name, "
        "       l.quality_flags "
        "  FROM arcs a JOIN links l ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id "
        "  LEFT JOIN link_display_names dn "
        "         ON dn.snapshot_id=a.snapshot_id AND dn.link_id=a.link_id "
        " WHERE a.snapshot_id=%s AND a.arc_id = ANY(%s)", (snapshot, arc_ids))
    by = {r["arc_id"]: r for r in rows}
    return [by[a] for a in arc_ids if a in by]


def print_route(label: str, snapshot: str, arc_ids: list[int]) -> float:
    rows = route_rows(snapshot, arc_ids)
    print(f"  {label}: {len(rows)} arcs")
    total = 0.0
    for r in rows:
        total += r["cost_distance_m"]
        flag = " *" if "SPLIT_AT_GRADE_CROSSING" in (r["quality_flags"] or []) else ""
        print(f"    arc {r['arc_id']:>7} link {r['link_id']:>7} "
              f"{r['source']:>7}->{r['target']:<7} {r['cost_distance_m']:>9.1f} m  "
              f"{r['name'] or '(unnamed)'}{flag}")
    print(f"    TOTAL {total:.1f} m")
    return total


def analyse(snapshot: str, link_id: int) -> dict:
    """Exactly the request the browser makes on a click, in V2 boundary mode:
    scope=segment, no direction, metric=distance, vehicle=car."""
    physical.clear_cache()
    out = impactv2.analyse(snapshot, link_id, scope="segment", direction=None,
                           metric="distance", profile="car",
                           with_geometry=False, with_corridor=True,
                           with_isolation=True)
    p = out.principal
    return {
        "snapshot": snapshot,
        "headline": out.headline,
        "qualityFlags": out.quality_flags,
        "removedLinkIds": out.closure.removed_link_ids,
        "removedArcIds": out.closure.removed_arc_ids,
        "boundaryNodes": out.closure.boundary_nodes,
        "selectedSegmentLengthM": out.closure.selected_segment_length_m,
        "principal": None if p is None else {
            "status": p.status,
            "resolved": p.resolved,
            "intactDistanceM": p.intact_distance_m,
            "replacementDistanceM": p.replacement_distance_m,
            "networkPenaltyM": p.network_penalty_m,
            "ratio": p.ratio,
            "arcIds": list(p.arc_ids or []),
            "linkIds": list(p.link_ids or []),
            "turnCheck": {
                "ok": p.turn_check.ok if p.turn_check else None,
                "checked": p.turn_check.checked if p.turn_check else None,
                "applicableRestrictions":
                    p.turn_check.applicable_restrictions if p.turn_check else None,
                "violations": list(p.turn_check.violations) if p.turn_check else None,
            },
            "topologyConfidence": p.topology_confidence,
        },
        "isolation": None if out.isolation is None else {
            "physicallyIsolates": out.isolation.physically_isolates,
            "closureIsBridge": out.isolation.closure_is_bridge,
            "topologyConfidence": out.isolation.topology_confidence,
            "topologyConfidenceReason": out.isolation.topology_confidence_reason,
        },
        "replacementCount": len(out.replacements.paths),
    }


def summarise(label: str, d: dict) -> None:
    p = d["principal"]
    print(f"\n--- {label} ---")
    print(f"  snapshot      : {d['snapshot']}")
    print(f"  headline      : {d['headline']}")
    print(f"  quality flags : {d['qualityFlags']}")
    print(f"  closed        : links {d['removedLinkIds']} "
          f"({d['selectedSegmentLengthM']:.1f} m)")
    if p:
        print(f"  status        : {p['status']}")
        print(f"  intact        : {p['intactDistanceM']:.1f} m")
        print(f"  replacement   : {p['replacementDistanceM']:.1f} m")
        print(f"  penalty       : {p['networkPenaltyM']:.1f} m  (ratio "
              f"{p['ratio']:.2f}x)" if p["ratio"] else "")
        print(f"  turn check    : ok={p['turnCheck']['ok']} "
              f"checked={p['turnCheck']['checked']} "
              f"applicable={p['turnCheck']['applicableRestrictions']} "
              f"violations={p['turnCheck']['violations']}")
    print(f"  topo conf     : {d['isolation']['topologyConfidence']}")


# --------------------------------------------------------------------------
def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "drop":
        whatif.drop_snapshot(COPY)
        print(f"dropped {COPY}")
        return 0

    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    record: dict = {}

    print("=" * 74)
    print("STEP 1  the exact user request, on the untouched national snapshot")
    print("=" * 74)
    base = analyse(NATIONAL, LINK)
    summarise("BASELINE (national, unmodified)", base)
    print()
    print_route("baseline replacement route", NATIONAL, base["principal"]["arcIds"])
    record["baseline"] = base

    print()
    print("=" * 74)
    print("STEP 2  every interior-interior crossing within 5 km of the closure")
    print("=" * 74)
    xs = local_crossings(NATIONAL, LINK, RADIUS_M)
    print(f"  {len(xs)} crossings")
    for x in xs:
        print(f"    {x['link_a']:>7} x {x['link_b']:<7} "
              f"({x['x']:.3f}, {x['y']:.3f})  "
              f"{x['name_a'] or '(unnamed)'} x {x['name_b'] or '(unnamed)'}")
    record["crossings"] = [
        {k: v for k, v in x.items()} for x in xs]

    print()
    print("=" * 74)
    print("STEP 3  isolated graph copy, CONTROL run (nothing changed)")
    print("=" * 74)
    whatif.copy_snapshot(NATIONAL, COPY)
    control = analyse(COPY, LINK)
    summarise("CONTROL (copy, unmodified)", control)
    record["control"] = control
    ok = (control["principal"]["arcIds"] == base["principal"]["arcIds"]
          and abs(control["principal"]["replacementDistanceM"]
                  - base["principal"]["replacementDistanceM"]) < 1e-6
          and control["headline"] == base["headline"])
    print(f"\n  copy reproduces the original EXACTLY: {ok}")
    record["controlMatchesBaseline"] = ok

    print()
    print("=" * 74)
    print("STEP 4  node the crossings on the copy, and rerun the SAME request")
    print("=" * 74)
    edits = [whatif.CrossingEdit(x["link_a"], x["link_b"], x["x"], x["y"])
             for x in xs]
    rep = whatif.node_crossings(COPY, edits)
    print(f"  applied {rep.crossings_applied}/{rep.crossings_requested}; "
          f"{rep.links_split} links split into {rep.links_split + rep.new_links}; "
          f"{rep.new_nodes} new nodes; {rep.arcs_rebuilt} arcs rebuilt")
    for key, why in rep.crossings_skipped:
        print(f"    SKIPPED {key}: {why}")
    record["noding"] = {
        "requested": rep.crossings_requested,
        "applied": rep.crossings_applied,
        "skipped": [[list(k), v] for k, v in rep.crossings_skipped],
        "linksSplit": rep.links_split,
        "newLinks": rep.new_links,
        "newNodes": rep.new_nodes,
        "nodeOfCrossing": {f"{a}x{b}": n
                           for (a, b), n in rep.node_of_crossing.items()},
        "piecesOfLink": {str(k): v for k, v in rep.pieces_of_link.items()},
    }

    treated = analyse(COPY, LINK)
    summarise("TREATMENT (copy, crossings noded)", treated)
    print()
    total = print_route("corrected replacement route", COPY,
                        treated["principal"]["arcIds"])
    record["treatment"] = treated

    print()
    print("=" * 74)
    print("STEP 5  which crossings does the corrected route actually use?")
    print("=" * 74)
    used_nodes = set()
    for r in route_rows(COPY, treated["principal"]["arcIds"]):
        used_nodes.add(r["source"])
        used_nodes.add(r["target"])
    crossing_nodes = {n: k for k, n in rep.node_of_crossing.items()}
    used = sorted((crossing_nodes[n], n) for n in used_nodes if n in crossing_nodes)
    for key, node in used:
        x = next(c for c in xs if (min(c["link_a"], c["link_b"]),
                                   max(c["link_a"], c["link_b"])) == key)
        print(f"    node {node}: crossing {key[0]} x {key[1]}  "
              f"{x['name_a'] or '(unnamed)'} x {x['name_b'] or '(unnamed)'}  "
              f"at ({x['x']:.3f}, {x['y']:.3f})")
    record["crossingsUsedByCorrectedRoute"] = [
        {"key": list(k), "node": n} for k, n in used]
    if not used:
        print("    NONE - the corrected route uses no newly noded crossing.")

    with (outdir / "greendale-counterfactual.json").open("w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)
    print(f"\nwrote {outdir / 'greendale-counterfactual.json'}")
    print(f"\nThe copy {COPY} is still present for follow-up queries.")
    print(f"Drop it with:  python ../scratch/greendale_counterfactual.py drop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
