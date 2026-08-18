"""The national crossing record, produced by the code that actually ships.

WHY THIS REPLACES classify_national.py
--------------------------------------
The old record was built in SQL over the LINKS table - that is, over the graph
AFTER `split_at_junctions` had already cut it - and then had
`crossings.classify()` applied to rows assembled by hand. The classifier that
ships runs somewhere else entirely: inside `split_at_junctions`, over AMDS
SOURCE FEATURES, before any graph exists. Three differences follow, and each
one changed a verdict on the 248-card holdout:

  ONE ROW PER PAIR vs ONE PER POINT. Two features can cross more than once; 692
  pairs do. The SQL took `ST_PointOnSurface` of the multipoint and produced a
  single row at one of them. The shipped detector emits every point, and the
  mixed-place rule then sees the disagreement between them. Holdout card H040
  (Hansen Road) is exactly this: two crossings 11.1 m apart at 14.1 and 48.9
  degrees. The record showed one AT_GRADE crossing at 31.4 degrees. The shipped
  code withdraws BOTH as MIXED_PLACE and nodes nothing.

  A FRACTIONAL ANGLE WINDOW vs A FIXED ONE. The SQL measured the crossing angle
  over +/-0.02 of each line's LENGTH. On a 12.7 m link that is +/-25 cm, which
  measures the noise between two digitised vertices; on a 3.6 km link it is
  +/-73 m, which measures the wrong corner. `crossing_angle_deg` uses a fixed
  10 m window. Holdout card H001 (Council Place Loop) is recorded at 31.6
  degrees and is 21.8 by the shipped measure - below the 30-degree tangential
  threshold, so the shipped code does not node it either.

  LINK GEOMETRY vs FEATURE GEOMETRY. A link is a piece of a feature, so every
  length-sensitive test - the duplicate-corridor run above all - was being
  asked about a fragment.

So the record the holdout was drawn from, and scored against, is not the
classifier. Two of the three AT_GRADE contradictions are artefacts of the
record; the third, H192, was real and is fixed by `corridor_polyline`.

WHAT THIS DOES INSTEAD
----------------------
Rebuilds the source features by re-merging the link pieces of each
`closure_group_id`, then calls the same functions `topology.split_at_junctions`
calls, in the same order: `crossings.detect`, `crossings.build_context`,
`crossings.classify`, `crossings.demote_mixed_places`.

THE ONE PLACE THIS IS NOT THE INGEST, STATED PLAINLY. The ingest reads the AMDS
extract. That extract is not in this worktree, so the features here are
reassembled from the database. Re-merging is exact for geometry - splitting
inserts vertices, it does not move the line - but a feature whose pieces do not
merge back into a single LineString is reported rather than silently patched,
and the count is written into the summary.

    python ../scratch/classify_national_v2.py ../docs/audits/at-grade-crossings
"""
from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path

from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.ops import linemerge
from shapely.strtree import STRtree

from nzcl import crossings, db, topology

SNAP = "amds-national-2026-07-28-5b359d84"


def load_sources() -> tuple[list[topology.SourceLink], int, dict]:
    """Every vehicle source feature, reassembled from its link pieces."""
    t0 = time.perf_counter()
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
    print(f"  {len(rows)} links in {time.perf_counter()-t0:.0f}s")

    by_group: dict[str, list] = {}
    for r in rows:
        by_group.setdefault(r["closure_group_id"], []).append(r)

    sources: list[topology.SourceLink] = []
    unmerged = 0
    meta: dict[str, dict] = {}
    for grp, rs in by_group.items():
        geoms = [wkb.loads(bytes(r["g"])) for r in rs]
        merged = linemerge(geoms) if len(geoms) > 1 else geoms[0]
        parts = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
        if len(parts) > 1:
            unmerged += 1
        r0 = rs[0]
        meta[grp] = {
            "rcaName": r0["rca_name"], "surface": r0["surface_type"],
            "urbanRural": r0["urban_rural"], "roadNumber": r0["road_number"],
        }
        for part in parts:
            sources.append(topology.SourceLink(
                amds_id=grp,
                coords=[(c[0], c[1]) for c in part.coords],
                attrs={
                    "road_name": r0["display_name"],
                    "rca_code": r0["rca_code"],
                    "model_asset_type": r0["model_asset_type"],
                    "oneway": r0["oneway"],
                    "is_ramp": bool(r0["is_ramp"]),
                    "quality_flags": list(r0["quality_flags"] or []),
                }))
    print(f"  {len(by_group)} source features -> {len(sources)} lines "
          f"({unmerged} did not merge to one line)")
    return sources, unmerged, meta


