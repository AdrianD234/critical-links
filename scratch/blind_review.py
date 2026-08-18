"""A BLINDED review pack, and the scorer that unblinds it afterwards.

Why the first pack was not good enough
--------------------------------------
It printed the classifier's answer on every card - "AT_GRADE ·
ORDINARY_CROSSROADS" - before the reviewer looked at the imagery. Whatever it
measured, it was not independent ground truth, and it must not be described as
such. This pack shows imagery, the two centrelines, the crossing point, ids and
names, and nothing else. The answer key is written to a separate file and only
joined back after the verdicts are recorded.

Order is randomised across strata, so a run of similar-looking cards does not
tell the reviewer what stratum it is in.

    python ../scratch/blind_review.py build  <outdir>
    python ../scratch/blind_review.py score  <outdir> <verdicts.json>
    python ../scratch/blind_review.py recode <outdir> <n>   # double-coding pack
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import random
import sys
from pathlib import Path

from nzcl import db
from nzcl.config import REPO_ROOT

SNAP = "amds-national-2026-07-28-5b359d84"
SEED = "at-grade-blinded-review-1"
ZOOM = 18
TILE = 256
CARD = 400

#: AT_GRADE is the only disposition that creates a node, so it carries the
#: whole graph decision and gets the sample. The cells below are the places a
#: rule like ORDINARY_CROSSROADS is most likely to be wrong, drawn deliberately
#: rather than left to a random draw that would miss them.
AT_GRADE_CELLS: dict[str, tuple[str, int]] = {
    # near the tangential veto, where a graze is easiest to mistake for a
    # junction
    "angle_20_30":     ("angleDeg >= 20 and angleDeg < 30", 18),
    "angle_30_60":     ("angleDeg >= 30 and angleDeg < 60", 18),
    "angle_60_80":     ("angleDeg >= 60 and angleDeg < 80", 18),
    "angle_80_90":     ("angleDeg >= 80", 28),
    # a mapped structure JUST outside the 15 m match radius: if the threshold
    # is too tight, this is where the misses are
    "structure_near":  ("structDistM is not None and 15 < structDistM <= 60", 15),
    # no name on either side - often forestry, industrial or private access
    "unnamed_both":    ("not nameA and not nameB", 12),
    "state_highway":   ("rcaA == 1 or rcaB == 1", 12),
    "urban":           ("urA == 'urban' and urB == 'urban'", 12),
    "rural":           ("urA == 'rural' and urB == 'rural'", 15),
    "junction_witness": ("reason == 'JUNCTION_WITNESS'", 12),
}

#: Decoys. Without them the pack is "here are 150 crossings, all AT_GRADE",
#: which is not blinded at all.
DECOY_CELLS: dict[str, tuple[str, int]] = {
    "gs_structure":   ("disposition == 'GRADE_SEPARATED' and reason == 'STRUCTURE_MAPPED'", 10),
    "gs_motorway":    ("disposition == 'GRADE_SEPARATED' and reason == 'MOTORWAY_CARRIAGEWAY'", 10),
    "gs_other":       ("disposition == 'GRADE_SEPARATED' and reason not in "
                       "('STRUCTURE_MAPPED','MOTORWAY_CARRIAGEWAY')", 8),
    "un_no_evidence": ("disposition == 'UNRESOLVED' and reason == 'NO_EVIDENCE_EITHER_WAY'", 10),
    "un_other":       ("disposition == 'UNRESOLVED' and reason != 'NO_EVIDENCE_EITHER_WAY'", 10),
}


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
    src = REPO_ROOT / "docs/audits/at-grade-crossings/classified.jsonl"
    if not src.exists():
        raise SystemExit(
            f"{src} is not present. It is derived and gitignored; regenerate "
            f"it with the steps in scratch/README.md.")
    return [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def draw(rows: list[dict], cells: dict, disposition: str | None) -> list[dict]:
    pool = [r for r in rows
            if disposition is None or r["disposition"] == disposition]
    picked: dict[tuple[int, int], dict] = {}
    for cell, (expr, n) in cells.items():
        cand = []
        for r in pool:
            try:
                if eval(expr, {"__builtins__": {}}, dict(r)):  # noqa: S307
                    cand.append(r)
            except Exception:  # noqa: BLE001
                continue
        cand.sort(key=lambda r: rank(r, cell))
        taken = 0
        for r in cand:
            key = (r["linkA"], r["linkB"])
            if key in picked:
                continue
            r = dict(r, cell=cell)
            picked[key] = r
            taken += 1
            if taken >= n:
                break
        if taken < n:
            print(f"  cell {cell}: only {taken} available, wanted {n}")
    return list(picked.values())


def build(outdir: Path, per_page: int = 9) -> int:
    rows = load_rows()
    at_grade = draw(rows, AT_GRADE_CELLS, "AT_GRADE")
    decoys = draw(rows, DECOY_CELLS, None)
    sample = at_grade + decoys
    print(f"drew {len(at_grade)} AT_GRADE + {len(decoys)} decoys "
          f"= {len(sample)} cards")

    rng = random.Random(SEED)
    rng.shuffle(sample)
    for i, r in enumerate(sample, 1):
        r["code"] = f"C{i:03d}"

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
        (outdir / f"blind-page-{pi:02d}.html").write_text("\n".join(html),
                                                          encoding="utf-8")

    # The answer key. Written separately, joined only after verdicts exist.
    (outdir / "answer-key.json").write_text(json.dumps({
        "seed": SEED, "snapshot": SNAP, "zoom": ZOOM,
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
                   "rcaA": r.get("rcaA"), "rcaB": r.get("rcaB")}
                  for r in sorted(sample, key=lambda r: r["code"])],
    }, indent=2), encoding="utf-8")
    print(f"wrote {len(pages)} blinded pages and answer-key.json to {outdir}")
    _report_spread(sample)
    return 0


def _report_spread(sample: list[dict]) -> None:
    ys = sorted(r["y"] for r in sample)
    print(f"  northing spread: {ys[0]:.0f} .. {ys[-1]:.0f} "
          f"({(ys[-1]-ys[0])/1000:.0f} km of New Zealand)")
    bands = collections.Counter(int(r["y"] // 200000) for r in sample)
    print(f"  occupied 200 km northing bands: {len(bands)}")


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
            url = (f"https://basemaps.linz.govt.nz/v1/tiles/aerial/"
                   f"WebMercatorQuad/{ZOOM}/{tx}/{ty}.webp?api={key}")
            tiles.append(f'<img class="t" src="{url}" '
                         f'style="left:{tx*TILE-left:.1f}px;'
                         f'top:{ty*TILE-top:.1f}px">')

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


_HEAD = """<meta charset="utf-8"><title>blinded crossing review {PAGE}</title>
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
<body><h1>BLINDED &mdash; page {PAGE} &mdash;
 <span style="color:#ff3b6b">link A</span> /
 <span style="color:#22d3ee">link B</span>, crossing in yellow.
 Verdict: at grade / grade separated / not a junction / unclear</h1>
