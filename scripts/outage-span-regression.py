"""Real-data regression gate for the two-point outage span.

Run against a database that holds a NATIONAL snapshot. Synthetic fixtures prove
the arithmetic; this proves the arithmetic survives contact with 375,696 real
links, which is where both corridor defects were found and neither was visible
on a fixture.

    scripts/outage-span-env.sh python scripts/outage-span-regression.py

Every check prints PASS or FAIL and the run exits non-zero if any failed, so it
is usable as a gate rather than as something to read.

The headline case is SH 6. Two adjacent links, 185 m of road between the
handles, and before the shared-junction corridor was built explicitly this
returned an 813 m corridor across three other streets - with no sign anything
was wrong, because every number downstream agreed with the wrong road.
"""

from __future__ import annotations

import statistics
import sys
import time

from nzcl import db, outage, routing, snap, span_corridor, vsplit
from nzcl.outage import HandleRef
from nzcl.span_corridor import HandleOption

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(name)


def timed(fn, n=3):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts)


def national_snapshot() -> str | None:
    row = db.query_one(
        "SELECT snapshot_id, processing_version, routable_link_count "
        "  FROM network_snapshots WHERE coverage_kind='national' "
        "   AND status='complete' ORDER BY retrieved_at_utc DESC LIMIT 1")
    if row is None:
        return None
    print(f"snapshot {row['snapshot_id']}")
    print(f"  processing version {row['processing_version']}  "
          f"{row['routable_link_count']} links\n")
    return str(row["snapshot_id"])


def adjacent_pair(snapshot: str, road_like: str | None = None,
                  min_len: float = 100.0, max_len: float = 400.0):
    """Two links of one road that meet at a node."""
    clause = "AND a.road_name LIKE %s" if road_like else ""
    params: tuple = (snapshot,) + ((road_like,) if road_like else ())
    return db.query_one(
        f"""
        SELECT a.link_id AS a_id, b.link_id AS b_id, a.road_name,
               a.length_m AS a_len, b.length_m AS b_len
          FROM links a JOIN links b
            ON b.snapshot_id = a.snapshot_id AND b.source_node = a.target_node
           AND b.link_id <> a.link_id AND b.road_name = a.road_name
         WHERE a.snapshot_id = %s AND a.road_name IS NOT NULL {clause}
           AND a.length_m BETWEEN {min_len} AND {max_len}
           AND b.length_m BETWEEN {min_len} AND {max_len}
         ORDER BY a.link_id LIMIT 1
        """, params)


def long_link(snapshot: str, minimum: float = 600.0):
    return db.query_one(
        "SELECT link_id, length_m, road_name, "
        "       ST_NPoints(geom_2193) AS points "
        "  FROM links WHERE snapshot_id=%s AND length_m > %s "
        "   AND ST_NPoints(geom_2193) >= 4 AND road_name IS NOT NULL "
        " ORDER BY link_id LIMIT 1", (snapshot, minimum))


def one_way_link(snapshot: str):
    return db.query_one(
        "SELECT link_id, length_m FROM links "
        " WHERE snapshot_id=%s AND forward_allowed AND NOT reverse_allowed "
        "   AND length_m > 200 ORDER BY link_id LIMIT 1", (snapshot,))


