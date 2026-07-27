"""Source-data and graph quality assurance.

    nzcl-qa <snapshotId>

Writes findings to the qa_issues table and prints a summary. Nothing here
"repairs" data: the job is to measure and expose, so that a number in the
application can be traced to a stated condition of the source.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import db


def run(snapshot_id: str) -> dict[str, Any]:
    meta = db.query_one(
        "SELECT * FROM network_snapshots WHERE snapshot_id=%s", (snapshot_id,))
    if meta is None:
        raise SystemExit(f"unknown snapshot {snapshot_id}")

    db.execute("DELETE FROM qa_issues WHERE snapshot_id=%s", (snapshot_id,))
    issues: list[dict[str, Any]] = []

    def add(severity: str, issue_type: str, entity: str, count: int,
            detail: str, samples: list[str] | None = None) -> None:
        issues.append({"severity": severity, "issue_type": issue_type,
                       "entity_type": entity, "count": count, "detail": detail,
                       "sample_ids": samples or []})

    totals = db.query_one(
        """
        SELECT (SELECT count(*) FROM links WHERE snapshot_id=%(s)s) AS links,
               (SELECT count(*) FROM arcs WHERE snapshot_id=%(s)s) AS arcs,
               (SELECT count(*) FROM nodes WHERE snapshot_id=%(s)s) AS nodes,
               (SELECT count(DISTINCT component_id) FROM nodes WHERE snapshot_id=%(s)s) AS components,
               (SELECT round((sum(length_m)/1000.0)::numeric, 1) FROM links WHERE snapshot_id=%(s)s) AS length_km,
               (SELECT count(*) FROM links WHERE snapshot_id=%(s)s AND road_name IS NOT NULL) AS named,
               (SELECT count(*) FROM links WHERE snapshot_id=%(s)s AND oneway = 1) AS one_way,
               (SELECT count(*) FROM links WHERE snapshot_id=%(s)s AND lifeline_route) AS lifeline,
               (SELECT count(*) FROM turn_restrictions WHERE snapshot_id=%(s)s) AS restrictions,
               (SELECT count(*) FROM near_misses WHERE snapshot_id=%(s)s) AS near_misses
        """,
        {"s": snapshot_id},
    )

    # --- link-level checks -------------------------------------------------
    zero = db.query(
        "SELECT amds_id FROM links WHERE snapshot_id=%s AND length_m <= 0 LIMIT 10",
        (snapshot_id,))
    if zero:
        add("error", "ZERO_LENGTH_LINK", "link", len(zero),
            "Link has non-positive length and cannot carry traffic.",
            [r["amds_id"] for r in zero])

    loops = db.query_one(
        "SELECT count(*) AS n FROM links WHERE snapshot_id=%s "
        "AND source_node = target_node", (snapshot_id,))["n"]
    if loops:
        samples = [r["amds_id"] for r in db.query(
            "SELECT amds_id FROM links WHERE snapshot_id=%s "
            "AND source_node = target_node LIMIT 10", (snapshot_id,))]
        add("warning", "SELF_LOOP", "link", loops,
            "Link starts and ends at the same node (typically a closed loop "
            "road). Excluded from arc generation; detour results report "
            "SOURCE_DATA_ERROR.", samples)

    dupes = db.query(
        "SELECT amds_id FROM links WHERE snapshot_id=%s GROUP BY amds_id "
        "HAVING count(*) > 1 LIMIT 10", (snapshot_id,))
    if dupes:
        add("error", "DUPLICATE_STABLE_ID", "link", len(dupes),
            "Two graph links share an amds_id. Identifiers must be unique "
            "within a snapshot.", [r["amds_id"] for r in dupes])

    long_links = db.query_one(
        "SELECT count(*) AS n FROM links WHERE snapshot_id=%s AND length_m > 50000",
        (snapshot_id,))["n"]
    if long_links:
        add("warning", "IMPLAUSIBLE_LENGTH", "link", long_links,
            "Link longer than 50 km. Verify against the source geometry.")

    # --- graph structure ---------------------------------------------------
    comps = db.query(
        """
        SELECT component_id, count(*) AS links FROM (
          SELECT n.component_id, l.link_id
          FROM links l JOIN nodes n
            ON n.snapshot_id = l.snapshot_id AND n.node_id = l.source_node
          WHERE l.snapshot_id = %s
        ) q GROUP BY component_id ORDER BY links DESC
        """,
        (snapshot_id,),
    )
    total_links = totals["links"] or 1
    largest = (comps[0]["links"] / total_links) if comps else 0.0
    top_two = ((comps[0]["links"] + (comps[1]["links"] if len(comps) > 1 else 0))
               / total_links) if comps else 0.0

    # New Zealand is two main islands with no road connection between them, so
    # a national graph is legitimately dominated by TWO components. Judging it
    # on the largest alone reports Cook Strait as a defect.
    if top_two < 0.9:
        add("error", "FRAGMENTED_GRAPH", "graph", totals["components"],
            f"The two largest connected components hold only {top_two * 100:.1f}% "
            f"of links (largest {largest * 100:.1f}%). A road network should be "
            f"dominated by one component per landmass. Investigate junction "
            f"splitting.")
    else:
        add("info", "COMPONENT_STRUCTURE", "graph", totals["components"],
            f"Largest component {largest * 100:.1f}% of links; two largest "
            f"{top_two * 100:.1f}%. Two dominant components is the expected shape "
            f"for New Zealand: the North and South Islands have no road "
            f"connection. Smaller components are ferry-only islands, isolated "
            f"peninsulas, and off-network parking or access areas.")

    if totals["near_misses"]:
        add("warning", "UNCONNECTED_NEAR_MISS", "node", totals["near_misses"],
            "A link endpoint lies between the 0.05 m split tolerance and 5 m of "
            "another link but was NOT connected. Either the source has a genuine "
            "gap or the tolerance is too tight. Rows are in the near_misses table.")

    long_restrictions = db.query_one(
        "SELECT count(*) AS n FROM turn_restrictions WHERE snapshot_id=%s "
        "AND array_length(link_seq, 1) > 2", (snapshot_id,))["n"]
    add("warning" if long_restrictions else "info", "TURN_RESTRICTION_COVERAGE",
        "restriction", totals["restrictions"],
        f"{totals['restrictions']} turn restrictions are applied, of which "
        f"{long_restrictions} span more than two links. Two-link restrictions are "
        f"removed structurally from the expanded graph. NOTE: AMDS publishes only "
        f"60 restricted turns nationally, so banned-turn coverage is effectively "
        f"negligible and routes through complex intersections must not be "
        f"presented as road-legal.")

    nslr = db.query_one(
        "SELECT count(*) AS n FROM links WHERE snapshot_id=%s "
        "AND speed_source = 'nslr'", (snapshot_id,))["n"]
    add("warning" if nslr == 0 else "info", "SPEED_PROVENANCE", "link",
        total_links - nslr,
        "AMDS publishes no speed attribute. All time-metric results derive from "
        "estimated speeds and are flagged TIME_ESTIMATED. Distance is the "
        "defensible metric.")

    # --- persist -----------------------------------------------------------
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            for i in issues:
                cur.execute(
                    "INSERT INTO qa_issues (snapshot_id, severity, issue_type, "
                    "entity_type, count, detail, sample_ids) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (snapshot_id, i["severity"], i["issue_type"], i["entity_type"],
                     i["count"], i["detail"], i["sample_ids"]),
                )
        conn.commit()

    report = {
        "snapshotId": snapshot_id,
        "totals": {
            **{k: v for k, v in totals.items()},
            "largestComponentSharePct": round(largest * 100, 2),
            "twoLargestComponentsSharePct": round(top_two * 100, 2),
        },
        "componentSizes": [c["links"] for c in comps[:20]],
        "issues": issues,
        "ingestNotes": list(meta["notes"] or []),
    }

    print(f"QA report for {snapshot_id}")
    print(f"  links {totals['links']}  arcs {totals['arcs']}  nodes {totals['nodes']}")
    print(f"  components {totals['components']}, largest {largest * 100:.2f}%, "
          f"two largest {top_two * 100:.2f}%")
    print(f"  network length {totals['length_km']} km")
    print(f"  named links {totals['named'] / total_links * 100:.2f}%")
    print("\n  issues:")
    for i in issues:
        print(f"    [{i['severity'].upper()}] {i['issue_type']} ({i['count']})")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Quality report for a snapshot")
    ap.add_argument("snapshot_id", nargs="?")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)

    snap = args.snapshot_id
    if not snap:
        row = db.query_one(
            "SELECT snapshot_id FROM network_snapshots ORDER BY retrieved_at_utc "
            "DESC LIMIT 1")
        if not row:
            raise SystemExit("no snapshots; run nzcl-ingest first")
        snap = row["snapshot_id"]

    report = run(snap)
    if args.json:
        print(json.dumps(report, indent=2, default=str), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
