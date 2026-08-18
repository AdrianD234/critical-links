"""Draw a stratified sample of classified crossings over LINZ aerial imagery,
so the classifier's precision can be MEASURED rather than asserted.

Each card is one crossing: aerial photography at the crossing point, with the
two AMDS centrelines drawn on top and the crossing marked. That is enough to
say, by eye, whether the roads meet or one passes over the other.

    python ../scratch/review_sheets.py <outdir> [cards_per_page]

Writes review-page-NN.html and sample.json. Nothing is classified here; the
verdicts are recorded by hand afterwards, in review-verdicts.json.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

from nzcl import db
from nzcl.config import REPO_ROOT

SNAP = "amds-national-2026-07-28-5b359d84"
SEED = "at-grade-crossing-precision-sample-1"
ZOOM = 18
TILE = 256
CARD = 400           # rendered card size in CSS pixels

#: How many to draw from each stratum, and why these numbers.
#:
#: This is a DISPROPORTIONATE stratified sample: every deciding rule is drawn
#: from, including the rare ones, so each rule gets its own verdict rather than
#: disappearing into a national average. It is therefore NOT a national
#: estimate of anything - a per-rule precision is a statement about that rule,
#: and the pooled figures are reweighted to the population before they are
#: quoted.
#:
#: The two classes that change the graph are drawn most heavily: AT_GRADE
#: becomes a node, GRADE_SEPARATED stays severed. UNRESOLVED changes nothing on
#: its own, so it is sampled to characterise what is being given up, not to
#: certify a decision.
STRATA = {
    ("AT_GRADE", "ORDINARY_CROSSROADS"): 30,
    ("AT_GRADE", "JUNCTION_WITNESS"): 6,
    ("GRADE_SEPARATED", "STRUCTURE_MAPPED"): 6,
    ("GRADE_SEPARATED", "MOTORWAY_CARRIAGEWAY"): 6,
    ("GRADE_SEPARATED", "RAMP"): 3,
    ("GRADE_SEPARATED", "RAMP_CONTEXT"): 3,
    ("GRADE_SEPARATED", "HEIGHT_LIMIT"): 3,
    ("GRADE_SEPARATED", "CONNECTOR"): 2,
    ("GRADE_SEPARATED", "NAMED_STRUCTURE"): 2,
    ("UNRESOLVED", "NO_EVIDENCE_EITHER_WAY"): 12,
    ("UNRESOLVED", "TANGENTIAL"): 4,
    ("UNRESOLVED", "MOTORWAY_CONTEXT"): 4,
}


def rank(row: dict) -> str:
    """Deterministic order. Same seed, same sample, on any machine."""
    return hashlib.md5(
        f"{SEED}|{SNAP}|{row['linkA']}|{row['linkB']}".encode()).hexdigest()


# --- web mercator ----------------------------------------------------------
def lonlat_to_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def main() -> int:
    outdir = Path(sys.argv[1])
    per_page = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    outdir.mkdir(parents=True, exist_ok=True)

    key = ""
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("VITE_LINZ_API_KEY="):
                key = line.split("=", 1)[1].strip()
    key = os.environ.get("LINZ_BASEMAPS_KEY", key)
    if not key:
        raise SystemExit("no LINZ Basemaps key found in .env")

    src = Path(sys.argv[3]) if len(sys.argv) > 3 else \
        REPO_ROOT / "docs/audits/at-grade-crossings/classified.jsonl"
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l]

    # --- stratified draw ---------------------------------------------------
    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        buckets.setdefault((r["disposition"], r["reason"]), []).append(r)
    sample: list[dict] = []
    for stratum, n in STRATA.items():
        pool = sorted(buckets.get(stratum, []), key=rank)
        if len(pool) < n:
            print(f"  stratum {stratum}: only {len(pool)} available, wanted {n}")
        sample.extend(pool[:n])
    sample.sort(key=rank)
    print(f"sampled {len(sample)} crossings from {len(rows)}")

    # --- geometry for the overlay -----------------------------------------
    ids = sorted({r["linkA"] for r in sample} | {r["linkB"] for r in sample})
    geo = {
        g["link_id"]: json.loads(g["gj"])
        for g in db.query(
            "SELECT link_id, ST_AsGeoJSON(geom_4326, 7) AS gj "
            "  FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)",
            (SNAP, ids))
    }

    pages = [sample[i:i + per_page] for i in range(0, len(sample), per_page)]
    manifest = []
    for pi, page in enumerate(pages, 1):
        html = [_HEAD.replace("{PAGE}", f"{pi} of {len(pages)}")]
        for r in page:
            html.append(_card(r, geo, key))
            manifest.append({
                "page": pi, "linkA": r["linkA"], "linkB": r["linkB"],
                "disposition": r["disposition"], "reason": r["reason"],
                "nameA": r["nameA"], "nameB": r["nameB"],
                "x": r["x"], "y": r["y"], "angleDeg": r["angleDeg"],
            })
        html.append("</div></body>")
        (outdir / f"review-page-{pi:02d}.html").write_text(
            "\n".join(html), encoding="utf-8")

    (outdir / "sample.json").write_text(json.dumps({
        "seed": SEED, "snapshot": SNAP, "zoom": ZOOM,
        "strata": {f"{a}/{b}": n for (a, b), n in STRATA.items()},
        "drawn": len(sample), "pages": len(pages),
        "items": manifest,
    }, indent=2), encoding="utf-8")
    print(f"wrote {len(pages)} pages to {outdir}")
    return 0


def _card(r: dict, geo: dict, key: str) -> str:
    # Centre on the crossing.
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
            tiles.append(
                f'<img class="t" src="{url}" '
                f'style="left:{tx*TILE-left:.1f}px;top:{ty*TILE-top:.1f}px">')

    def path(link_id: int) -> str:
        g = geo.get(link_id)
        if not g:
            return ""
        pts = []
        for lo, la in g["coordinates"]:
            px, py = lonlat_to_px(lo, la, ZOOM)
            pts.append(f"{px-left:.1f},{py-top:.1f}")
        return " ".join(pts)

    pa, pb = path(r["linkA"]), path(r["linkB"])
    mx, my = CARD / 2, CARD / 2
    svg = (
        f'<svg class="ov" width="{CARD}" height="{CARD}">'
        f'<polyline points="{pa}" class="ra"/>'
        f'<polyline points="{pb}" class="rb"/>'
        f'<circle cx="{mx}" cy="{my}" r="11" class="mk"/>'
        f'<circle cx="{mx}" cy="{my}" r="3" class="mk2"/>'
        f'</svg>')

    cls = {"AT_GRADE": "ag", "GRADE_SEPARATED": "gs",
           "UNRESOLVED": "un"}[r["disposition"]]
    return (
        f'<figure class="c">'
        f'<div class="v" style="width:{CARD}px;height:{CARD}px">{"".join(tiles)}{svg}</div>'
        f'<figcaption>'
        f'<b class="{cls}">{r["disposition"]}</b> &middot; {r["reason"]}<br>'
        f'<code>{r["linkA"]} &times; {r["linkB"]}</code> &middot; '
        f'{r["angleDeg"]:.0f}&deg;<br>'
        f'<span class="n">{r["nameA"] or "(unnamed)"} &times; '
        f'{r["nameB"] or "(unnamed)"}</span>'
        f'</figcaption></figure>')


_HEAD = """<meta charset="utf-8"><title>crossing review {PAGE}</title>
<style>
 body{background:#12151a;color:#e8ecf1;font:13px/1.45 system-ui,sans-serif;margin:14px}
 h1{font-size:15px;margin:0 0 12px;font-weight:600;color:#9fb2c8}
 .g{display:flex;flex-wrap:wrap;gap:14px}
 .c{margin:0}
 .v{position:relative;overflow:hidden;border-radius:6px;background:#000}
 .t{position:absolute;width:256px;height:256px;image-rendering:auto}
 .ov{position:absolute;left:0;top:0;pointer-events:none}
 .ra{fill:none;stroke:#ff3b6b;stroke-width:2.5;opacity:.9}
 .rb{fill:none;stroke:#22d3ee;stroke-width:2.5;opacity:.9}
 .mk{fill:none;stroke:#ffe14d;stroke-width:2.5}
 .mk2{fill:#ffe14d;stroke:none}
 figcaption{padding:6px 2px 0;max-width:460px}
 code{color:#9fb2c8}
 .n{color:#8ea3ba}
 .ag{color:#4ade80}.gs{color:#fbbf24}.un{color:#a78bfa}
</style>
<body><h1>At-grade crossing review &mdash; page {PAGE} &mdash;
 <span class="ra" style="color:#ff3b6b">link A</span> /
 <span style="color:#22d3ee">link B</span>,
 crossing marked in yellow</h1><div class="g">
"""

if __name__ == "__main__":
    raise SystemExit(main())