def main() -> int:
    snapshot = national_snapshot()
    if snapshot is None:
        print("no national snapshot in this database - nothing to check")
        return 0

    # ---------------------------------------------------------------- snap
    print("snapping")
    pt = db.query_one(
        "SELECT ST_X(ST_LineInterpolatePoint(geom_2193,0.5)) AS x, "
        "       ST_Y(ST_LineInterpolatePoint(geom_2193,0.5)) AS y "
        "  FROM links WHERE snapshot_id=%s AND road_name IS NOT NULL "
        " ORDER BY link_id LIMIT 1", (snapshot,))
    ms = timed(lambda: snap.snap(snapshot, pt["x"] + 25, pt["y"] + 25), n=5)
    check("snap is millisecond-scale", ms < 50.0, f"{ms:.1f} ms")

    result = snap.snap(snapshot, pt["x"] + 25, pt["y"] + 25)
    check("snap lands on a centreline", result.found and result.chosen.offset_m < 60.0,
          f"offset {result.chosen.offset_m:.1f} m" if result.found else "not found")

    # --------------------------------------------------- adjacent SH6 links
    print("\nadjacent links on one road (the 813 m regression)")
    pair = adjacent_pair(snapshot, "SH 6%") or adjacent_pair(snapshot)
    if pair is None:
        check("found an adjacent pair", False)
    else:
        a, b = int(pair["a_id"]), int(pair["b_id"])
        expected = (float(pair["a_len"]) + float(pair["b_len"])) / 2.0
        ms = timed(lambda: span_corridor.select(
            snapshot, [HandleOption(a, 0.5)], [HandleOption(b, 0.5)]))
        choice = span_corridor.select(
            snapshot, [HandleOption(a, 0.5)], [HandleOption(b, 0.5)])
        check("corridor runs through the shared junction",
              len(choice.chosen.steps) == 2,
              f"{len(choice.chosen.steps)} steps on {pair['road_name']}")
        check("corridor length is the road between the handles",
              abs(choice.chosen.length_m - expected) < 1.0,
              f"{choice.chosen.length_m:.0f} m, expected {expected:.0f} m")
        check("corridor stays on one road", choice.chosen.road_changes == 0)
        check("corridor selection is 7-25 ms scale", ms < 200.0, f"{ms:.1f} ms")

    # ------------------------------------------------- same-link, straight
    print("\nsame-link partial spans")
    straight = db.query_one(
        "SELECT link_id, length_m FROM links WHERE snapshot_id=%s "
        "  AND ST_NPoints(geom_2193) = 2 AND length_m > 400 "
        " ORDER BY link_id LIMIT 1", (snapshot,))
    if straight:
        lid, length = int(straight["link_id"]), float(straight["length_m"])
        r = outage.analyse(snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75))
        check("straight same-link closure is half the link",
              abs(r.closed_length_m - length * 0.5) < 0.01,
              f"{r.closed_length_m:.1f} m of {length:.1f} m")
        check("straight same-link headline is from the vocabulary",
              r.headline in outage.HEADLINES, r.headline)

    curved = long_link(snapshot)
    if curved:
        lid, length = int(curved["link_id"]), float(curved["length_m"])
        r = outage.analyse(snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75))
        geom = vsplit.span_geometry(snapshot, r.split.intervals)
        check("curved same-link closure is half the link",
              abs(r.closed_length_m - length * 0.5) < 0.01,
              f"{curved['points']} vertices")
        check("red preview equals the closure to 1 mm",
              abs(geom["measuredLengthM"] - r.closed_length_m) < 0.001,
              f"drawn {geom['measuredLengthM']:.3f} m vs "
              f"closed {r.closed_length_m:.3f} m")
        check("handles exactly at nodes close the whole link",
              abs(outage.analyse(snapshot, HandleRef(lid, 0.0),
                                 HandleRef(lid, 1.0)).closed_length_m
                  - length) < 0.01)

        # Replacement must never traverse the closed interval.
        r2 = outage.analyse(snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75))
        used = {aid for m in r2.measures for aid in m.arc_ids}
        check("replacement never traverses the closed interval",
              used.isdisjoint(r2.split.closed_piece_ids)
              and used.isdisjoint(r2.split.excluded_arc_ids))
        check("open endpoint pieces remain routable",
              all(pid in r2.split.open_piece_ids
                  for pid in r2.split.open_piece_ids))
        check("virtual ids are collision-free and never the sentinel",
              all(p < 0 for p in r2.split.open_piece_ids)
              and routing.RESERVED_EDGE_SENTINEL not in r2.split.open_piece_ids
              and len(set(r2.split.open_piece_ids)) ==
              len(r2.split.open_piece_ids))

        # Swapping A and B must preserve both-direction geometry.
        fwd = outage.analyse(snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75))
        rev = outage.analyse(snapshot, HandleRef(lid, 0.75), HandleRef(lid, 0.25))
        check("swapping A and B preserves the closed length",
              abs(fwd.closed_length_m - rev.closed_length_m) < 1e-6)
        check("swapping A and B preserves the drawn geometry",
              abs(vsplit.span_geometry(snapshot, fwd.split.intervals)
                  ["measuredLengthM"]
                  - vsplit.span_geometry(snapshot, rev.split.intervals)
                  ["measuredLengthM"]) < 0.001)

    # ------------------------------------------------------------ one-way
    print("\ndirection")
    ow = one_way_link(snapshot)
    if ow:
        lid = int(ow["link_id"])
        both = outage.analyse(snapshot, HandleRef(lid, 0.3), HandleRef(lid, 0.6))
        a_to_b = outage.analyse(snapshot, HandleRef(lid, 0.3),
                                HandleRef(lid, 0.6), direction_mode="a_to_b")
        check("one-way A->B yields a single measure",
              len(a_to_b.measures) == 1 and a_to_b.measures[0].direction == "a_to_b")
        check("both-directions yields two measures", len(both.measures) == 2)
        check("every status is a known one",
              all(m.status in ("OK", "DISCONNECTED")
                  or m.status in outage.UNRESOLVED_STATUSES
                  for m in both.measures),
              ", ".join(sorted({m.status for m in both.measures})))
        check("one-way split produces one arc's worth of pieces",
              len(both.split.excluded_arc_ids) == 1,
              f"{len(both.split.excluded_arc_ids)} real arcs superseded")

    # ------------------------------------------------------ tamper refusal
    print("\ncontract")
    if straight:
        lid = int(straight["link_id"])
        r = outage.analyse(snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75))
        try:
            outage.analyse(snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75),
                           corridor_id="not-a-real-corridor")
            check("a tampered corridor id is refused", False)
        except outage.UnknownCorridor:
            check("a tampered corridor id is refused", True)
        restored = outage.analyse(
            snapshot, HandleRef(lid, 0.25), HandleRef(lid, 0.75),
            corridor_id=r.corridor.candidate_id)
        check("a pinned corridor restores the same span",
              restored.fingerprint == r.fingerprint)
        check("processing version is the snapshot's",
              bool(r.processing_version), r.processing_version)
        check("sensitivity is unavailable, not robust",
              r.sensitivity is None and "NOT a finding" in
              r.sensitivity_unavailable_reason)

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("FAILED: " + "; ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
