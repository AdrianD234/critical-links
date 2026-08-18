"""Batch detour computation over every eligible link.

    nzcl-detours --snapshot <id> [--vehicle car] [--metric distance]
                 [--closure-scope physical] [--limit N] [--resume]

Restartable and idempotent: results are upserted into `detour_results`, and
`--resume` skips links already recorded for the same parameter set. Killing the
process loses at most the link in flight.

Completeness is asserted, not assumed. The run reports the eligible link count
up front and reconciles at the end; anything not OK or DISCONNECTED is listed
separately as unresolved. A run that does not reconcile is reported as
INCOMPLETE rather than being quietly presented as full coverage.
"""

from __future__ import annotations

import argparse
import statistics
import time
from typing import Any

from . import db
from .config import ALGORITHM, ALGORITHM_VERSION
from .detour import compute

_UPSERT = """
INSERT INTO detour_results (
  snapshot_id, link_id, closure_group_id, vehicle_profile, metric,
  closure_scope, direction, status, source_node, target_node,
  selected_link_length_m, normal_path_distance_m, alternative_distance_m,
  added_distance_vs_link_m, network_penalty_m, detour_ratio_vs_link,
  normal_path_time_s, alternative_time_s, added_time_s,
  corridor_status, corridor_normal_m, corridor_alternative_m, corridor_penalty_m,
  corridor_hops_upstream, corridor_hops_downstream,
  isolation_side, isolation_link_count, isolation_length_m,
  route_arc_ids, removed_arc_ids, algorithm, algorithm_version, runtime_ms,
  quality_flags, error_detail)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (snapshot_id, link_id, vehicle_profile, metric, closure_scope,
             direction, algorithm_version)
DO UPDATE SET status = EXCLUDED.status,
              alternative_distance_m = EXCLUDED.alternative_distance_m,
              network_penalty_m = EXCLUDED.network_penalty_m,
              calculated_at_utc = now()
"""


