"""Apply crossings.classify() to every national crossing and report the split.

Runs the SAME function the ingest will run. Nothing is reimplemented in SQL.

That claim was not true for one input. `crossings.build_context()`, which the
ingest uses, computes `duplicate_corridor` from the two centrelines; this
script built its context by hand and never set it, so it silently defaulted to
False. The DUPLICATE_GEOMETRY rule - the larger of the two fixes the blinded
review produced, accounting for eleven of its seventeen AT_GRADE misses - could
therefore never fire on the national record, and the record still showed the
pre-fix classification. Fixed: the geometries are loaded and
`crossings.is_duplicate_corridor()` is called, which is the same function
`build_context()` calls.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from shapely import wkb
from shapely.geometry import Point

from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"


def load() -> list[dict]:
    return db.query(
        """
        SELECT f.*,
               -- The Road Controlling Authority name is the closest thing the
               -- snapshot has to a region: for local roads it is the district
               -- or city council, and for state highways it is the agency.
               -- Used to stratify the holdout geographically, never as a
               -- classifier input.
               a.rca_name AS rca_name_a, b.rca_name AS rca_name_b,
               -- a THIRD link ending at the crossing: exclude the four nodes
               -- belonging to the two links that cross.
               (SELECT count(*) FROM nodes n
                 WHERE n.snapshot_id = %s
                   AND ST_DWithin(n.geom_2193, f.pt_2193, 1.0)
                   AND n.node_id <> ALL (ARRAY[a.source_node, a.target_node,
                                               b.source_node, b.target_node]))
                 AS third_link_nodes
          FROM scratch_features f
          JOIN links a ON a.snapshot_id = %s AND a.link_id = f.link_a
          JOIN links b ON b.snapshot_id = %s AND b.link_id = f.link_b
        """, (SNAP, SNAP, SNAP))


def load_geometries(rows: list[dict]) -> dict[int, object]:
    """Centrelines in NZTM for every link that takes part in a crossing.

    Needed for `is_duplicate_corridor`, which is a question about geometry and
    cannot be answered from the attribute row.
    """
    ids = sorted({int(r["link_a"]) for r in rows} | {int(r["link_b"]) for r in rows})
    out: dict[int, object] = {}
    CHUNK = 20000
    for i in range(0, len(ids), CHUNK):
        for g in db.query(
                "SELECT link_id, ST_AsBinary(geom_2193) AS g FROM links "
                " WHERE snapshot_id=%s AND link_id = ANY(%s)",
                (SNAP, ids[i:i + CHUNK])):
            out[int(g["link_id"])] = wkb.loads(bytes(g["g"]))
    print(f"loaded {len(out)} centrelines for {len(ids)} referenced links")
    return out


def duplicate_of(r: dict, geoms: dict[int, object]) -> bool:
    a = geoms.get(int(r["link_a"]))
    b = geoms.get(int(r["link_b"]))
    if a is None or b is None:
        return False
    p = Point(float(r["px"]), float(r["py"]))
    return crossings.is_duplicate_corridor(a, a.project(p), b, b.project(p))


def context_of(r: dict, duplicate_corridor: bool = False) -> crossings.CrossingContext:
    return crossings.CrossingContext(
        duplicate_corridor=duplicate_corridor,
        angle_deg=float(r["angle_deg"]),
        model_asset_type=(r["mat_a"], r["mat_b"]),
        oneway=(r["oneway_a"], r["oneway_b"]),
        rca_code=(r["rca_a"], r["rca_b"]),
        is_ramp=(bool(r["ramp_a"]), bool(r["ramp_b"])),
        road_name=(r["name_a"], r["name_b"]),
        quality_flags=(r["flags_a"] or (), r["flags_b"] or ()),
        junction_witness=int(r["third_link_nodes"]) > 0,
        motorway_links_near=int(r["motorway_links_300m"]),
        ramp_links_near=int(r["ramp_links_300m"]),
        same_source_feature=r["group_a"] == r["group_b"],
        structure_dist_m=(None if r["struct_dist_m"] is None
                          else float(r["struct_dist_m"])),
        structure_align_deg=(None if r["struct_align_deg"] is None
                             else float(r["struct_align_deg"])),
        structure_kind=r["struct_kind"],
    )


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = load()
    print(f"{len(rows)} point-like crossing pairs")
    geoms = load_geometries(rows)

    by_disp = collections.Counter()
    by_reason = collections.Counter()
    results = []
    for r in rows:
        dup = duplicate_of(r, geoms)
        c = crossings.classify(context_of(r, dup))
        by_disp[c.disposition] += 1
        by_reason[(c.disposition, c.reason)] += 1
        r["duplicate_corridor"] = dup
        results.append((r, c))

    print()
    print("=== disposition, by crossing PAIR ===")
    for d, n in by_disp.most_common():
        print(f"  {d:<16} {n:>6}  {100.0*n/len(rows):5.1f}%")

    print()
    print("=== deciding rule ===")
    for (d, reason), n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  {d:<16} {reason:<26} {n:>6}  {100.0*n/len(rows):5.1f}%")

    # --- the same thing counted as PLACES, not pairs ----------------------
    #
    # The radius is an AUDIT CONVENTION for grouping a review display. It never
    # drives noding: every cut is made at its own exact crossing point, for its
    # own specific source pair. All the radius does is decide which crossings
    # are considered "the same interchange" when checking for disagreement.
    #
    # 25 m was chosen because it is wide enough to hold one physical
    # intersection of two divided carriageways - four crossing points spread
    # over 20-30 m - and narrower than an urban block, so two genuinely
    # separate junctions do not merge.
    #
    # Reporting several radii makes the sensitivity of that choice visible
    # rather than hiding it behind one number.
    pts = [(float(r["px"]), float(r["py"])) for r, _ in results]
    print()
    print("=== places, by clustering radius (an audit convention, not "
          "topology identity) ===")
    for eps in (5.0, 10.0, 25.0, 50.0):
        lab = crossings.cluster(pts, eps_m=eps)
        pd: dict[int, set[str]] = collections.defaultdict(set)
        for (r, c), L in zip(results, lab):
            pd[L].add(c.disposition)
        mixed = sum(1 for ds in pd.values() if len(ds) > 1)
        print(f"  {eps:5.0f} m: {len(pd):>6} places, {mixed:>4} mixed "
              f"({100.0*mixed/len(pd):4.1f}%)")
    print("  Mixedness is MONOTONE in the radius: merging clusters can only "
          "add disagreement, never remove it. So a place that is mixed at 5 m "
          "is necessarily inside a place that is mixed at 25 m, and clustering "
          "at the wider radius withdraws a SUPERSET. 25 m is the conservative "
          "choice, not a convenient one.")

    labels = crossings.cluster(pts, eps_m=25.0)
    place_disp: dict[int, set[str]] = collections.defaultdict(set)
    for (r, c), lab in zip(results, labels):
        place_disp[lab].add(c.disposition)
    print()
    print(f"=== places (25 m clustering): {len(place_disp)} unique crossings ===")
    agree = collections.Counter()
    for lab, ds in place_disp.items():
        agree["|".join(sorted(ds))] += 1
    for k, n in agree.most_common():
        print(f"  {k:<40} {n:>6}")

    # A place is AT_GRADE only if every pair at it says so.
    unanimous = collections.Counter(
        sorted(ds)[0] if len(ds) == 1 else "MIXED" for ds in place_disp.values())
    print()
    print("=== disposition per PLACE (mixed = pairs at one place disagree) ===")
    for k, n in unanimous.most_common():
        print(f"  {k:<16} {n:>6}  {100.0*n/len(place_disp):5.1f}%")

    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
        with (outdir / "classified.jsonl").open("w", encoding="utf-8") as fh:
            for (r, c), lab in zip(results, labels):
                fh.write(json.dumps({
                    "linkA": r["link_a"], "linkB": r["link_b"],
                    "groupA": r["group_a"], "groupB": r["group_b"],
                    "x": float(r["px"]), "y": float(r["py"]),
                    "place": int(lab),
                    "disposition": c.disposition, "reason": c.reason,
                    "detail": c.detail, "evidence": c.evidence,
                    "angleDeg": round(float(r["angle_deg"]), 2),
                    "nameA": r["name_a"], "nameB": r["name_b"],
                    "rcaA": r["rca_a"], "rcaB": r["rca_b"],
                    "onewayA": r["oneway_a"], "onewayB": r["oneway_b"],
                    "matA": r["mat_a"], "matB": r["mat_b"],
                    "urA": r["ur_a"], "urB": r["ur_b"],
                    # 1 = sealed. 2 and 3 are unsealed, which together with an
                    # absent name is the best proxy the source offers for a
                    # forestry, industrial or private-looking access road.
                    "surfA": r["surf_a"], "surfB": r["surf_b"],
                    "rcaNameA": r["rca_name_a"], "rcaNameB": r["rca_name_b"],
                    "duplicateCorridor": bool(r["duplicate_corridor"]),
                    "thirdLinkNodes": int(r["third_link_nodes"]),
                    "motorwayLinks300m": int(r["motorway_links_300m"]),
                    "rampLinks300m": int(r["ramp_links_300m"]),
                    "vdistA": round(float(r["vdist_a"]), 4),
                    "vdistB": round(float(r["vdist_b"]), 4),
                    "structDistM": (None if r["struct_dist_m"] is None
                                    else round(float(r["struct_dist_m"]), 1)),
                    "structAlignDeg": (None if r["struct_align_deg"] is None
                                       else round(float(r["struct_align_deg"]), 1)),
                    "structKind": r["struct_kind"],
                }) + "\n")
        summary = {
            "snapshot": SNAP,
            "pairsAll": 13084,
            "pairsPointLike": len(rows),
            "pairsCollinearOverlap": 28,
            "intersectionPoints": 18675,
            "placesAt25m": len(place_disp),
            "byDispositionPairs": dict(by_disp),
            "byReasonPairs": {f"{d}/{r}": n for (d, r), n in by_reason.items()},
            "byDispositionPlaces": dict(unanimous),
        }
        (outdir / "classification-summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nwrote {outdir/'classified.jsonl'} and "
              f"{outdir/'classification-summary.json'}")

    print()
    print("=== the two Darfield crossings ===")
    for r, c in results:
        if (r["link_a"], r["link_b"]) in ((232709, 234053), (232708, 234875)):
            print(f"  {r['link_a']} x {r['link_b']}  {r['name_a']} x {r['name_b']}")
            print(f"    -> {c.disposition} ({c.reason})")
            print(f"       {c.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
