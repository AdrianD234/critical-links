"""GREENDALE ACCEPTANCE, on a populated TRANSIENT COPY of the national snapshot.

Why a copy: the national snapshot has ZERO rows in `crossings` - it was
ingested before crossing detection existed - so candidate search has nothing
to draw from. Populating it in place would be a durable write to the national
snapshot, which is refused. So the snapshot is copied, crossings are detected
and written on the COPY, and the acceptance runs there.

The copy is transient by construction: `whatif.copy_snapshot` marks
`is_transient` and rewrites `coverage_kind`, so it cannot win automatic
snapshot selection while it exists. It is dropped on every path.

THIS PROVES THE WIRING, NOT THE PRODUCTION PATH. How crossings get populated
for a real snapshot is still open - see greendale-acceptance-status.json.

    cd python && PYTHONPATH=src:../scratch python ../scratch/greendale_acceptance.py out.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from nzcl import candidates as candidates_mod
from nzcl import crossings as crossings_mod
from nzcl import db, impactv2, neighbourhood, pinning, sensitivityrun
from nzcl import topology, whatif


SNAP = "amds-national-2026-07-28-5b359d84"
LINK = 234872
COPY = "cf-greendale-acceptance"

#: The two crossings the audit names. Only the first changes the route.
CAUSAL = (1525969.0, 5182907.6)      # Clintons Road x McLaughlins Road
DECOY = (1526312.0, 5181822.6)       # Clintons Road x Greendale Road



def load_sources():
    """Every vehicle source feature, reassembled from its link pieces.

    Inlined rather than imported: this branch is a clean extraction from main
    and does not carry the research branch scratch scripts. Same calls, same
    order as the ingest.
    """
    from shapely import wkb
    from shapely.ops import linemerge
    rows = db.query(
        "SELECT l.link_id, l.closure_group_id, l.rca_code, l.rca_name,"
        "       l.model_asset_type, l.oneway, l.surface_type, l.urban_rural,"
        "       l.quality_flags, l.road_number,"
        "       n.display_name, COALESCE(n.is_ramp, false) AS is_ramp,"
        "       ST_AsBinary(l.geom_2193) AS g"
        "  FROM links l"
        "  LEFT JOIN link_names n ON n.snapshot_id = l.snapshot_id"
        "                        AND n.closure_group_id = l.closure_group_id"
        " WHERE l.snapshot_id = %s AND l.mode_vehicle", (SNAP,))
    by_group = {}
    for r in rows:
        by_group.setdefault(r["closure_group_id"], []).append(r)
    sources = []
    for grp, rs in by_group.items():
        geoms = [wkb.loads(bytes(r["g"])) for r in rs]
        merged = linemerge(geoms) if len(geoms) > 1 else geoms[0]
        parts = ([merged] if merged.geom_type == "LineString"
                 else list(merged.geoms))
        r0 = rs[0]
        for part in parts:
            sources.append(topology.SourceLink(
                amds_id=grp,
                coords=[(c[0], c[1]) for c in part.coords],
                attrs={"road_name": r0["display_name"],
                       "rca_code": r0["rca_code"],
                       "model_asset_type": r0["model_asset_type"],
                       "oneway": r0["oneway"],
                       "is_ramp": bool(r0["is_ramp"]),
                       "quality_flags": list(r0["quality_flags"] or [])}))
    return sources


def load_structures():
    from shapely import wkb
    rows = db.query(
        "SELECT kind, ST_AsBinary(geom_2193) AS g FROM ext_structures")
    return [(wkb.loads(bytes(r["g"])), r["kind"]) for r in rows]


def populate_crossings(snapshot_id: str) -> int:
    """Detect and record crossings on the copy, as the ingest would."""
    sources = load_sources()
    structures = load_structures()
    geoms = [LineString(s.coords) for s in sources]
    endpoints, owner = [], []
    for i, s in enumerate(sources):
        endpoints.append(Point(s.coords[0]))
        owner.append(i)
        endpoints.append(Point(s.coords[-1]))
        owner.append(i)
    tree = STRtree(endpoints)

    found = crossings_mod.detect(geoms, [s.amds_id for s in sources],
                                 end_guard_m=0.05)
    attrs = [s.attrs for s in sources]
    motorway_tree, ramp_tree = topology._context_trees(sources, geoms)
    structure_tree = STRtree([g for g, _ in structures])
    structure_kinds = [k for _, k in structures]
    for x in found:
        x.classification = crossings_mod.classify(crossings_mod.build_context(
            x, geoms, attrs, endpoint_tree=tree, endpoint_owner=owner,
            motorway_tree=motorway_tree, ramp_tree=ramp_tree,
            structure_tree=structure_tree, structure_kinds=structure_kinds))
    places, demoted = crossings_mod.demote_mixed_places(found)

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crossings WHERE snapshot_id=%s",
                        (snapshot_id,))
            with cur.copy(
                "COPY crossings (snapshot_id, crossing_id, source_a, source_b,"
                " disposition, reason, detail, evidence, noded, safe_to_node,"
                " confidence, angle_deg, place_id, geom_2193) FROM STDIN"
            ) as cp:
                for cid, x in enumerate(found):
                    c = x.classification
                    cp.write_row((
                        snapshot_id, cid, x.amds_a, x.amds_b, x.disposition,
                        c.reason, c.detail, list(c.evidence),
                        # NOTHING is noded: there are no overrides, and the
                        # classifier does not create canonical nodes.
                        False, c.safe_to_node, c.confidence,
                        round(x.angle_deg, 4),
                        int(places[cid]) if places else None,
                        "SRID=2193;POINT(" + repr(x.x) + " " + repr(x.y) + ")"))
        conn.commit()
    return len(found)


def analyse_fn(snapshot_id, link_id, pinned_movement=None):
    return impactv2.analyse(snapshot_id, link_id, scope="segment",
                            metric="distance", profile="car",
                            with_geometry=False, with_corridor=False,
                            with_isolation=True)


def pin_fn(impact):
    p = impact.principal
    iso = impact.isolation
    return pinning.AnalysisPin(
        closure_links=(LINK,),
        profile=impact.profile, metric=impact.metric,
        movement=pinning.MovementPin(
            movement_id=str(getattr(p, "movement_id", "") or ""),
            entry_node=int(getattr(p, "from_node", -1) or -1),
            exit_node=int(getattr(p, "to_node", -1) or -1),
            entry_port_link=getattr(p, "entry_port_id", None),
            exit_port_link=getattr(p, "exit_port_id", None)) if p else None,
        route_arcs=tuple(getattr(p, "arc_ids", ()) or ()) if p else (),
        status=str(getattr(p, "status", "") or ""),
        distance_m=getattr(p, "replacement_distance_m", None) if p else None,
        is_bridge=getattr(iso, "closure_is_bridge", None),
        isolated_link_count=getattr(iso, "isolated_link_count", None),
        isolated_length_m=getattr(iso, "isolated_length_m", None),
        restrictions_checked=bool(getattr(p, "turn_check", None)) if p else False,
    )


def near(x, y, xy, tol=30.0):
    return abs(x - xy[0]) < tol and abs(y - xy[1]) < tol


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    t_total = time.perf_counter()
    report: dict = {"copy": COPY, "source": SNAP, "linkId": LINK}
    try:
        print("copying " + SNAP + " -> " + COPY)
        t = time.perf_counter()
        whatif.copy_snapshot(SNAP, COPY)
        report["copySeconds"] = round(time.perf_counter() - t, 1)
        row = db.query_one(
            "SELECT is_transient, coverage_kind FROM network_snapshots "
            " WHERE snapshot_id=%s", (COPY,))
        report["copyIsTransient"] = bool(row["is_transient"])
        report["copyCoverageKind"] = row["coverage_kind"]
        print("  transient=" + str(row["is_transient"])
              + " coverage_kind=" + str(row["coverage_kind"]))
        assert row["is_transient"], "the copy MUST be transient"

        print("detecting crossings on the copy")
        t = time.perf_counter()
        n = populate_crossings(COPY)
        report["crossingsDetected"] = n
        report["detectSeconds"] = round(time.perf_counter() - t, 1)
        print("  " + str(n) + " crossings in "
              + str(report["detectSeconds"]) + "s")

        print("canonical analysis")
        t = time.perf_counter()
        imp = analyse_fn(COPY, LINK)
        p = pin_fn(imp)
        report["canonical"] = {
            "status": p.status, "distanceM": p.distance_m,
            "arcs": len(p.route_arcs),
            "movementId": p.movement.movement_id if p.movement else None,
            "seconds": round(time.perf_counter() - t, 2)}
        print("  " + p.status + " " + str(p.distance_m) + " m over "
              + str(len(p.route_arcs)) + " arcs")

        route_links = list(getattr(imp.principal, "link_ids", ()) or ())
        ports = ([p.movement.entry_node, p.movement.exit_node]
                 if p.movement else [])
        run = sensitivityrun.run(
            COPY, LINK, analyse_fn=analyse_fn, pin_fn=pin_fn,
            route_link_ids=route_links, port_node_ids=ports)
        d = run.as_dict()
        report["sensitivity"] = d
        report["totalSeconds"] = round(time.perf_counter() - t_total, 1)

        print("")
        print("available=" + str(d.get("available"))
              + " topologySensitive=" + str(d.get("topologySensitive")))
        cs = d["candidateSearch"]
        print("candidates=" + str(cs["candidates"]) + " bySource="
              + str(cs["bySource"]) + " truncated=" + str(cs["truncated"]))
        if d.get("available"):
            print("headline: " + d["headline"])
            for cf in d["counterfactuals"]:
                if cf["individuallyChangesAnswer"]:
                    print("  CHANGES -> " + str(cf["distanceM"]) + " "
                          + str([j["label"] for j in cf["assumedJunctions"]])
                          + " " + str(cf["whatChanged"]))
            cands = run.search.candidates
            causal = [c for c in cands if near(c.x, c.y, CAUSAL)]
            decoy = [c for c in cands if near(c.x, c.y, DECOY)]
            report["causalFound"] = [c.crossing_id for c in causal]
            report["causalLabels"] = [c.label for c in causal]
            report["decoyFound"] = [c.crossing_id for c in decoy]
            report["decoyLabels"] = [c.label for c in decoy]
            material = set(d["materialCrossingIds"])
            report["causalIsMaterial"] = bool(
                material & set(report["causalFound"]))
            report["decoyIsMaterial"] = bool(
                material & set(report["decoyFound"]))
            print("  causal in candidates: " + str(report["causalLabels"]))
            print("  decoy  in candidates: " + str(report["decoyLabels"]))
            print("  causal is material: " + str(report["causalIsMaterial"]))
            print("  decoy  is material: " + str(report["decoyIsMaterial"])
                  + "  (MUST be False)")
        else:
            print("unavailable: " + str(d["unavailableReason"]))
    finally:
        print("")
        print("dropping the copy")
        whatif.drop_snapshot(COPY)
        left = neighbourhood.transient_snapshots()
        report["transientLeftBehind"] = left
        print("  transient snapshots left: " + str(left))
        n = db.query_one("SELECT count(*) AS n FROM links WHERE snapshot_id=%s",
                         (SNAP,))["n"]
        report["nationalLinksAfter"] = n
        print("  national snapshot intact: " + str(n) + " links")
        if out_path:
            out_path.write_text(
                json.dumps(report, indent=2, default=str) + "\n",
                encoding="utf-8")
            print("  wrote " + str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
