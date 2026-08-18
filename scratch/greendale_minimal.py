"""Phase 1 steps 3-6: narrow the counterfactual to the minimal cause, and rule
out every non-topological explanation for the original perimeter route."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from nzcl import db, impactv2, physical, routing, turns, whatif

NATIONAL = "amds-national-2026-07-28-5b359d84"
COPY = "whatif-greendale-min"
LINK = 234872

# The crossing the corrected route actually used.
USED = (232709, 234053, 1525968.9950117653, 5182907.627272354)
# The crossing named in the original report: real, zero-gap, but NOT the one
# that changes this route.
REPORTED = (232708, 234875, 1526312.043495837, 5181822.622474743)


def run(label: str, edits: list[whatif.CrossingEdit]) -> dict:
    whatif.drop_snapshot(COPY)
    whatif.copy_snapshot(NATIONAL, COPY)
    if edits:
        rep = whatif.node_crossings(COPY, edits)
        print(f"  [{label}] noded {rep.crossings_applied}/{rep.crossings_requested}"
              f"  skipped={rep.crossings_skipped}")
    physical.clear_cache()
    out = impactv2.analyse(COPY, LINK, scope="segment", direction=None,
                           metric="distance", profile="car",
                           with_geometry=False)
    p = out.principal
    rows = db.query(
        "SELECT a.arc_id, a.link_id, a.source, a.target, a.cost_distance_m, "
        "       coalesce(dn.display_name, l.road_name) AS name "
        "  FROM arcs a JOIN links l ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id "
        "  LEFT JOIN link_display_names dn ON dn.snapshot_id=a.snapshot_id "
        "        AND dn.link_id=a.link_id "
        " WHERE a.snapshot_id=%s AND a.arc_id = ANY(%s)", (COPY, list(p.arc_ids)))
    by = {r["arc_id"]: r for r in rows}
    d = {
        "label": label,
        "headline": out.headline,
        "status": p.status,
        "replacementDistanceM": p.replacement_distance_m,
        "networkPenaltyM": p.network_penalty_m,
        "arcIds": list(p.arc_ids),
        "linkIds": list(p.link_ids or []),
        "roads": [by[a]["name"] for a in p.arc_ids if a in by],
    }
    print(f"  [{label}] {p.status}  replacement {p.replacement_distance_m:.1f} m  "
          f"penalty {p.network_penalty_m:.1f} m  ({len(p.arc_ids)} arcs)")
    return d


def main() -> int:
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)
    skip_copies = "--no-copies" in sys.argv
    rec: dict = {}
    if skip_copies:
        prev = json.loads((outdir / "greendale-minimal.json").read_text("utf-8"))
        rec.update({k: prev[k] for k in
                    ("onlyUsedCrossing", "onlyReportedCrossing", "bothCrossings")
                    if k in prev})
    else:
        print("=" * 74)
        print("A  minimal cause: node ONLY the crossing the corrected route used")
        print("=" * 74)
        rec["onlyUsedCrossing"] = run(
            "only 232709 x 234053",
            [whatif.CrossingEdit(USED[0], USED[1], USED[2], USED[3])])

        print()
        print("=" * 74)
        print("B  the crossing named in the original report, on its own")
        print("=" * 74)
        rec["onlyReportedCrossing"] = run(
            "only 232708 x 234875",
            [whatif.CrossingEdit(REPORTED[0], REPORTED[1], REPORTED[2], REPORTED[3])])

        print()
        print("=" * 74)
        print("C  both together")
        print("=" * 74)
        rec["bothCrossings"] = run("both", [
            whatif.CrossingEdit(USED[0], USED[1], USED[2], USED[3]),
            whatif.CrossingEdit(REPORTED[0], REPORTED[1], REPORTED[2], REPORTED[3])])

    print()
    print("=" * 74)
    print("D  rule out turn restrictions")
    print("=" * 74)
    n_all = db.query_one(
        "SELECT count(*) n FROM turn_restrictions WHERE snapshot_id=%s",
        (NATIONAL,))["n"]
    per_mode = db.query_one(
        "SELECT count(*) FILTER (WHERE restricted_vehicle) AS car, "
        "       count(*) FILTER (WHERE restricted_heavy) AS heavy, "
        "       count(*) FILTER (WHERE restricted_emergency) AS emerg "
        "  FROM turn_restrictions WHERE snapshot_id=%s", (NATIONAL,))
    print(f"  turn restrictions in the national snapshot : {n_all}")
    print(f"  restricting car / heavy / emergency        : "
          f"{per_mode['car']} / {per_mode['heavy']} / {per_mode['emerg']}")
    seqs = turns.restricted_sequences(NATIONAL, "car")
    print(f"  sequences applicable to a car              : {len(seqs)}")
    involved = sorted({lid for s in seqs for lid in s})
    print(f"  links named by ANY car restriction         : {involved}")
    near = db.query_one(
        "SELECT count(*) n FROM links WHERE snapshot_id=%s AND link_id = ANY(%s) "
        "  AND ST_DWithin(geom_2193, (SELECT ST_Buffer(geom_2193, 20000) FROM links "
        "      WHERE snapshot_id=%s AND link_id=%s), 0)",
        (NATIONAL, involved, NATIONAL, LINK))["n"] if involved else 0
    print(f"  ...of those, within 20 km of the closure   : {near}")
    rec["turnRestrictions"] = {
        "total": n_all, "car": per_mode["car"], "heavy": per_mode["heavy"],
        "emergency": per_mode["emerg"], "applicableSequences": len(seqs),
        "linksNamed": involved, "within20kmOfClosure": near,
    }

    print()
    print("=" * 74)
    print("E  rule out vehicle-mode rules and one-way rules")
    print("=" * 74)
    # Every link on the shortcut, and on the perimeter route, under every mode.
    shortcut_links = [232709, 234053, 234054, 234055, 234056, 234057, 234058,
                      234328, 234329, 230514, 230515, 234871, 233119, 234874,
                      234873]
    rows = db.query(
        "SELECT l.link_id, l.oneway, l.forward_allowed, l.reverse_allowed, "
        "       l.mode_vehicle, l.mode_vehicle_heavy, l.mode_emergency, l.speed_kph, "
        "       coalesce(dn.display_name, l.road_name) AS name "
        "  FROM links l LEFT JOIN link_display_names dn "
        "         ON dn.snapshot_id=l.snapshot_id AND dn.link_id=l.link_id "
        " WHERE l.snapshot_id=%s AND l.link_id = ANY(%s) ORDER BY link_id",
        (NATIONAL, shortcut_links))
    bad = []
    for r in rows:
        flag = ""
        if not (r["forward_allowed"] and r["reverse_allowed"]):
            flag += " ONE-WAY"
        if not r["mode_vehicle"]:
            flag += " NOT-CAR"
        if flag:
            bad.append(r["link_id"])
        print(f"    link {r['link_id']:>7} oneway={r['oneway']} "
              f"fwd={r['forward_allowed']} rev={r['reverse_allowed']} "
              f"car={r['mode_vehicle']} heavy={r['mode_vehicle_heavy']} "
              f"emerg={r['mode_emergency']} {r['name'] or ''}{flag}")
    print(f"  links on the shortcut that are one-way or not car-eligible: "
          f"{bad or 'NONE'}")
    rec["shortcutLinkPermissions"] = {"oneWayOrNotCar": bad,
                                      "checked": len(rows)}

    # And: the baseline result is identical under the least restrictive profile.
    physical.clear_cache()
    emerg = impactv2.analyse(NATIONAL, LINK, scope="segment", direction=None,
                             metric="distance", profile="emergency",
                             with_geometry=False)
    print(f"  national baseline under profile=emergency  : "
          f"{emerg.principal.status} {emerg.principal.replacement_distance_m:.1f} m")
    rec["baselineEmergencyProfile"] = {
        "status": emerg.principal.status,
        "replacementDistanceM": emerg.principal.replacement_distance_m,
        "arcIds": list(emerg.principal.arc_ids),
    }

    print()
    print("=" * 74)
    print("F  the two links really do meet, in the source data")
    print("=" * 74)
    g = db.query_one(
        "SELECT ST_Distance(a.geom_2193, b.geom_2193) AS gap, "
        "       ST_AsText(ST_Intersection(a.geom_2193, b.geom_2193)) AS pt, "
        "       a.source_node AS a_s, a.target_node AS a_t, "
        "       b.source_node AS b_s, b.target_node AS b_t, "
        "       a.closure_group_id AS ga, b.closure_group_id AS gb "
        "  FROM links a, links b "
        " WHERE a.snapshot_id=%s AND b.snapshot_id=%s AND a.link_id=%s AND b.link_id=%s",
        (NATIONAL, NATIONAL, USED[0], USED[1]))
    print(f"  links {USED[0]} x {USED[1]}")
    print(f"    separation           : {g['gap']:.6f} m")
    print(f"    intersection         : {g['pt']}")
    print(f"    node sets            : {{{g['a_s']}, {g['a_t']}}} vs "
          f"{{{g['b_s']}, {g['b_t']}}}")
    print(f"    source features      : {g['ga']}  /  {g['gb']}")
    nm = db.query_one(
        "SELECT count(*) n FROM near_misses WHERE snapshot_id=%s AND "
        "  ST_DWithin(geom_2193, ST_SetSRID(ST_MakePoint(%s,%s),2193), 25)",
        (NATIONAL, USED[2], USED[3]))["n"]
    print(f"    near misses within 25 m of it: {nm}  "
          f"(so topologyConfidence cannot see it)")
    rec["crossingEvidence"] = {
        "gapM": g["gap"], "intersection": g["pt"],
        "nodeSetA": [g["a_s"], g["a_t"]], "nodeSetB": [g["b_s"], g["b_t"]],
        "sourceFeatureA": g["ga"], "sourceFeatureB": g["gb"],
        "nearMissesWithin25m": nm,
    }

    whatif.drop_snapshot(COPY)
    print(f"\nrolled back: dropped {COPY}")
    left = db.query_one("SELECT count(*) n FROM links WHERE snapshot_id=%s",
                        (COPY,))["n"]
    nat = db.query_one("SELECT count(*) n FROM links WHERE snapshot_id=%s",
                       (NATIONAL,))["n"]
    print(f"  links left in the copy: {left};  national snapshot links: {nat}")
    rec["rollback"] = {"copyLinksRemaining": left, "nationalLinks": nat}

    with (outdir / "greendale-minimal.json").open("w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, default=str)
    print(f"wrote {outdir / 'greendale-minimal.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
