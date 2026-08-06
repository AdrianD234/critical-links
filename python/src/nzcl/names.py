"""The naming pipeline: backfill from AMDS, then enrich from external sources.

Run against an existing snapshot. Nothing here writes to `links`, `nodes`,
`arcs`, `arc_transitions` or `closure` structures, so a naming pass cannot
change a routing answer - and the proof of that is an equality check, not an
assurance (see `nzcl-names verify`).

    nzcl-names backfill   read AMDS tables 11 and 13 properly, populate
                          link_names for a snapshot
    nzcl-names report     naming coverage for a snapshot
    nzcl-names verify     prove topology and routing are unchanged
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from . import db
from .config import get_settings
from .naming import (
    AMBIGUOUS_CONFLICT,
    AMDS_NAMED,
    EPOCH_MS_9999,
    OFFICIALLY_UNNAMED,
    ROUTE_DESIGNATION_ONLY,
    UNRESOLVED,
    NameSelection,
    build_candidates,
    select_amds_name,
)
from .namesources import acquire_amds_route_names, latest_cache, read_ndjson

#: Bump when a naming rule changes. Recorded on every run so a coverage figure
#: can be traced to the rules that produced it.
NAMING_VERSION = "1.0.0"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def default_snapshot() -> str:
    """Newest complete national snapshot; never a partial or synthetic one."""
    row = db.query_one(
        "SELECT snapshot_id FROM network_snapshots "
        " WHERE status = 'complete' AND coverage_kind = 'national' "
        " ORDER BY retrieved_at_utc DESC LIMIT 1"
    )
    if row:
        return row["snapshot_id"]
    row = db.query_one(
        "SELECT snapshot_id FROM network_snapshots "
        " WHERE status = 'complete' ORDER BY retrieved_at_utc DESC LIMIT 1"
    )
    if not row:
        raise SystemExit("no complete snapshot in the database")
    return row["snapshot_id"]


def _ts(ms: int | None) -> datetime | None:
    """Epoch milliseconds to a timestamp, treating AMDS sentinels as open."""
    if ms is None or ms >= EPOCH_MS_9999:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# backfill
# --------------------------------------------------------------------------

def backfill(snapshot_id: str, *, refresh: bool = False,
             now_ms: int | None = None) -> dict[str, Any]:
    """Populate link_names for every source feature in a snapshot.

    Source features that carry no route name at all still get a row, with
    status `unresolved`. An absent row and a row that says "nothing found" are
    different facts, and only the second one can be counted.
    """
    now_ms = now_ms or _now_ms()
    print(f"snapshot {snapshot_id}")

    print("\n  AMDS route names")
    detail_path, join_path = acquire_amds_route_names(refresh=refresh)
    detail = read_ndjson(detail_path)
    join = read_ndjson(join_path)
    print(f"  table 11: {len(detail)} route names")
    print(f"  table 13: {len(join)} join rows")

    candidates = build_candidates(join, detail)
    print(f"  {len(candidates)} source features carry at least one route name")

    groups = [r["closure_group_id"] for r in db.query(
        "SELECT DISTINCT closure_group_id FROM links WHERE snapshot_id = %s",
        (snapshot_id,))]
    print(f"  {len(groups)} source features in the snapshot")

    counts: dict[str, int] = {}
    conflicts = 0
    written = 0

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM link_names WHERE snapshot_id = %s",
                        (snapshot_id,))
            with cur.copy(
                "COPY link_names (snapshot_id, closure_group_id, display_name, "
                "name_status, name_source, source_field, native_name, "
                "native_name_key, route_designation, designation_raw, "
                "alternates, route_name_ids, primary_route_name_id, "
                "effective_from, effective_to, conflict, is_ramp, "
                "locality_code, notes) FROM STDIN"
            ) as cp:
                for gid in groups:
                    sel = select_amds_name(candidates.get(gid, ()), now_ms=now_ms)
                    counts[sel.name_status] = counts.get(sel.name_status, 0) + 1
                    conflicts += int(sel.conflict)
                    cp.write_row((
                        snapshot_id, gid, sel.display_name, sel.name_status,
                        sel.name_source, sel.source_field, sel.native_name,
                        sel.native_name_key, sel.route_designation,
                        sel.designation_raw, list(sel.alternates),
                        list(sel.route_name_ids), sel.primary_route_name_id,
                        _ts(sel.effective_from), _ts(sel.effective_to),
                        sel.conflict, sel.is_ramp, sel.locality_code,
                        list(sel.notes),
                    ))
                    written += 1
        conn.commit()

    _record_run(snapshot_id, "backfill-amds", {
        "amds_route_names": len(detail),
        "amds_join_rows": len(join),
        "source_features": len(groups),
        "rows_written": written,
        "by_status": counts,
        "conflicts": conflicts,
        "now_ms": now_ms,
    })
    return {"written": written, "by_status": counts, "conflicts": conflicts}


def _record_run(snapshot_id: str, stage: str, counts: dict[str, Any],
                sources: dict[str, Any] | None = None,
                notes: list[str] | None = None) -> None:
    run_id = f"{stage}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    db.execute(
        "INSERT INTO naming_runs (snapshot_id, run_id, stage, finished_at_utc, "
        "naming_version, sources, counts, notes) "
        "VALUES (%s, %s, %s, now(), %s, %s, %s, %s) "
        "ON CONFLICT (snapshot_id, run_id) DO UPDATE SET counts = EXCLUDED.counts",
        (snapshot_id, run_id, stage, NAMING_VERSION,
         json.dumps(sources or {}), json.dumps(counts), notes or []),
    )


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def report(snapshot_id: str) -> dict[str, Any]:
    """Naming coverage at both levels. The two are not interchangeable.

    A source feature split into six graph links contributes one to the source
    figure and six to the graph figure, so a cohort quoted at one level and
    compared at the other will look like a different network.
    """
    src = db.query(
        "SELECT name_status, count(*) AS n FROM link_names "
        " WHERE snapshot_id = %s GROUP BY name_status ORDER BY n DESC",
        (snapshot_id,))
    lnk = db.query(
        "SELECT name_status, count(*) AS n FROM link_display_names "
        " WHERE snapshot_id = %s GROUP BY name_status ORDER BY n DESC",
        (snapshot_id,))
    totals = db.query_one(
        "SELECT count(*) AS links, count(DISTINCT closure_group_id) AS groups "
        "  FROM links WHERE snapshot_id = %s", (snapshot_id,))
    named_links = db.query_one(
        "SELECT count(*) AS n FROM link_display_names "
        " WHERE snapshot_id = %s AND display_name IS NOT NULL", (snapshot_id,))
    legacy = db.query_one(
        "SELECT count(*) AS n FROM links "
        " WHERE snapshot_id = %s AND road_name IS NOT NULL", (snapshot_id,))

    out = {
        "snapshot_id": snapshot_id,
        "source_features": totals["groups"],
        "graph_links": totals["links"],
        "by_status_source": {r["name_status"]: r["n"] for r in src},
        "by_status_link": {r["name_status"]: r["n"] for r in lnk},
        "named_links_now": named_links["n"],
        "named_links_before": legacy["n"],
    }

    print(f"snapshot {snapshot_id}")
    print(f"  source features {totals['groups']:,}   graph links {totals['links']:,}")
    print("\n  by name state (source features)")
    for r in src:
        print(f"    {r['name_status']:<24} {r['n']:>9,}"
              f"  {100 * r['n'] / totals['groups']:5.1f}%")
    print("\n  by name state (graph links)")
    for r in lnk:
        print(f"    {r['name_status']:<24} {r['n']:>9,}"
              f"  {100 * r['n'] / totals['links']:5.1f}%")
    print(f"\n  graph links with a name: {named_links['n']:,} "
          f"({100 * named_links['n'] / totals['links']:.1f}%)")
    print(f"  before this work:        {legacy['n']:,} "
          f"({100 * legacy['n'] / totals['links']:.1f}%)")
    return out


# --------------------------------------------------------------------------
# no-change proof
# --------------------------------------------------------------------------

#: Everything a routing answer depends on. If all of these are identical before
#: and after a naming pass, no detour result can have moved - the search reads
#: no other table. `road_name` is deliberately included: naming must not
#: rewrite the column the old pipeline wrote, because that column is part of
#: the evidence that the snapshot is the one the results were computed from.
_FINGERPRINT_SQL = {
    "nodes": "SELECT count(*) AS n, sum(node_id) AS s, sum(component_id) AS c "
             "  FROM nodes WHERE snapshot_id = %(snap)s",
    "links": "SELECT count(*) AS n, sum(source_node) AS src, "
             "       sum(target_node) AS tgt, "
             "       round(sum(length_m)::numeric, 3) AS len, "
             "       count(road_name) AS named "
             "  FROM links WHERE snapshot_id = %(snap)s",
    "arcs": "SELECT count(*) AS n, sum(source) AS src, sum(target) AS tgt, "
            "       round(sum(cost_distance_m)::numeric, 3) AS cost "
            "  FROM arcs WHERE snapshot_id = %(snap)s",
    "arc_transitions": "SELECT count(*) AS n, sum(from_arc) AS f, "
                       "       sum(to_arc) AS t, sum(via_node) AS v "
                       "  FROM arc_transitions WHERE snapshot_id = %(snap)s",
    "closure_groups": "SELECT count(DISTINCT closure_group_id) AS n "
                      "  FROM links WHERE snapshot_id = %(snap)s",
    "geometry": "SELECT md5(string_agg(h, '' ORDER BY h)) AS digest FROM ("
                "  SELECT md5(link_id::text || ':' || source_node || ':' || "
                "             target_node || ':' || "
                "             round(length_m::numeric, 6)::text) AS h "
                "    FROM links WHERE snapshot_id = %(snap)s) t",
}


def fingerprint(snapshot_id: str) -> dict[str, Any]:
    """A digest of every table a route search reads."""
    out: dict[str, Any] = {}
    for name, sql in _FINGERPRINT_SQL.items():
        row = db.query_one(sql, {"snap": snapshot_id}) or {}
        out[name] = {k: (str(v) if v is not None else None) for k, v in row.items()}
    return out


#: Deliberately includes the link from the reported screenshot, so the case
#: that motivated the work is also the case whose routing answer is pinned.
PROBE_ANCHOR_LINKS = (373604,)


def probe_links(snapshot_id: str, sample: int) -> list[int]:
    """A reproducible spread of links to route over.

    Evenly spaced by link_id rather than randomly drawn: no seed to record, no
    way for the "before" and "after" runs to disagree about which links they
    compared.
    """
    total = (db.query_one("SELECT count(*) AS n FROM links WHERE snapshot_id = %s",
                          (snapshot_id,)) or {})["n"]
    if not total:
        return []
    step = max(1, total // sample)
    rows = db.query(
        "SELECT link_id FROM (SELECT link_id, row_number() OVER (ORDER BY link_id) "
        "  AS rn FROM links WHERE snapshot_id = %s) t WHERE rn %% %s = 1",
        (snapshot_id, step))
    ids = [r["link_id"] for r in rows][:sample]
    for anchor in PROBE_ANCHOR_LINKS:
        if anchor not in ids and db.query_one(
                "SELECT 1 AS ok FROM links WHERE snapshot_id = %s AND link_id = %s",
                (snapshot_id, anchor)):
            ids.append(anchor)
    return sorted(ids)


def routing_probe(snapshot_id: str, sample: int = 40) -> dict[str, Any]:
    """Run real detours and record every number they produce.

    The fingerprint proves the inputs did not move. This proves the outputs did
    not either, which is the claim that actually matters to a reader.
    """
    from .detour import compute  # local: pulls in routing, not needed to report

    out: dict[str, Any] = {}
    ids = probe_links(snapshot_id, sample)
    for i, link_id in enumerate(ids, 1):
        print(f"\r  routing probe {i}/{len(ids)} (link {link_id})",
              end="", flush=True)
        try:
            res = compute(snapshot_id, link_id, compute_corridor=True)
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            out[str(link_id)] = {"error": type(exc).__name__}
            continue
        entry: dict[str, Any] = {}
        for direction in ("forward", "reverse"):
            dr = getattr(res, direction)
            if dr is None:
                continue
            entry[direction] = {
                "status": dr.status,
                "source_node": dr.source_node,
                "target_node": dr.target_node,
                "alternative_distance_m": _round(dr.alternative_distance_m),
                "added_distance_vs_link_m": _round(dr.added_distance_vs_link_m),
                "network_penalty_m": _round(dr.network_penalty_m),
                "route_link_count": len(dr.route_link_ids),
                "route_link_digest": _digest(dr.route_link_ids),
                "corridor_penalty_m": _round(
                    dr.corridor.penalty_m if dr.corridor else None),
                "isolation_links": (dr.isolation.pocket_link_count
                                    if dr.isolation else None),
            }
        entry["removed_link_ids"] = res.removed_link_ids
        out[str(link_id)] = entry
    print()
    return out


def _round(v: float | None) -> str | None:
    return None if v is None else f"{v:.6f}"


def _digest(values: list[int]) -> str:
    import hashlib
    return hashlib.md5(",".join(str(v) for v in values).encode()).hexdigest()


def verify(snapshot_id: str, baseline_path: str | None,
           save_path: str | None, *, probe: int = 0) -> int:
    current = fingerprint(snapshot_id)
    if probe:
        current["routing"] = routing_probe(snapshot_id, probe)
    if save_path:
        with open(save_path, "w", encoding="utf-8") as fh:
            json.dump({"snapshot_id": snapshot_id, "fingerprint": current},
                      fh, indent=2)
        print(f"fingerprint written to {save_path}")
    if not baseline_path:
        print(json.dumps(current, indent=2))
        return 0

    with open(baseline_path, encoding="utf-8") as fh:
        baseline = json.load(fh)["fingerprint"]
    drift = [k for k in current if current[k] != baseline.get(k)]
    for k in sorted(current):
        mark = "CHANGED" if k in drift else "unchanged"
        print(f"  {k:<18} {mark}")
        if k in drift:
            print(f"      before {baseline.get(k)}")
            print(f"      after  {current[k]}")
    if drift:
        print(f"\nFAIL: {len(drift)} topology fingerprints changed", file=sys.stderr)
        return 1
    print("\nPASS: every topology and cost fingerprint is byte-identical")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def acquire_and_stage(sources: list[str], *, refresh: bool) -> None:
    """Download (or reuse) each external source and load it into PostGIS."""
    from . import namesources as ns

    getters = {
        "nzta_street_names": ns.acquire_nzta_street_names,
        "linz_road_sections": ns.acquire_linz_road_sections,
        "nzta_ramm_carriageway": ns.acquire_nzta_ramm,
    }
    for source in sources:
        print(f"\n  {source}")
        getters[source](refresh=refresh)
        out = ns.stage(source)
        print(f"    staged {out['rows']:,} rows from {out['features']:,} features")


def main() -> int:
    p = argparse.ArgumentParser(prog="nzcl-names", description=__doc__)
    p.add_argument("command",
                   choices=["backfill", "report", "verify", "sources"])
    p.add_argument("--source", action="append", default=None,
                   help="sources: limit to these external sources")
    p.add_argument("--snapshot", default=None)
    p.add_argument("--refresh", action="store_true",
                   help="re-download the AMDS route-name tables")
    p.add_argument("--baseline", default=None,
                   help="verify: fingerprint file to compare against")
    p.add_argument("--save", default=None,
                   help="verify: write the current fingerprint here")
    p.add_argument("--probe", type=int, default=0,
                   help="verify: also run this many real detours and record "
                        "every number they produce")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    db.migrate()

    if args.command == "sources":
        from .namesources import SOURCES
        acquire_and_stage(args.source or list(SOURCES), refresh=args.refresh)
        return 0

    snapshot = args.snapshot or default_snapshot()

    if args.command == "verify":
        return verify(snapshot, args.baseline, args.save, probe=args.probe)

    if args.command == "backfill":
        out = backfill(snapshot, refresh=args.refresh)
        print("\n  by name state")
        for k, v in sorted(out["by_status"].items(), key=lambda kv: -kv[1]):
            print(f"    {k:<24} {v:>9,}")
        print(f"    conflicts flagged: {out['conflicts']}")
    else:
        out = report(snapshot)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":  # python -m nzcl.names
    raise SystemExit(main())
