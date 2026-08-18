"""A FRESH blinded holdout, drawn independently of the 208-card pack.

Why a second pack exists at all
-------------------------------
The 208-card blinded pack did its job: it overturned a claimed "100%
precision", and it exposed two systematic failures. Both were then FIXED using
that same pack - the tangential threshold moved from 20 to 30 degrees because
its 20-30 band came back 8/8 wrong, and DUPLICATE_GEOMETRY was written because
eleven of its seventeen misses were one road recorded twice.

That makes those 208 cards DEVELOPMENT DATA. Re-scoring the revised classifier
against them would measure it on the sample its rules were derived from, and
the number would be optimistic by construction. They are labelled as
development data in the audit and they are excluded here.

What "independent" means, precisely
-----------------------------------
Excluded from the draw:

  * every link pair in scratch/blind/answer-key.json      (the 208-card pack)
  * every link pair in review-verdicts.json               (the earlier
                                                           unblinded pack)
  * every candidate whose crossing point lies within
    INDEPENDENCE_M of any of the above

The last one is the one that matters. Excluding pairs alone would still let a
neighbouring pair AT THE SAME PHYSICAL INTERSECTION into the holdout - four
pairs per crossing of two divided carriageways - which would be the same case
wearing a different id. The project's own definition of "one place" is 25 m;
50 m is used here so a holdout card cannot even be a near neighbour of a
development card.

Blinding
--------
Identical to pass 1, and for the same reason: the card shows imagery, the two
centrelines, the crossing point, a code, the ids and the names. The
classifier's disposition and deciding rule are written to a separate answer
key and joined only after every verdict is recorded. Order is randomised
across strata with a NEW seed.

    python ../scratch/holdout_review.py build  <outdir>
    python ../scratch/holdout_review.py score  <outdir> <verdicts.json>
    python ../scratch/holdout_review.py recode <outdir> <n>
    python ../scratch/holdout_review.py agree  <outdir> <pass1.json> <pass2.json>
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import sys
from pathlib import Path

from nzcl import db
from nzcl.config import REPO_ROOT

# A different seed from the development pack, so no card can be drawn because
# of where it sat in the first ordering.
SEED = "at-grade-holdout-2026-08-18"
SNAP = "amds-national-2026-07-28-5b359d84"
ZOOM = 18
TILE = 256
CARD = 400

#: No holdout card may lie within this of a development card. The place
#: convention is 25 m; this is deliberately wider. See the module docstring.
INDEPENDENCE_M = 50.0

#: No single Road Controlling Authority may supply more than this share of the
#: AT_GRADE draw. Without it the biggest councils dominate every cell and the
#: pack measures Auckland rather than New Zealand.
RCA_CAP_FRAC = 0.12

#: The AT_GRADE strata. These are NOT the pass-1 cells: they are re-aimed at
#: the thresholds as they now stand, because a holdout that retests the old
#: boundaries measures a classifier that no longer exists.
AT_GRADE_CELLS: dict[str, tuple[str, int]] = {
    # THE cell. 30 degrees is the new tangential threshold and this band sits
    # immediately above it. If the threshold is still in the wrong place, the
    # evidence is here.
    "angle_30_40":      ("angleDeg >= 30 and angleDeg < 40", 22),
    "angle_40_60":      ("angleDeg >= 40 and angleDeg < 60", 16),
    "angle_60_80":      ("angleDeg >= 60 and angleDeg < 80", 16),
    "angle_80_90":      ("angleDeg >= 80", 20),
    # Just outside the WIDENED 25 m structure radius. The equivalent pass-1
    # cell was 15-60 m and it caught the only confirmed false positive.
    "structure_near":   ("structDistM is not None and 25 < structDistM <= 70", 16),
    # Adversarial: the two roads carry the SAME name but DUPLICATE_GEOMETRY
    # did not fire. If the new rule is too narrow, these are the survivors.
    "same_name_not_dup": ("nameA and nameB and nameA == nameB "
                          "and not duplicateCorridor", 14),
    # Unsealed, and unnamed on at least one side: the best proxy the source
    # offers for forestry, industrial and private-looking access roads.
    "unsealed_access":  ("(surfA in (2, 3) or surfB in (2, 3))", 14),
    "unnamed_both":     ("not nameA and not nameB", 12),
    "state_highway":    ("rcaA == 1 or rcaB == 1", 12),
    "urban":            ("urA == 'urban' and urB == 'urban'", 12),
    "rural":            ("urA == 'rural' and urB == 'rural'", 14),
    "junction_witness": ("reason == 'JUNCTION_WITNESS'", 12),
    # Imagery age, from the LINZ Basemaps attribution layer.
    #
    # This cell was first written as "<= 2018" and drew ZERO candidates.
    # Basemaps serves the CURRENT mosaic, not an archive: nationally the
    # oldest survey over any crossing is 2019, and 2024-2026 covers most of
    # the country. So imagery age barely varies, and a stratum asking for old
    # imagery cannot be filled. It is retuned to the oldest band that actually
    # exists rather than left designed-but-empty, and the reviewer records
    # obscuration per card instead, which is the part that really varies -
    # cloud, shadow, tree canopy and time-of-day are what make a card hard,
    # not the survey year.
    "imagery_older":    ("imageryYear is not None and imageryYear <= 2023", 14),
    "imagery_unknown":  ("imageryYear is None", 2),
}

#: Decoys, so the pack is not "here are N crossings, all AT_GRADE". They are
#: scored too - a GRADE_SEPARATED the reviewer calls at-grade is a false
#: NEGATIVE, which costs connectivity rather than inventing it.
DECOY_CELLS: dict[str, tuple[str, int]] = {
    "gs_structure":  ("disposition == 'GRADE_SEPARATED' and reason == 'STRUCTURE_MAPPED'", 10),
    "gs_motorway":   ("disposition == 'GRADE_SEPARATED' and reason == 'MOTORWAY_CARRIAGEWAY'", 8),
    "gs_other":      ("disposition == 'GRADE_SEPARATED' and reason not in "
                      "('STRUCTURE_MAPPED','MOTORWAY_CARRIAGEWAY')", 6),
    "un_no_evidence": ("disposition == 'UNRESOLVED' and reason == 'NO_EVIDENCE_EITHER_WAY'", 10),
    # The new rule, as a decoy: if DUPLICATE_GEOMETRY is over-firing it is
    # withdrawing real junctions, and that is a cost worth measuring.
    "un_duplicate":  ("disposition == 'UNRESOLVED' and reason == 'DUPLICATE_GEOMETRY'", 10),
    "un_tangential": ("disposition == 'UNRESOLVED' and reason == 'TANGENTIAL'", 8),
}

AUDIT = REPO_ROOT / "docs/audits/at-grade-crossings"


# --------------------------------------------------------------------------
def rank(r: dict, salt: str = "") -> str:
    return hashlib.md5(
        f"{SEED}|{salt}|{r['linkA']}|{r['linkB']}".encode()).hexdigest()


def lonlat_to_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def linz_key() -> str:
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_LINZ_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no VITE_LINZ_API_KEY in .env")


def load_rows() -> list[dict]:
    src = AUDIT / "classified.jsonl"
    if not src.exists():
        raise SystemExit(
            f"{src} is not present. It is derived and gitignored; regenerate "
            f"it with the steps in scratch/README.md.")
    return [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# --------------------------------------------------------------------------
def development_points(rows: list[dict]) -> tuple[set[tuple[int, int]],
                                                  list[tuple[float, float]]]:
    """Every crossing already seen by a reviewer, as pairs AND as points."""
    pairs: set[tuple[int, int]] = set()

    key_path = REPO_ROOT / "scratch/blind/answer-key.json"
    if key_path.exists():
        for c in json.loads(key_path.read_text(encoding="utf-8"))["cards"]:
            pairs.add((int(c["linkA"]), int(c["linkB"])))
    else:
        print("  WARNING: scratch/blind/answer-key.json absent. The 208-card "
              "development pack cannot be excluded by pair. Regenerate it "
              "with scratch/blind_review.py build before drawing a holdout.")

    rv = AUDIT / "review-verdicts.json"
    if rv.exists():
        for v in json.loads(rv.read_text(encoding="utf-8"))["verdicts"]:
            pairs.add((int(v["linkA"]), int(v["linkB"])))

    by_pair = {(int(r["linkA"]), int(r["linkB"])): r for r in rows}
    pts = [(float(by_pair[p]["x"]), float(by_pair[p]["y"]))
           for p in pairs if p in by_pair]
    return pairs, pts


def independent(rows: list[dict]) -> list[dict]:
    pairs, pts = development_points(rows)
    print(f"development set: {len(pairs)} link pairs, "
          f"{len(pts)} of them locatable in the current record")

    # Grid index at the exclusion radius: O(n) rather than 13,056 x 289.
    cell = INDEPENDENCE_M
    grid: dict[tuple[int, int], list[tuple[float, float]]] = \
        collections.defaultdict(list)
    for x, y in pts:
        grid[(int(x // cell), int(y // cell))].append((x, y))

    kept, drop_pair, drop_near = [], 0, 0
    for r in rows:
        if (int(r["linkA"]), int(r["linkB"])) in pairs:
            drop_pair += 1
            continue
        x, y = float(r["x"]), float(r["y"])
        gx, gy = int(x // cell), int(y // cell)
        near = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py in grid.get((gx + dx, gy + dy), ()):
                    if math.hypot(x - px, y - py) <= INDEPENDENCE_M:
                        near = True
                        break
                if near:
                    break
            if near:
                break
        if near:
            drop_near += 1
            continue
        kept.append(r)

    print(f"excluded {drop_pair} by link pair and a further {drop_near} "
          f"within {INDEPENDENCE_M:.0f} m of a development card")
    print(f"{len(kept)} of {len(rows)} crossings remain eligible")
    return kept


# --------------------------------------------------------------------------
def attach_imagery_year(rows: list[dict]) -> None:
    """Tag each crossing with the year of the LINZ imagery over it.

    From the Basemaps attribution layer, which is the same imagery the review
    cards render. Age is not in the road data and cannot be guessed from it,
    so without this it could only be noted after the fact.
    """
    from pyproj import Transformer
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    cache = REPO_ROOT / "scratch/_linz_attribution.json"
    if cache.exists():
        doc = json.loads(cache.read_text(encoding="utf-8"))
    else:
        import requests
        url = ("https://basemaps.linz.govt.nz/v1/attribution/aerial/"
               f"EPSG:3857/summary.json?api={linz_key()}")
        doc = requests.get(url, timeout=60).json()
        # The cache is scratch/_* and gitignored: it is a raw service payload,
        # re-fetchable by this function. It carries no key.
        cache.write_text(json.dumps(doc), encoding="utf-8")

    polys, years = [], []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        cat = (p.get("category") or "")
        if "Aerial Photos" not in cat:
            continue  # bathymetry, satellite basemaps: not what we look at
        dt = p.get("end_datetime") or p.get("start_datetime") or p.get("datetime")
        if not dt:
            continue
        try:
            g = shape(f["geometry"])
        except Exception:  # noqa: BLE001
            continue
        polys.append(g)
        years.append(int(str(dt)[:4]))
    print(f"imagery attribution: {len(polys)} aerial survey footprints")

    tree = STRtree(polys)
    t = Transformer.from_crs(2193, 4326, always_xy=True)
    hit = 0
    for r in rows:
        lon, lat = t.transform(float(r["x"]), float(r["y"]))
        p = Point(lon, lat)
        best = None
        for k in tree.query(p):
            if polys[int(k)].contains(p):
                y = years[int(k)]
                best = y if best is None else max(best, y)
        r["imageryYear"] = best
        if best is not None:
            hit += 1
    print(f"  matched a survey year for {hit} of {len(rows)} crossings")


# --------------------------------------------------------------------------
def draw(rows: list[dict], cells: dict, disposition: str | None,
         rca_cap: int | None = None) -> list[dict]:
    pool = [r for r in rows
            if disposition is None or r["disposition"] == disposition]
    picked: dict[tuple[int, int], dict] = {}
    rca_used: collections.Counter = collections.Counter()

    def rca_of(r: dict) -> str:
        return str(r.get("rcaNameA") or r.get("rcaNameB") or "unknown")

    for cell, (expr, n) in cells.items():
        cand = []
        for r in pool:
            try:
                if eval(expr, {"__builtins__": {}}, dict(r)):  # noqa: S307
                    cand.append(r)
            except Exception:  # noqa: BLE001
                continue
        cand.sort(key=lambda r: rank(r, cell))
        taken, skipped_cap = 0, 0
        # Two passes: honour the RCA cap first, then relax it rather than
        # under-fill a cell. Under-filling would quietly change the design.
        for relaxed in (False, True):
            for r in cand:
                if taken >= n:
                    break
                key = (r["linkA"], r["linkB"])
                if key in picked:
                    continue
                if not relaxed and rca_cap is not None \
                        and rca_used[rca_of(r)] >= rca_cap:
                    skipped_cap += 1
                    continue
                picked[key] = dict(r, cell=cell)
                rca_used[rca_of(r)] += 1
                taken += 1
            if taken >= n:
                break
        note = f" ({skipped_cap} deferred by the RCA cap)" if skipped_cap else ""
        if taken < n:
            print(f"  cell {cell}: only {taken} available, wanted {n}{note}")
        else:
            print(f"  cell {cell}: {taken}{note}")
    return list(picked.values())


def build(outdir: Path, per_page: int = 9) -> int:
    rows = load_rows()
    print(f"{len(rows)} classified crossing pairs in the national record")
    eligible = independent(rows)
    attach_imagery_year(eligible)

    n_at_grade_target = sum(n for _, n in AT_GRADE_CELLS.values())
    cap = max(4, int(n_at_grade_target * RCA_CAP_FRAC))
    print(f"\nAT_GRADE draw (RCA cap {cap} per authority):")
    at_grade = draw(eligible, AT_GRADE_CELLS, "AT_GRADE", rca_cap=cap)
    print("\ndecoy draw:")
    decoys = draw(eligible, DECOY_CELLS, None)

    sample = at_grade + decoys
    print(f"\ndrew {len(at_grade)} AT_GRADE + {len(decoys)} decoys "
          f"= {len(sample)} cards")

    import random
    rng = random.Random(SEED)
    rng.shuffle(sample)
    for i, r in enumerate(sample, 1):
        r["code"] = f"H{i:03d}"

    ids = sorted({r["linkA"] for r in sample} | {r["linkB"] for r in sample})
    geo = {g["link_id"]: json.loads(g["gj"]) for g in db.query(
        "SELECT link_id, ST_AsGeoJSON(geom_4326, 7) AS gj FROM links "
        " WHERE snapshot_id=%s AND link_id = ANY(%s)", (SNAP, ids))}

    outdir.mkdir(parents=True, exist_ok=True)
    key = linz_key()
    pages = [sample[i:i + per_page] for i in range(0, len(sample), per_page)]
    for pi, page in enumerate(pages, 1):
        html = [_HEAD.replace("{PAGE}", f"{pi} of {len(pages)}")]
        for r in page:
            html.append(_card(r, geo, key))
        html.append("</div></body>")
        (outdir / f"holdout-page-{pi:02d}.html").write_text(
            "\n".join(html), encoding="utf-8")

    (outdir / "answer-key.json").write_text(json.dumps({
        "seed": SEED, "snapshot": SNAP, "zoom": ZOOM,
        "independenceM": INDEPENDENCE_M,
        "atGradeDrawn": len(at_grade), "decoysDrawn": len(decoys),
        "pages": len(pages),
        "cells": {k: v[1] for k, v in
                  {**AT_GRADE_CELLS, **DECOY_CELLS}.items()},
        "cards": [{"code": r["code"], "linkA": r["linkA"], "linkB": r["linkB"],
                   "cell": r["cell"], "disposition": r["disposition"],
                   "reason": r["reason"], "angleDeg": r["angleDeg"],
                   "nameA": r["nameA"], "nameB": r["nameB"],
                   "x": r["x"], "y": r["y"],
                   "structDistM": r.get("structDistM"),
                   "urA": r.get("urA"), "urB": r.get("urB"),
                   "rcaA": r.get("rcaA"), "rcaB": r.get("rcaB"),
                   "rcaNameA": r.get("rcaNameA"), "rcaNameB": r.get("rcaNameB"),
                   "surfA": r.get("surfA"), "surfB": r.get("surfB"),
                   "imageryYear": r.get("imageryYear"),
                   "duplicateCorridor": r.get("duplicateCorridor")}
                  for r in sorted(sample, key=lambda r: r["code"])],
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {len(pages)} blinded pages and answer-key.json to {outdir}")
    _report_spread(sample)
    return 0


def _report_spread(sample: list[dict]) -> None:
    ys = sorted(r["y"] for r in sample)
    print(f"  northing spread: {ys[0]:.0f} .. {ys[-1]:.0f} "
          f"({(ys[-1]-ys[0])/1000:.0f} km of New Zealand)")
    print(f"  occupied 200 km northing bands: "
          f"{len({int(r['y'] // 200000) for r in sample})}")
    rca = collections.Counter(str(r.get("rcaNameA") or "unknown")
                              for r in sample)
    print(f"  distinct road controlling authorities: {len(rca)}")
    for name, n in rca.most_common(8):
        print(f"    {n:>3}  {name}")
    yrs = collections.Counter(r.get("imageryYear") for r in sample)
    print("  imagery year: " + ", ".join(
        f"{k}:{v}" for k, v in sorted(yrs.items(),
                                      key=lambda kv: (kv[0] is None, kv[0]))))


def _card(r: dict, geo: dict, key: str) -> str:
    from pyproj import Transformer
    t = Transformer.from_crs(2193, 4326, always_xy=True)
    lon, lat = t.transform(r["x"], r["y"])
    cx, cy = lonlat_to_px(lon, lat, ZOOM)
    left, top = cx - CARD / 2, cy - CARD / 2

    tiles = []
    tx0, ty0 = int(left // TILE), int(top // TILE)
    tx1, ty1 = int((left + CARD) // TILE), int((top + CARD) // TILE)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tiles.append(
                f'<img class="t" src="https://basemaps.linz.govt.nz/v1/tiles/'
                f'aerial/WebMercatorQuad/{ZOOM}/{tx}/{ty}.webp?api={key}" '
                f'style="left:{tx*TILE-left:.1f}px;top:{ty*TILE-top:.1f}px">')

    def path(link_id: int) -> str:
        g = geo.get(link_id)
        if not g:
            return ""
        return " ".join(
            f"{px-left:.1f},{py-top:.1f}"
            for px, py in (lonlat_to_px(lo, la, ZOOM)
                           for lo, la in g["coordinates"]))

    m = CARD / 2
    svg = (f'<svg class="ov" width="{CARD}" height="{CARD}">'
           f'<polyline points="{path(r["linkA"])}" class="ra"/>'
           f'<polyline points="{path(r["linkB"])}" class="rb"/>'
           f'<circle cx="{m}" cy="{m}" r="11" class="mk"/>'
           f'<circle cx="{m}" cy="{m}" r="3" class="mk2"/></svg>')

    # NOTHING about the classifier's answer appears here.
    return (f'<figure class="c">'
            f'<div class="v" style="width:{CARD}px;height:{CARD}px">'
            f'{"".join(tiles)}{svg}</div>'
            f'<figcaption><b>{r["code"]}</b> '
            f'<code>{r["linkA"]}&times;{r["linkB"]}</code><br>'
            f'<span class="n">{r["nameA"] or "(unnamed)"} &times; '
            f'{r["nameB"] or "(unnamed)"}</span></figcaption></figure>')


_HEAD = """<meta charset="utf-8"><title>holdout crossing review {PAGE}</title>
<style>
 body{background:#12151a;color:#e8ecf1;font:13px/1.45 system-ui,sans-serif;margin:14px}
 h1{font-size:14px;margin:0 0 10px;font-weight:600;color:#9fb2c8}
 .g{display:flex;flex-wrap:wrap;gap:14px}
 .c{margin:0}
 .v{position:relative;overflow:hidden;border-radius:6px;background:#000}
 .t{position:absolute;width:256px;height:256px}
 .ov{position:absolute;left:0;top:0;pointer-events:none}
 .ra{fill:none;stroke:#ff3b6b;stroke-width:2.5;opacity:.9}
 .rb{fill:none;stroke:#22d3ee;stroke-width:2.5;opacity:.9}
 .mk{fill:none;stroke:#ffe14d;stroke-width:2.5}
 .mk2{fill:#ffe14d;stroke:none}
 figcaption{padding:6px 2px 0;max-width:400px}
 code{color:#9fb2c8}
 .n{color:#8ea3ba}
</style>
<body><h1>HOLDOUT &mdash; BLINDED &mdash; page {PAGE} &mdash;
 <span style="color:#ff3b6b">link A</span> /
 <span style="color:#22d3ee">link B</span>, crossing in yellow.
 Verdict: at grade / grade separated / not a junction / unclear</h1>
<div class="g">
"""


# --------------------------------------------------------------------------
#: What a reviewer verdict is allowed to be, per disposition.
#:
#: AT_GRADE is the only disposition that creates a node, so only "at grade"
#: confirms it. GRADE_SEPARATED and UNRESOLVED both leave the crossing
#: severed, so anything except "this is a plain at-grade junction" is
#: consistent with them - which is exactly why UNRESOLVED cannot be
#: contradicted and its precision is not a meaningful number.
ACCEPTS = {
    "AT_GRADE": {"at_grade"},
    "GRADE_SEPARATED": {"grade_separated", "not_a_junction"},
    "UNRESOLVED": {"at_grade", "grade_separated", "not_a_junction"},
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def _load_verdicts(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    v = doc["verdicts"]
    if isinstance(v, str):
        # compact "H001 a H002 g ..." form
        legend = doc.get("legend", {"a": "at_grade", "g": "grade_separated",
                                    "n": "not_a_junction", "u": "unclear"})
        toks = v.split()
        out = []
        for i in range(0, len(toks) - 1, 2):
            out.append({"code": toks[i],
                        "verdict": legend.get(toks[i + 1], toks[i + 1])})
        notes = doc.get("notes", {})
        for r in out:
            r["note"] = notes.get(r["code"], "")
        return out
    return v


def score(outdir: Path, verdicts_path: Path) -> int:
    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in key["cards"]}
    verdicts = _load_verdicts(verdicts_path)

    rows = []
    seen = set()
    for v in verdicts:
        c = by_code.get(v["code"])
        if c is None:
            print(f"  unknown code {v['code']}")
            continue
        if v["code"] in seen:
            print(f"  duplicate verdict for {v['code']}")
            continue
        seen.add(v["code"])
        rows.append({**c, "verdict": v["verdict"], "note": v.get("note", "")})

    missing = sorted(set(by_code) - seen)
    print(f"scored {len(rows)} of {len(by_code)} cards")
    if missing:
        print(f"  NOT SCORED ({len(missing)}): {', '.join(missing)}")
    print()

    def block(title: str, subset: list[dict]) -> None:
        if not subset:
            return
        conf = sum(1 for r in subset
                   if r["verdict"] in ACCEPTS[r["disposition"]])
        contra = sum(1 for r in subset
                     if r["verdict"] not in ACCEPTS[r["disposition"]]
                     and r["verdict"] != "unclear")
        unrev = sum(1 for r in subset if r["verdict"] == "unclear")
        n = len(subset)
        lo, hi = wilson(conf, n)
        lo2, hi2 = wilson(conf, n - unrev) if n - unrev else (0.0, 1.0)
        print(title)
        print(f"    n={n}  confirmed={conf}  contradicted={contra}  "
              f"unreviewable={unrev}")
        print(f"    counting unreviewable as failures: "
              f"{100.0*conf/n:.1f}%  lower 95% bound {100*lo:.1f}%")
        if n - unrev:
            print(f"    excluding unreviewable:            "
                  f"{100.0*conf/(n-unrev):.1f}%  lower 95% bound {100*lo2:.1f}%")
        print()

    for disp in ("AT_GRADE", "GRADE_SEPARATED", "UNRESOLVED"):
        block(f"=== {disp} ===", [r for r in rows if r["disposition"] == disp])
    print("  (UNRESOLVED accepts every verdict but 'unclear' by construction: "
          "it makes no claim about the ground. Its 'precision' is not a\n"
          "   meaningful number and is printed only for completeness.)\n")

    print("=== AT_GRADE by deciding rule ===")
    for reason in sorted({r["reason"] for r in rows
                          if r["disposition"] == "AT_GRADE"}):
        block(f"  {reason}", [r for r in rows if r["disposition"] == "AT_GRADE"
                              and r["reason"] == reason])

    print("=== AT_GRADE by stratum cell ===")
    for cell in sorted({r["cell"] for r in rows
                        if r["disposition"] == "AT_GRADE"}):
        sub = [r for r in rows if r["cell"] == cell]
        conf = sum(1 for r in sub if r["verdict"] in ACCEPTS[r["disposition"]])
        contra = sum(1 for r in sub if r["verdict"] not in
                     ACCEPTS[r["disposition"]] and r["verdict"] != "unclear")
        unrev = sum(1 for r in sub if r["verdict"] == "unclear")
        print(f"  {cell:<20} n={len(sub):>3}  confirmed={conf:>3}  "
              f"contradicted={contra:>2}  unreviewable={unrev:>2}")

    print("\n=== AT_GRADE by imagery year ===")
    ag = [r for r in rows if r["disposition"] == "AT_GRADE"]
    for yr in sorted({r.get("imageryYear") for r in ag},
                     key=lambda y: (y is None, y)):
        sub = [r for r in ag if r.get("imageryYear") == yr]
        conf = sum(1 for r in sub if r["verdict"] in ACCEPTS["AT_GRADE"])
        unrev = sum(1 for r in sub if r["verdict"] == "unclear")
        print(f"  {str(yr):<8} n={len(sub):>3}  confirmed={conf:>3}  "
              f"unreviewable={unrev:>2}")

    print("\n=== PROMOTION GATE for AT_GRADE ===")
    fp = [r for r in ag if r["verdict"] == "grade_separated"]
    conf = sum(1 for r in ag if r["verdict"] in ACCEPTS["AT_GRADE"])
    unrev = sum(1 for r in ag if r["verdict"] == "unclear")
    lo, _ = wilson(conf, len(ag))
    lo2, _ = wilson(conf, len(ag) - unrev) if len(ag) - unrev else (0.0, 1.0)
    print(f"  confirmed grade-separated false positives: {len(fp)}   "
          f"(gate: must be 0)")
    for r in fp:
        print(f"    {r['code']} {r['linkA']}x{r['linkB']} {r['reason']}: "
              f"{r['note']}")
    print(f"  confirmed / contradicted / unreviewable:   "
          f"{conf} / {len(ag)-conf-unrev} / {unrev}")
    print(f"  lower 95% bound, unreviewable as failures: {100*lo:.1f}%")
    print(f"  lower 95% bound, unreviewable excluded:    {100*lo2:.1f}%")

    print("\n=== every contradiction ===")
    for r in rows:
        if r["verdict"] not in ACCEPTS[r["disposition"]] \
                and r["verdict"] != "unclear":
            print(f"  {r['code']} {r['disposition']}/{r['reason']} "
                  f"{r['linkA']}x{r['linkB']} ({r['nameA']} x {r['nameB']}) "
                  f"-> {r['verdict']}")
            print(f"      {r['note']}")

    (outdir / "scored.json").write_text(json.dumps(rows, indent=2),
                                        encoding="utf-8")
    return 0


# --------------------------------------------------------------------------
def recode(outdir: Path, n: int, per_page: int = 9) -> int:
    """Second pack for double-coding: same cases, reshuffled, ids removed.

    Drawn from THIS pack, not from the development pack, because the number
    that matters is how reliable the verdicts gating promotion are.

    Everything identifying is stripped: fresh codes from a different seed, no
    link ids, no names. The mapping goes to recode-key.json and is joined only
    when agreement is computed.
    """
    import random
    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    cards = key["cards"]
    rng = random.Random(SEED + "-recode")
    chosen = rng.sample(cards, min(n, len(cards)))
    rng.shuffle(chosen)
    for i, c in enumerate(chosen, 1):
        c["recode"] = f"R{i:02d}"

    ids = sorted({c["linkA"] for c in chosen} | {c["linkB"] for c in chosen})
    geo = {g["link_id"]: json.loads(g["gj"]) for g in db.query(
        "SELECT link_id, ST_AsGeoJSON(geom_4326, 7) AS gj FROM links "
        " WHERE snapshot_id=%s AND link_id = ANY(%s)", (SNAP, ids))}

    linz = linz_key()
    pages = [chosen[i:i + per_page] for i in range(0, len(chosen), per_page)]
    for pi, page in enumerate(pages, 1):
        html = [_HEAD.replace("{PAGE}", f"R{pi} of {len(pages)}")]
        for c in page:
            html.append(_card_anon(c, geo, linz))
        html.append("</div></body>")
        (outdir / f"recode-page-{pi:02d}.html").write_text(
            "\n".join(html), encoding="utf-8")

    (outdir / "recode-key.json").write_text(json.dumps(
        [{"recode": c["recode"], "code": c["code"]} for c in
         sorted(chosen, key=lambda c: c["recode"])], indent=2),
        encoding="utf-8")
    print(f"wrote {len(pages)} recode pages ({len(chosen)} cases) to {outdir}")
    return 0


def _card_anon(real: dict, geo: dict, key: str) -> str:
    from pyproj import Transformer
    t = Transformer.from_crs(2193, 4326, always_xy=True)
    lon, lat = t.transform(real["x"], real["y"])
    cx, cy = lonlat_to_px(lon, lat, ZOOM)
    left, top = cx - CARD / 2, cy - CARD / 2
    tiles = []
    tx0, ty0 = int(left // TILE), int(top // TILE)
    tx1, ty1 = int((left + CARD) // TILE), int((top + CARD) // TILE)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tiles.append(
                f'<img class="t" src="https://basemaps.linz.govt.nz/v1/tiles/'
                f'aerial/WebMercatorQuad/{ZOOM}/{tx}/{ty}.webp?api={key}" '
                f'style="left:{tx*TILE-left:.1f}px;top:{ty*TILE-top:.1f}px">')

    def path(link_id: int) -> str:
        g = geo.get(link_id)
        if not g:
            return ""
        return " ".join(f"{px-left:.1f},{py-top:.1f}"
                        for px, py in (lonlat_to_px(lo, la, ZOOM)
                                       for lo, la in g["coordinates"]))

    m = CARD / 2
    svg = (f'<svg class="ov" width="{CARD}" height="{CARD}">'
           f'<polyline points="{path(real["linkA"])}" class="ra"/>'
           f'<polyline points="{path(real["linkB"])}" class="rb"/>'
           f'<circle cx="{m}" cy="{m}" r="11" class="mk"/>'
           f'<circle cx="{m}" cy="{m}" r="3" class="mk2"/></svg>')
    return (f'<figure class="c"><div class="v" '
            f'style="width:{CARD}px;height:{CARD}px">{"".join(tiles)}{svg}</div>'
            f'<figcaption><b>{real["recode"]}</b></figcaption></figure>')


def agree(outdir: Path, pass1: Path, pass2: Path) -> int:
    """Inter-rater agreement over the double-coded subset, with Cohen's kappa.

    Raw agreement alone flatters any task where one answer dominates: if 90%
    of cards are at-grade, two coders who always said "at grade" would agree
    90% of the time while demonstrating nothing. Kappa discounts the agreement
    chance alone would produce.
    """
    mapping = {r["recode"]: r["code"] for r in
               json.loads((outdir / "recode-key.json").read_text(encoding="utf-8"))}
    v1 = {v["code"]: v["verdict"] for v in _load_verdicts(pass1)}
    v2raw = _load_verdicts(pass2)
    v2 = {mapping[v["code"]]: v["verdict"] for v in v2raw
          if v["code"] in mapping}
    notes2 = {mapping[v["code"]]: v.get("note", "") for v in v2raw
              if v["code"] in mapping}

    both = sorted(set(v1) & set(v2))
    print(f"double-coded cases with a verdict in both passes: {len(both)}")
    if not both:
        return 1

    same = [c for c in both if v1[c] == v2[c]]
    labels = sorted({v1[c] for c in both} | {v2[c] for c in both})
    po = len(same) / len(both)
    pe = sum((sum(1 for c in both if v1[c] == L) / len(both))
             * (sum(1 for c in both if v2[c] == L) / len(both))
             for L in labels)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    lo, hi = wilson(len(same), len(both))
    print(f"  raw agreement: {len(same)}/{len(both)} = {100*po:.1f}% "
          f"[{100*lo:.1f}-{100*hi:.1f}]")
    print(f"  expected by chance: {100*pe:.1f}%")
    print(f"  Cohen's kappa: {kappa:.3f}")

    print("\n  confusion (pass 1 down, pass 2 across):")
    print("    " + "".join(f"{L[:9]:>11}" for L in labels))
    for a in labels:
        cells = "".join(
            f"{sum(1 for c in both if v1[c] == a and v2[c] == b):>11}"
            for b in labels)
        print(f"    {a[:14]:<14}{cells}")

    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in key["cards"]}
    dis = [c for c in both if v1[c] != v2[c]]
    print(f"\n  disagreements: {len(dis)} - every one is adjudicated below")
    out = []
    for c in dis:
        k = by_code[c]
        print(f"    {c} {k['disposition']}/{k['reason']} "
              f"{k['linkA']}x{k['linkB']} ({k['nameA']} x {k['nameB']})")
        print(f"      pass1={v1[c]}  pass2={v2[c]}  {notes2.get(c, '')}")
        out.append({"code": c, "pass1": v1[c], "pass2": v2[c],
                    "linkA": k["linkA"], "linkB": k["linkB"],
                    "nameA": k["nameA"], "nameB": k["nameB"],
                    "disposition": k["disposition"], "reason": k["reason"],
                    "cell": k["cell"], "note2": notes2.get(c, "")})

    (outdir / "agreement.json").write_text(json.dumps({
        "n": len(both), "agreed": len(same), "rawAgreement": po,
        "expectedByChance": pe, "cohensKappa": kappa,
        "agreementLower95": lo, "agreementUpper95": hi,
        "labels": labels, "disagreements": out,
    }, indent=2), encoding="utf-8")
    return 0


def zoom(outdir: Path, codes: list[str], z: int = 20, size: int = 720) -> int:
    """Re-render named cards larger and closer, still blinded.

    A 400 px card at zoom 18 is enough for a plain rural crossroads and not
    enough for a motorway, a canopy-covered urban street or a case where the
    centreline looks displaced from the carriageway. Guessing those would put
    noise into the very cells the pack was drawn to test, so they get looked
    at properly instead. Nothing about the classifier's answer is shown here
    either - this is the same card, magnified.
    """
    global ZOOM, CARD
    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in key["cards"]}
    want = [by_code[c] for c in codes if c in by_code]
    for c in codes:
        if c not in by_code:
            print(f"  unknown code {c}")
    if not want:
        return 1

    ids = sorted({c["linkA"] for c in want} | {c["linkB"] for c in want})
    geo = {g["link_id"]: json.loads(g["gj"]) for g in db.query(
        "SELECT link_id, ST_AsGeoJSON(geom_4326, 7) AS gj FROM links "
        " WHERE snapshot_id=%s AND link_id = ANY(%s)", (SNAP, ids))}

    ZOOM, CARD = z, size
    linz = linz_key()
    html = [_HEAD.replace("{PAGE}", f"zoom z{z}")]
    for c in want:
        html.append(_card(c, geo, linz))
    html.append("</div></body>")
    name = f"zoom-{'-'.join(codes)[:60]}.html"
    (outdir / name).write_text("\n".join(html), encoding="utf-8")
    print(f"wrote {outdir / name} ({len(want)} cards at z{z}, {size}px)")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        raise SystemExit(build(Path(sys.argv[2])))
    if cmd == "zoom":
        raise SystemExit(zoom(Path(sys.argv[2]), sys.argv[3].split(","),
                              int(sys.argv[4]) if len(sys.argv) > 4 else 20,
                              int(sys.argv[5]) if len(sys.argv) > 5 else 720))
    if cmd == "score":
        raise SystemExit(score(Path(sys.argv[2]), Path(sys.argv[3])))
    if cmd == "recode":
        raise SystemExit(recode(Path(sys.argv[2]), int(sys.argv[3])))
    if cmd == "agree":
        raise SystemExit(agree(Path(sys.argv[2]), Path(sys.argv[3]),
                               Path(sys.argv[4])))
    raise SystemExit("build | score | recode | agree")
