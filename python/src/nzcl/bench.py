"""Quick routing benchmark against a loaded snapshot.

    python -m nzcl.bench <snapshotId> [n]
"""

from __future__ import annotations

import statistics
import sys
import time

from . import db
from .routing import route


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    snapshot = argv[0] if argv else db.query_one(
        "SELECT snapshot_id FROM network_snapshots ORDER BY retrieved_at_utc DESC LIMIT 1"
    )["snapshot_id"]
    n = int(argv[1]) if len(argv) > 1 else 50

    links = db.query(
        """
        SELECT link_id, source_node, target_node, length_m, closure_group_id
        FROM links
        WHERE snapshot_id = %s AND in_analysis_area AND source_node <> target_node
        ORDER BY link_id
        """,
        (snapshot,),
    )
    stride = max(1, len(links) // n)
    sample = links[::stride][:n]
    print(f"snapshot {snapshot}: {len(links)} eligible links, sampling {len(sample)}")

    times: list[float] = []
    statuses: dict[str, int] = {}
    for l in sample:
        arcs = [
            r["arc_id"]
            for r in db.query(
                "SELECT arc_id FROM arcs WHERE snapshot_id=%s AND closure_group_id=%s",
                (snapshot, l["closure_group_id"]),
            )
        ]
        t0 = time.perf_counter()
        res = route(snapshot, l["source_node"], l["target_node"],
                    excluded_arcs=arcs)
        times.append((time.perf_counter() - t0) * 1000)
        statuses[res.status] = statuses.get(res.status, 0) + 1

    times.sort()
    def pct(p: float) -> float:
        return times[min(len(times) - 1, int(p / 100 * len(times)))]

    print(f"  mean {statistics.mean(times):.1f} ms")
    print(f"  p50  {pct(50):.1f} ms")
    print(f"  p95  {pct(95):.1f} ms")
    print(f"  max  {times[-1]:.1f} ms")
    print(f"  statuses: {statuses}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