<div class="g">
"""


# ---------------------------------------------------------------------------
def score(outdir: Path, verdicts_path: Path) -> int:
    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in key["cards"]}
    verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))["verdicts"]

    ACCEPTS = {
        "AT_GRADE": {"at_grade"},
        "GRADE_SEPARATED": {"grade_separated", "not_a_junction"},
        "UNRESOLVED": {"at_grade", "grade_separated", "not_a_junction"},
    }

    rows = []
    for v in verdicts:
        c = by_code.get(v["code"])
        if c is None:
            print(f"  unknown code {v['code']}")
            continue
        rows.append({**c, "verdict": v["verdict"], "note": v.get("note", "")})

    print(f"scored {len(rows)} of {len(by_code)} cards\n")

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
        print(f"{title}")
        print(f"    n={n}  confirmed={conf}  contradicted={contra}  "
              f"unreviewable={unrev}")
        print(f"    including unreviewable as failures: "
              f"{100.0*conf/n:.1f}% [{100*lo:.1f}-{100*hi:.1f}]")
        if n - unrev:
            print(f"    excluding unreviewable:            "
                  f"{100.0*conf/(n-unrev):.1f}% [{100*lo2:.1f}-{100*hi2:.1f}]")
        print()

    for disp in ("AT_GRADE", "GRADE_SEPARATED", "UNRESOLVED"):
        block(f"=== {disp} ===",
              [r for r in rows if r["disposition"] == disp])

    print("=== AT_GRADE by deciding rule ===")
    for reason in sorted({r["reason"] for r in rows
                          if r["disposition"] == "AT_GRADE"}):
        block(f"  {reason}", [r for r in rows
                              if r["disposition"] == "AT_GRADE"
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

    print("\n=== PROMOTION GATE for AT_GRADE ===")
    ag = [r for r in rows if r["disposition"] == "AT_GRADE"]
    fp = [r for r in ag if r["verdict"] == "grade_separated"]
    conf = sum(1 for r in ag if r["verdict"] in ACCEPTS["AT_GRADE"])
    lo, _ = wilson(conf, len(ag))
    print(f"  confirmed grade-separated false positives: {len(fp)}  "
          f"(gate: must be 0)")
    for r in fp:
        print(f"    {r['code']} {r['linkA']}x{r['linkB']} {r['reason']}: "
              f"{r['note']}")
    print(f"  lower 95% confidence bound on precision:   {100*lo:.1f}%  "
          f"(from {conf}/{len(ag)})")

    print("\n=== every contradiction ===")
    for r in rows:
        if r["verdict"] not in ACCEPTS[r["disposition"]] \
                and r["verdict"] != "unclear":
            print(f"  {r['code']} {r['disposition']}/{r['reason']} "
                  f"{r['linkA']}x{r['linkB']} -> {r['verdict']}")
            print(f"      {r['note']}")

    (outdir / "scored.json").write_text(json.dumps(rows, indent=2),
                                        encoding="utf-8")
    return 0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


# ---------------------------------------------------------------------------
def recode(outdir: Path, n: int, per_page: int = 9) -> int:
    """Second pack for double-coding: same cases, reshuffled, ids obscured.

    Identifiers are replaced with fresh codes drawn from a different seed, so
    the second pass cannot be matched to the first by eye. The mapping is
    written to recode-key.json and joined only when agreement is computed.
    """
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
            r = dict(c, code=c["recode"], nameA=None, nameB=None,
                     linkA=0, linkB=0)
            html.append(_card_anon(r, c, geo, linz))
        html.append("</div></body>")
        (outdir / f"recode-page-{pi:02d}.html").write_text(
            "\n".join(html), encoding="utf-8")

    (outdir / "recode-key.json").write_text(json.dumps(
        [{"recode": c["recode"], "code": c["code"]} for c in
         sorted(chosen, key=lambda c: c["recode"])], indent=2),
        encoding="utf-8")
    print(f"wrote {len(pages)} recode pages ({len(chosen)} cases) to {outdir}")
    return 0


def _card_anon(r: dict, real: dict, geo: dict, key: str) -> str:
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
            f'<figcaption><b>{r["code"]}</b></figcaption></figure>')


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build":
        raise SystemExit(build(Path(sys.argv[2])))
    if cmd == "score":
        raise SystemExit(score(Path(sys.argv[2]), Path(sys.argv[3])))
    if cmd == "recode":
        raise SystemExit(recode(Path(sys.argv[2]), int(sys.argv[3])))
    raise SystemExit("build | score | recode")
