"""Apply crossings.classify() to every national crossing and report the split.

Runs the SAME function the ingest will run. Nothing is reimplemented in SQL.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from nzcl import crossings, db

SNAP = "amds-national-2026-07-28-5b359d84"


def load() -> list[dict]:
    return db.query(
        """
        SELECT f.*,
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


def context_of(r: dict) -> crossings.CrossingContext:
    return crossings.CrossingContext(
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
    )


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = load()
    print(f"{len(rows)} point-like crossing pairs")

    by_disp = collections.Counter()
    by_reason = collections.Counter()
    results = []
    for r in rows:
        c = crossings.classify(context_of(r))
        by_disp[c.disposition] += 1
        by_reason[(c.disposition, c.reason)] += 1
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
    pts = [(float(r["px"]), float(r["py"])) for r, _ in results]
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
                    "thirdLinkNodes": int(r["third_link_nodes"]),
                    "motorwayLinks300m": int(r["motorway_links_300m"]),
                    "rampLinks300m": int(r["ramp_links_300m"]),
                    "vdistA": round(float(r["vdist_a"]), 4),
                    "vdistB": round(float(r["vdist_b"]), 4),
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