def load_structures():
    rows = db.query("SELECT kind, ST_AsBinary(geom_2193) AS g FROM ext_structures")
    return [(wkb.loads(bytes(r["g"])), r["kind"]) for r in rows]


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else None
    print("loading source features")
    sources, unmerged, meta = load_sources()
    structures = load_structures()
    print(f"  {len(structures)} LINZ Topo50 structure centrelines")

    geoms = [LineString(s.coords) for s in sources]
    endpoints, owner = [], []
    for i, s in enumerate(sources):
        endpoints.append(Point(s.coords[0]))
        owner.append(i)
        endpoints.append(Point(s.coords[-1]))
        owner.append(i)
    tree = STRtree(endpoints)

    print("detecting interior-interior crossings")
    t0 = time.perf_counter()
    found = crossings.detect(geoms, [s.amds_id for s in sources],
                             end_guard_m=0.05)
    print(f"  {len(found)} crossing POINTS in {time.perf_counter()-t0:.0f}s")

    print("classifying")
    t0 = time.perf_counter()
    attrs = [s.attrs for s in sources]
    motorway_tree, ramp_tree = topology._context_trees(sources, geoms)
    structure_tree = STRtree([g for g, _ in structures])
    structure_kinds = [k for _, k in structures]
    for n, x in enumerate(found):
        x.classification = crossings.classify(crossings.build_context(
            x, geoms, attrs, endpoint_tree=tree, endpoint_owner=owner,
            motorway_tree=motorway_tree, ramp_tree=ramp_tree,
            structure_tree=structure_tree, structure_kinds=structure_kinds))
        if n and n % 2000 == 0:
            print(f"    {n}/{len(found)} in {time.perf_counter()-t0:.0f}s")
    print(f"  classified in {time.perf_counter()-t0:.0f}s")

    before = collections.Counter((x.disposition, x.classification.reason)
                                 for x in found)
    places, demoted = crossings.demote_mixed_places(found)
    print(f"  {demoted} crossings withdrawn as MIXED_PLACE")

    by_disp = collections.Counter(x.disposition for x in found)
    by_reason = collections.Counter(
        (x.disposition, x.classification.reason) for x in found)
    noded = sum(1 for x in found
                if x.disposition == crossings.AT_GRADE
                and x.classification.safe_to_node)

    print()
    print("=== disposition, by crossing POINT ===")
    for d, n in by_disp.most_common():
        print(f"  {d:<16} {n:>6}  {100.0*n/len(found):5.1f}%")
    print()
    print("=== deciding rule ===")
    for (d, reason), n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  {d:<16} {reason:<24} {n:>6}")
    print()
    print(f"=== crossings the CONFIRMED graph would node: {noded} ===")

    pd: dict[int, set[str]] = collections.defaultdict(set)
    for x, lab in zip(found, places):
        pd[lab].add(x.disposition)
    per_place = collections.Counter(
        sorted(v)[0] if len(v) == 1 else "MIXED" for v in pd.values())
    print()
    print(f"=== disposition per PLACE (25 m): {len(pd)} places ===")
    for k, n in per_place.most_common():
        print(f"  {k:<16} {n:>6}")

    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
        path = outdir / "classified-v2.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for x, lab in zip(found, places):
                c = x.classification
                was = x.classification_before_place_rule
                a, b = meta.get(x.amds_a, {}), meta.get(x.amds_b, {})
                ctx_a = attrs[x.index_a]
                ctx_b = attrs[x.index_b]
                fh.write(json.dumps({
                    "groupA": x.amds_a, "groupB": x.amds_b,
                    "x": round(x.x, 3), "y": round(x.y, 3),
                    "place": int(lab),
                    "disposition": c.disposition, "reason": c.reason,
                    "confidence": c.confidence,
                    "safeToNode": c.safe_to_node,
                    "wasBeforePlaceRule": (None if was is None else
                                           f"{was.disposition}/{was.reason}"),
                    "angleDeg": round(x.angle_deg, 2),
                    "nameA": ctx_a.get("road_name"),
                    "nameB": ctx_b.get("road_name"),
                    "rcaA": ctx_a.get("rca_code"), "rcaB": ctx_b.get("rca_code"),
                    "onewayA": ctx_a.get("oneway"), "onewayB": ctx_b.get("oneway"),
                    "matA": ctx_a.get("model_asset_type"),
                    "matB": ctx_b.get("model_asset_type"),
                    "rampA": ctx_a.get("is_ramp"), "rampB": ctx_b.get("is_ramp"),
                    "rcaNameA": a.get("rcaName"), "rcaNameB": b.get("rcaName"),
                    "surfA": a.get("surface"), "surfB": b.get("surface"),
                    "urA": a.get("urbanRural"), "urB": b.get("urbanRural"),
                }) + "\n")
        summary = {
            "snapshot": SNAP,
            "producedBy": "scratch/classify_national_v2.py",
            "method": ("source features reassembled from the links table, then "
                       "crossings.detect + build_context + classify + "
                       "demote_mixed_places - the same calls, in the same "
                       "order, that topology.split_at_junctions makes"),
            "sourceFeatures": len({s.amds_id for s in sources}),
            "featuresThatDidNotMergeToOneLine": unmerged,
            "crossingPoints": len(found),
            "placesAt25m": len(pd),
            "byDispositionPoints": dict(by_disp),
            "byReasonPoints": {f"{d}/{r}": n for (d, r), n in by_reason.items()},
            "byReasonBeforeMixedPlaceRule":
                {f"{d}/{r}": n for (d, r), n in before.items()},
            "mixedPlaceDemotions": demoted,
            "nodedByConfirmedPolicy": noded,
            "byDispositionPlaces": dict(per_place),
        }
        (outdir / "classification-summary-v2.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path} and {outdir/'classification-summary-v2.json'}")

    print()
    print("=== the two Darfield crossings ===")
    want = {frozenset({"{232709}", "{234053}"})}
    for x in found:
        p = Point(x.x, x.y)
        for label, (px, py) in (("Clintons x McLaughlins", (1525969.0, 5182907.6)),
                                ("Clintons x Greendale", (1526312.0, 5181822.6))):
            if p.distance(Point(px, py)) < 1.0:
                print(f"  {label}: {x.disposition} / "
                      f"{x.classification.reason} angle={x.angle_deg:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