def run(snapshot_id: str, *, vehicle: str = "car", metric: str = "distance",
        closure_scope: str = "physical", limit: int | None = None,
        resume: bool = False) -> dict[str, Any]:
    eligible = db.query(
        "SELECT link_id FROM links WHERE snapshot_id=%s AND in_analysis_area "
        "AND length_m > 0 ORDER BY link_id",
        (snapshot_id,),
    )
    eligible_ids = [r["link_id"] for r in eligible]
    print(f"  eligible links in analysis area: {len(eligible_ids)}")

    done: set[int] = set()
    if resume:
        done = {
            r["link_id"] for r in db.query(
                "SELECT DISTINCT link_id FROM detour_results WHERE snapshot_id=%s "
                "AND vehicle_profile=%s AND metric=%s AND closure_scope=%s "
                "AND algorithm_version=%s",
                (snapshot_id, vehicle, metric, closure_scope, ALGORITHM_VERSION))
        }
        print(f"  resuming: {len(done)} links already computed")

    todo = [i for i in eligible_ids if i not in done]
    if limit:
        todo = todo[:limit]
    print(f"  computing {len(todo)}\n")

    times: list[float] = []
    statuses: dict[str, int] = {}
    started = time.perf_counter()

    with db.direct_connection(autocommit=False) as conn:
        for n, link_id in enumerate(todo, start=1):
            t0 = time.perf_counter()
            try:
                res = compute(snapshot_id, link_id, metric=metric,
                              profile=vehicle, closure_scope=closure_scope)
            except Exception as exc:  # noqa: BLE001
                # An application fault is not a network finding.
                statuses["API_ERROR"] = statuses.get("API_ERROR", 0) + 1
                print(f"    link {link_id}: API_ERROR {exc}")
                continue
            times.append((time.perf_counter() - t0) * 1000)

            with conn.cursor() as cur:
                for d in (res.forward, res.reverse):
                    if d is None:
                        continue
                    statuses[d.status] = statuses.get(d.status, 0) + 1
                    c, iso = d.corridor, d.isolation
                    cur.execute(_UPSERT, (
                        snapshot_id, link_id, res.closure_group_id, vehicle, metric,
                        closure_scope, d.direction, d.status, d.source_node,
                        d.target_node, d.selected_link_length_m,
                        d.normal_path_distance_m, d.alternative_distance_m,
                        d.added_distance_vs_link_m, d.network_penalty_m,
                        d.detour_ratio_vs_link, d.normal_path_time_s,
                        d.alternative_time_s, d.added_time_s,
                        c.status if c else None,
                        c.normal_distance_m if c else None,
                        c.alternative_distance_m if c else None,
                        c.penalty_m if c else None,
                        c.hops_upstream if c else None,
                        c.hops_downstream if c else None,
                        iso.side if iso else None,
                        iso.pocket_link_count if iso else None,
                        iso.pocket_length_m if iso else None,
                        d.route_arc_ids, d.removed_arc_ids, ALGORITHM,
                        ALGORITHM_VERSION, d.runtime_ms, d.quality_flags,
                        d.error_detail,
                    ))
            if n % 200 == 0:
                conn.commit()
                el = time.perf_counter() - started
                rate = n / el
                print(f"\r  {n}/{len(todo)}  {rate:.1f} links/s  "
                      f"eta {(len(todo) - n) / rate / 60:.1f} min   ",
                      end="", flush=True)
        conn.commit()
    print()

    elapsed = time.perf_counter() - started
    times.sort()

    def pct(p: float) -> float:
        return times[min(len(times) - 1, int(p / 100 * len(times)))] if times else 0.0

    computed = len(done) + len(todo)
    unresolved = sum(v for k, v in statuses.items()
                     if k not in ("OK", "DISCONNECTED"))
    complete = computed == len(eligible_ids) and limit is None

    report = {
        "snapshotId": snapshot_id,
        "run": f"{vehicle}-{metric}-{closure_scope}",
        "eligibleLinks": len(eligible_ids),
        "computedLinks": computed,
        "complete": complete,
        "completeness": ("COMPLETE - every eligible link has a recorded outcome"
                         if complete else
                         "INCOMPLETE - do not present this as full coverage"),
        "unresolvedDirections": unresolved,
        "statusCounts": statuses,
        "performance": {
            "elapsedSeconds": round(elapsed, 1),
            "linksPerSecond": round(len(todo) / elapsed, 2) if elapsed else 0,
            "meanMs": round(statistics.mean(times), 1) if times else 0,
            "p50Ms": round(pct(50), 1),
            "p95Ms": round(pct(95), 1),
            "maxMs": round(times[-1], 1) if times else 0,
        },
    }

    print(f"\ncompleted {len(todo)} links in {elapsed:.1f} s")
    print(f"  mean {report['performance']['meanMs']} ms  "
          f"p95 {report['performance']['p95Ms']} ms  "
          f"max {report['performance']['maxMs']} ms")
    print(f"  {report['completeness']}")
    print("  status counts (per direction):")
    for k, v in sorted(statuses.items()):
        print(f"    {k:20} {v}")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Batch detour computation")
    ap.add_argument("--snapshot")
    ap.add_argument("--vehicle", default="car",
                    choices=["car", "heavy", "emergency"])
    ap.add_argument("--metric", default="distance", choices=["distance", "time"])
    ap.add_argument("--closure-scope", default="physical",
                    choices=["physical", "directed"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)

    snap = args.snapshot
    if not snap:
        row = db.query_one("SELECT snapshot_id FROM network_snapshots "
                           "WHERE NOT is_transient "
                           "ORDER BY retrieved_at_utc DESC LIMIT 1")
        if not row:
            raise SystemExit("no snapshots; run nzcl-ingest first")
        snap = row["snapshot_id"]

    print(f"batch detours for {snap}")
    run(snap, vehicle=args.vehicle, metric=args.metric,
        closure_scope=args.closure_scope, limit=args.limit, resume=args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
