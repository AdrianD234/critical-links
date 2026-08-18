"""Cross-validate the pgRouting engine against the TypeScript engine.

    python -m nzcl.crossvalidate --pg-snapshot <id> --ts-url http://localhost:8787

Two independent implementations of the same specification - different
languages, different graph representations, different shortest-path libraries -
computing the same quantity over the same source data. Where they agree, the
result is corroborated by something stronger than either engine's own test
suite. Where they disagree, one of them is wrong and the difference is the
finding.

Comparison is by AMDS id, not internal link id: the two engines assign their own
dense indices, and only the source identifier is common to both.

Note that the two snapshots are ingested independently, so tiny differences in
junction splitting can put a boundary in a slightly different place. Links whose
length differs by more than a tolerance are reported separately as
NOT_COMPARABLE rather than counted as disagreements.
"""

from __future__ import annotations

import argparse
import json
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import db
from .detour import compute


def _ts_detour(base_url: str, amds_id: str) -> dict[str, Any] | None:
    ref = urllib.parse.quote(amds_id, safe="")
    url = (f"{base_url}/api/v1/links/{ref}/detour"
           f"?metric=distance&vehicle=car&closure_scope=physical"
           f"&direction=both&geometry=false")
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def run(pg_snapshot: str, ts_url: str, sample: int = 150,
        length_tolerance_m: float = 1.0,
        distance_tolerance_m: float = 1.0) -> dict[str, Any]:
    links = db.query(
        """
        SELECT link_id, amds_id, length_m, road_name
        FROM links
        WHERE snapshot_id = %s AND in_analysis_area AND source_node <> target_node
        ORDER BY link_id
        """,
        (pg_snapshot,),
    )
    stride = max(1, len(links) // sample)
    chosen = links[::stride][:sample]
    print(f"comparing {len(chosen)} links (of {len(links)} eligible)")
    print(f"  pgRouting snapshot: {pg_snapshot}")
    print(f"  TypeScript engine:  {ts_url}\n")

    agree = disagree = not_comparable = missing = 0
    status_agree = status_disagree = 0
    deltas: list[float] = []
    examples: list[str] = []

    for l in chosen:
        ts = _ts_detour(ts_url, l["amds_id"])
        if ts is None:
            missing += 1
            continue

        # Only comparable when both engines split the source link the same way.
        ts_len = (ts.get("selectedLink") or {}).get("lengthM")
        if ts_len is None or abs(ts_len - l["length_m"]) > length_tolerance_m:
            not_comparable += 1
            continue

        pg = compute(pg_snapshot, l["link_id"], compute_corridor=False)

        for direction in ("forward", "reverse"):
            pg_dir = getattr(pg, direction)
            ts_dir = ts.get(direction)
            if pg_dir is None or ts_dir is None:
                continue

            if pg_dir.status != ts_dir["status"]:
                status_disagree += 1
                if len(examples) < 10:
                    examples.append(
                        f"STATUS {l['road_name'] or l['amds_id']} {direction}: "
                        f"pg={pg_dir.status} ts={ts_dir['status']}")
                continue
            status_agree += 1

            if pg_dir.status != "OK":
                agree += 1
                continue

            ts_alt = ts_dir["metrics"]["alternativeDistanceM"]
            pg_alt = pg_dir.alternative_distance_m
            if ts_alt is None or pg_alt is None:
                not_comparable += 1
                continue
            delta = abs(pg_alt - ts_alt)
            deltas.append(delta)
            if delta <= distance_tolerance_m:
                agree += 1
            else:
                disagree += 1
                if len(examples) < 10:
                    examples.append(
                        f"DISTANCE {l['road_name'] or l['amds_id']} {direction}: "
                        f"pg={pg_alt:.1f} ts={ts_alt:.1f} delta={delta:.1f} m")

    compared = agree + disagree
    report = {
        "pgSnapshot": pg_snapshot,
        "tsUrl": ts_url,
        "linksSampled": len(chosen),
        "notComparable": not_comparable,
        "missingInTs": missing,
        "statusAgreements": status_agree,
        "statusDisagreements": status_disagree,
        "distanceAgreements": agree,
        "distanceDisagreements": disagree,
        "agreementPct": round(agree / compared * 100, 2) if compared else None,
        "maxDeltaM": round(max(deltas), 3) if deltas else None,
        "medianDeltaM": round(statistics.median(deltas), 3) if deltas else None,
        "examples": examples,
    }

    print(f"  links not comparable (different split): {not_comparable}")
    print(f"  links absent from the TypeScript snapshot: {missing}")
    print(f"\n  status agreements:    {status_agree}")
    print(f"  status disagreements: {status_disagree}")
    print(f"\n  distance agreements    (<= {distance_tolerance_m} m): {agree}")
    print(f"  distance disagreements:                    {disagree}")
    if compared:
        print(f"  agreement: {report['agreementPct']}%")
    if deltas:
        print(f"  median delta {report['medianDeltaM']} m, "
              f"max delta {report['maxDeltaM']} m")
    if examples:
        print("\n  examples:")
        for e in examples:
            print(f"    {e}")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cross-validate pgRouting against the TypeScript engine")
    ap.add_argument("--pg-snapshot")
    ap.add_argument("--ts-url", default="http://localhost:8787")
    ap.add_argument("--sample", type=int, default=150)
    args = ap.parse_args(argv)

    snap = args.pg_snapshot
    if not snap:
        row = db.query_one(
            "SELECT snapshot_id FROM network_snapshots "
            "WHERE snapshot_id NOT LIKE 'test-%' AND NOT is_transient "
            "ORDER BY retrieved_at_utc DESC LIMIT 1")
        if not row:
            raise SystemExit("no snapshots")
        snap = row["snapshot_id"]

    run(snap, args.ts_url, args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
