"""The third holdout: cards as IMAGE FILES, so the reviewer can be isolated.

Read `docs/audits/at-grade-crossings/third-holdout-predeclaration.md` first.
350 AT_GRADE cards, at most 4 failures, unreviewable counted among them. Those
numbers were committed before this file existed and nothing here may change
them.

WHY THIS GENERATOR EXISTS RATHER THAN A THIRD COPY OF THE HTML ONE
------------------------------------------------------------------
The two previous packs are browser-rendered HTML that pulls LINZ tiles live.
That was fine for a reviewer sitting at the machine that generated them. It
cannot meet the standard this holdout is required to meet:

    a FRESH ISOLATED reviewer, with no prior transcript, no access to the
    classifier source, no prior verdicts, no score summary, on anonymous
    randomised cards.

A reviewer handed a URL is not isolated - they hold a browser, a network and,
one directory up, the classifier. A reviewer handed 440 image files holds the
imagery and nothing else. So every card is rendered here, once, into a
self-contained JPEG: the aerial imagery, the two centrelines, the crossing
point, a scale bar, and an anonymous id. No network, no repository, no
recovery of what the classifier said.

WHAT A CARD DELIBERATELY DOES NOT CONTAIN
-----------------------------------------
The disposition. The deciding rule. The confidence. The road names - the
previous packs printed them, and "Hansen Road x Hansen Road" tells a reviewer
that the answer is "one road recorded twice" before they look at a pixel. The
link or source-feature ids, for the same reason. The stratum. Anything
positional: ids are assigned after a seeded shuffle of the WHOLE pack,
AT_GRADE and decoys together, so T001 is not more likely to be anything than
T440 is. Which centreline is drawn in which colour is randomised per card too,
so colour cannot correlate with state-highway status or with anything else.

The answer key is a separate file the reviewer is never given.

THE KEY
-------
The LINZ Basemaps API key is read from `.env` at render time and used only as
a query parameter on a tile request. It is not written into any emitted
artefact - there is no URL in a JPEG - and the tile cache under `scratch/_*`
holds image bytes, not requests. This is the same runtime-injection rule the
HTML generator follows, with the stronger property that the output format has
nowhere to put a key even by accident.

    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py build <outdir>
    cd python && PYTHONPATH=src python ../scratch/holdout3_review.py score <outdir> <verdicts.json>
"""
from __future__ import annotations

import collections
import concurrent.futures
import hashlib
import io
import json
import math
import random
import sys
import time
from pathlib import Path

from nzcl import db
from nzcl.config import REPO_ROOT

# A third seed. No card may be drawn because of where it sat in an earlier
# ordering.
SEED = "at-grade-holdout3-2026-08-18"
SNAP = "amds-national-2026-07-28-5b359d84"

#: Predeclared: "cards are rendered at zoom 19 and 512 px rather than zoom 18
#: and 400 px, so the reviewer is not guessing from four pixels of
#: carriageway". Not tunable from a result.
ZOOM = 19
IMG = 512
TILE = 256

#: Predeclared. 350 AT_GRADE cards; at most 4 failures; unreviewable counted
#: among the failures. `required_sample_size(4)` is 339 and 350 is drawn so the
#: declaration is not sitting on its own boundary.
AT_GRADE_TARGET = 350

#: No holdout card may lie within this of a card any reviewer has already seen.
INDEPENDENCE_M = 50.0

#: No single Road Controlling Authority may supply more than this share of the
#: AT_GRADE draw, so the pack measures New Zealand rather than Auckland.
RCA_CAP_FRAC = 0.12

AUDIT = REPO_ROOT / "docs/audits/at-grade-crossings"

# --------------------------------------------------------------------------
# The strata.
#
# The CELL LIST is predeclared: the third-holdout predeclaration names every
# one of these and says why it exists. The per-cell COUNTS are not - the
# declaration fixes n=350 and the failure tolerance, not the split - so they
# are set here, committed, and pushed BEFORE `build` is run for the first
# time, for the same reason the sample size was.
#
# They are the 248-card pack's cells scaled by 350/196, with the band
# immediately above the tangential threshold given the largest share, because
# that is where the classifier's own history says it is most likely wrong.
#
# Cells overlap and a card is drawn once. Where a cell cannot be filled from
# the eligible pool the shortfall is topped up from the unstratified AT_GRADE
# pool by the same seeded rank, and the top-up is reported - under-filling
# would quietly change n, which is the one number that may not move.
AT_GRADE_CELLS: dict[str, tuple[str, int]] = {
    # THE cell. 30 degrees is the tangential threshold and this band sits
    # immediately above it.
    "angle_30_40":       ("angleDeg >= 30 and angleDeg < 40", 40),
    "angle_40_60":       ("angleDeg >= 40 and angleDeg < 60", 28),
    "angle_60_80":       ("angleDeg >= 60 and angleDeg < 80", 28),
    "angle_80_90":       ("angleDeg >= 80", 36),
    # Immediately outside the widened 25 m structure match radius.
    "structure_near":    ("structDistM is not None and 25 < structDistM <= 70", 28),
    # Adversarial: the two roads carry the SAME name and DUPLICATE_GEOMETRY
    # did not withdraw them. An AT_GRADE row is by construction one the
    # duplicate rule did not fire on, so this cell IS the survivors.
    "same_name_not_dup": ("nameA and nameB and nameA == nameB", 25),
    # The source's best proxy for forestry, industrial and private access.
    "unsealed_access":   ("surfA in (2, 3) or surfB in (2, 3)", 25),
    "unnamed_both":      ("not nameA and not nameB", 21),
    "state_highway":     ("rcaA == 1 or rcaB == 1", 21),
    "urban":             ("urA == 'urban' and urB == 'urban'", 21),
    "rural":             ("urA == 'rural' and urB == 'rural'", 25),
    # The only HIGH-confidence AT_GRADE rule.
    "junction_witness":  ("reason == 'JUNCTION_WITNESS'", 21),
    # Imagery age, from the LINZ Basemaps attribution layer. An "<= 2018" cell
    # was tried for the 248-card pack and drew zero: Basemaps serves the
    # current mosaic, and the oldest survey over any crossing nationally is
    # 2019. This is the oldest band that exists.
    "imagery_older":     ("imageryYear is not None and imageryYear <= 2023", 25),
    "imagery_unknown":   ("imageryYear is None", 6),
}

#: Decoys. NOT part of n - the gate is about AT_GRADE precision - and scored
#: separately. They exist because a pack that is entirely AT_GRADE tells the
#: reviewer the answer before they look.
DECOY_CELLS: dict[str, tuple[str, int]] = {
    "gs_structure":      ("disposition == 'GRADE_SEPARATED' "
                          "and reason == 'STRUCTURE_MAPPED'", 24),
    # The other surviving GRADE_SEPARATED rule. It decides NOTHING on the
    # national record - see the build log - and the cell is kept so that fact
    # is recorded by an empty draw rather than by omission.
    "gs_named_structure": ("disposition == 'GRADE_SEPARATED' "
                           "and reason == 'NAMED_STRUCTURE'", 4),
    # The crossings MOTORWAY_CARRIAGEWAY now leaves unresolved rather than
    # asserting. It confirmed 2 of 8 on the previous pack.
    "un_motorway":       ("disposition == 'UNRESOLVED' "
                          "and reason == 'MOTORWAY_CARRIAGEWAY'", 16),
    # The corridor walk's own false-positive risk: the crossings it, and only
    # it, moved out of AT_GRADE. If it over-fires, these are where.
    "un_corridor_walk":  ("corridorWithdrawn", 14),
    "un_no_evidence":    ("disposition == 'UNRESOLVED' "
                          "and reason == 'NO_EVIDENCE_EITHER_WAY'", 14),
    "un_duplicate":      ("disposition == 'UNRESOLVED' "
                          "and reason == 'DUPLICATE_GEOMETRY' "
                          "and not corridorWithdrawn", 12),
    "un_tangential":     ("disposition == 'UNRESOLVED' "
                          "and reason == 'TANGENTIAL'", 10),
}

# Card colours. Chosen to survive both grass and asphalt, and to be
# distinguishable to the common forms of colour vision deficiency: a warm
# magenta against a cyan, which differ in blue channel as well as in hue.
COL_A = (255, 59, 107)
COL_B = (34, 211, 238)
COL_MARK = (255, 225, 77)
CASING = (10, 12, 16)
STRIP = (18, 21, 26)
INK = (232, 236, 241)


# --------------------------------------------------------------------------
def linz_key() -> str:
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_LINZ_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no VITE_LINZ_API_KEY in .env")


def lonlat_to_px(lon: float, lat: float, z: int = ZOOM) -> tuple[float, float]:
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def metres_per_pixel(lat: float, z: int = ZOOM) -> float:
    return 156543.03392804097 * math.cos(math.radians(lat)) / (2 ** z)


def rank(r: dict, salt: str = "") -> str:
    return hashlib.md5(
        f"{SEED}|{salt}|{r['groupA']}|{r['groupB']}|"
        f"{r['x']:.3f}|{r['y']:.3f}".encode()).hexdigest()


# --------------------------------------------------------------------------
def load_rows() -> list[dict]:
    src = AUDIT / "classified-v2.jsonl"
    if not src.exists():
        raise SystemExit(
            f"{src} is not present. It is derived and gitignored; the exact "
            f"command and its sha256 are in classified-v2-manifest.json.")
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    man = json.loads((AUDIT / "classified-v2-manifest.json")
                     .read_text(encoding="utf-8"))
    got = hashlib.sha256(src.read_bytes()).hexdigest()
    if got != man["sha256"]:
        raise SystemExit(
            f"{src} does not match its manifest.\n  manifest {man['sha256']}\n"
            f"  on disk  {got}\nThe holdout must be drawn from the recorded "
            f"national record, not from something that has drifted from it.")
    print(f"{len(rows)} crossing points in the national record "
          f"(sha256 matches the manifest)")
    return rows


def burned_points() -> tuple[list[tuple[float, float]], set[frozenset], dict]:
    """Every crossing any reviewer has already seen, as points AND as pairs.

    Points come out of the two blinded packs' answer keys directly. The
    unblinded pack recorded only link ids, so those are looked up in the v1
    record, which is the record it was drawn from.

    Pair exclusion needs a translation: the previous packs are keyed by graph
    LINK, this record by AMDS SOURCE FEATURE. The link ids are mapped to their
    closure_group_id so a burned pair can be excluded even if the two records
    disagree about where the crossing point is. The 50 m rule already covers
    that case; this is the belt to its braces.
    """
    pts: list[tuple[float, float]] = []
    link_pairs: set[tuple[int, int]] = set()
    provenance: dict[str, int] = {}

    for name, path in (("blind208", REPO_ROOT / "scratch/blind/answer-key.json"),
                       ("holdout248", REPO_ROOT / "scratch/holdout/answer-key.json")):
        if not path.exists():
            raise SystemExit(
                f"{path} is absent, so the {name} pack cannot be excluded. "
                f"Regenerate it before drawing this holdout - a holdout that "
                f"cannot prove its independence is not a holdout.")
        cards = json.loads(path.read_text(encoding="utf-8"))["cards"]
        for c in cards:
            pts.append((float(c["x"]), float(c["y"])))
            link_pairs.add((int(c["linkA"]), int(c["linkB"])))
        provenance[name] = len(cards)

    unblinded = json.loads((AUDIT / "review-verdicts.json")
                           .read_text(encoding="utf-8"))["verdicts"]
    v1 = AUDIT / "classified.jsonl"
    by_pair = {}
    if v1.exists():
        for line in v1.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                by_pair[(int(r["linkA"]), int(r["linkB"]))] = r
    else:
        print("  WARNING: classified.jsonl absent; the unblinded pack can only "
              "be excluded by link pair, not by position.")
    located = 0
    for v in unblinded:
        p = (int(v["linkA"]), int(v["linkB"]))
        link_pairs.add(p)
        if p in by_pair:
            pts.append((float(by_pair[p]["x"]), float(by_pair[p]["y"])))
            located += 1
    provenance["unblinded81"] = len(unblinded)
    provenance["unblindedLocatable"] = located

    group_pairs: set[frozenset] = set()
    ids = sorted({i for p in link_pairs for i in p})
    rows = db.query("SELECT link_id, closure_group_id FROM links "
                    " WHERE snapshot_id = %s AND link_id = ANY(%s)",
                    (SNAP, ids))
    grp = {r["link_id"]: r["closure_group_id"] for r in rows}
    for a, b in link_pairs:
        if a in grp and b in grp:
            group_pairs.add(frozenset((grp[a], grp[b])))
    provenance["burnedLinkPairs"] = len(link_pairs)
    provenance["burnedSourceFeaturePairs"] = len(group_pairs)
    provenance["burnedPoints"] = len(pts)
    return pts, group_pairs, provenance


def independent(rows: list[dict]) -> tuple[list[dict], dict]:
    pts, group_pairs, prov = burned_points()
    print(f"development + holdout set: {prov['burnedLinkPairs']} link pairs, "
          f"{prov['burnedSourceFeaturePairs']} source-feature pairs, "
          f"{prov['burnedPoints']} locatable points")

    cell = INDEPENDENCE_M
    grid: dict[tuple[int, int], list[tuple[float, float]]] = \
        collections.defaultdict(list)
    for x, y in pts:
        grid[(int(x // cell), int(y // cell))].append((x, y))

    kept, drop_pair, drop_near = [], 0, 0
    for r in rows:
        if frozenset((r["groupA"], r["groupB"])) in group_pairs:
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

    print(f"excluded {drop_pair} by source-feature pair and a further "
          f"{drop_near} within {INDEPENDENCE_M:.0f} m of a burned card")
    print(f"{len(kept)} of {len(rows)} crossings remain eligible")
    prov.update({"excludedBySourceFeaturePair": drop_pair,
                 "excludedAsNearNeighbourWithin50m": drop_near,
                 "eligiblePool": len(kept)})
    return kept, prov


# --------------------------------------------------------------------------
def attach_structure_distance(rows: list[dict]) -> None:
    """Distance to the nearest LINZ Topo50 bridge or tunnel centreline.

    The `structure_near` stratum wants the crossings that sit JUST OUTSIDE the
    25 m radius at which `STRUCTURE_MAPPED` fires. This is a plain nearest-
    centreline distance with no alignment test: the classifier requires
    alignment as well, and a stratum that applied the same filter would
    select only the cases the classifier already declined for a second reason.
    """
    from shapely import wkb
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    srows = db.query("SELECT ST_AsBinary(geom_2193) AS g FROM ext_structures")
    geoms = [wkb.loads(bytes(r["g"])) for r in srows]
    tree = STRtree(geoms)
    print(f"structure distance: {len(geoms)} LINZ Topo50 centrelines")
    for r in rows:
        p = Point(float(r["x"]), float(r["y"]))
        i = tree.nearest(p)
        r["structDistM"] = round(float(geoms[int(i)].distance(p)), 1) \
            if i is not None else None


def attach_imagery_year(rows: list[dict]) -> None:
    """Year of the LINZ aerial survey over each crossing, from Basemaps.

    Same source, same cache and same caveat as the 248-card pack: Basemaps
    serves the current mosaic rather than an archive, so this varies far less
    than it looks like it should.
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
        cache.write_text(json.dumps(doc), encoding="utf-8")

    polys, years = [], []
    for f in doc.get("features", []):
        p = f.get("properties") or {}
        if "Aerial Photos" not in (p.get("category") or ""):
            continue
        dt = p.get("end_datetime") or p.get("start_datetime") or p.get("datetime")
        if not dt:
            continue
        try:
            polys.append(shape(f["geometry"]))
        except Exception:  # noqa: BLE001
            continue
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
        r["_lon"], r["_lat"] = lon, lat
        hit += best is not None
    print(f"  matched a survey year for {hit} of {len(rows)} crossings")


def attach_corridor_withdrawn(rows: list[dict]) -> int:
    """Mark the crossings the corridor walk, and only it, took out of AT_GRADE."""
    path = AUDIT / "corridor-withdrawn.json"
    if not path.exists():
        raise SystemExit(
            f"{path} is absent. The `un_corridor_walk` stratum is predeclared "
            f"and cannot be drawn from a count. Run "
            f"scratch/corridor_withdrawn.py first.")
    doc = json.loads(path.read_text(encoding="utf-8"))
    want = {(round(c["x"], 3), round(c["y"], 3), c["groupA"], c["groupB"])
            for c in doc["crossings"]}
    n = 0
    for r in rows:
        k = (round(r["x"], 3), round(r["y"], 3), r["groupA"], r["groupB"])
        r["corridorWithdrawn"] = k in want
        n += r["corridorWithdrawn"]
    print(f"corridor walk withdrew {doc['count']} crossings nationally; "
          f"{n} of them are in the eligible pool")
    return n


# --------------------------------------------------------------------------
def draw(pool: list[dict], cells: dict, rca_cap: int | None,
         picked: dict) -> dict[str, dict]:
    """Fill each cell by seeded rank, honouring the per-authority cap first."""
    rca_used: collections.Counter = collections.Counter()
    for r in picked.values():
        rca_used[str(r.get("rcaNameA") or r.get("rcaNameB") or "unknown")] += 1
    report: dict[str, dict] = {}

    for cell, (expr, want) in cells.items():
        cand = []
        for r in pool:
            try:
                if eval(expr, {"__builtins__": {}}, dict(r)):  # noqa: S307
                    cand.append(r)
            except Exception:  # noqa: BLE001
                continue
        cand.sort(key=lambda r: rank(r, cell))
        taken, skipped_cap = 0, 0
        for relaxed in (False, True):
            for r in cand:
                if taken >= want:
                    break
                key = (r["groupA"], r["groupB"], r["x"], r["y"])
                if key in picked:
                    continue
                rca = str(r.get("rcaNameA") or r.get("rcaNameB") or "unknown")
                if not relaxed and rca_cap is not None and rca_used[rca] >= rca_cap:
                    skipped_cap += 1
                    continue
                picked[key] = dict(r, cell=cell)
                rca_used[rca] += 1
                taken += 1
            if taken >= want:
                break
        report[cell] = {"intended": want, "drawn": taken,
                        "available": len(cand),
                        "deferredByRcaCap": skipped_cap}
        flag = "" if taken >= want else "   <-- UNDER-FILLED"
        print(f"  {cell:<20} intended {want:>3}  drawn {taken:>3}  "
              f"(pool {len(cand):>5}){flag}")
    return report


def top_up(pool: list[dict], picked: dict, target: int,
           rca_cap: int | None) -> int:
    """Bring the AT_GRADE draw back to exactly n.

    n is predeclared and the arithmetic of the gate depends on it. Cells
    overlap, and some cannot be filled from the eligible pool; letting the
    total fall short would silently move the number the declaration fixed.
    Top-ups carry cell "topup" and are reported as such - they are ordinary
    AT_GRADE crossings drawn by the same seeded rank, not a re-draw of
    anything.
    """
    short = target - len(picked)
    if short <= 0:
        return 0
    rca_used: collections.Counter = collections.Counter(
        str(r.get("rcaNameA") or r.get("rcaNameB") or "unknown")
        for r in picked.values())
    cand = sorted(pool, key=lambda r: rank(r, "topup"))
    added = 0
    for relaxed in (False, True):
        for r in cand:
            if added >= short:
                break
            key = (r["groupA"], r["groupB"], r["x"], r["y"])
            if key in picked:
                continue
            rca = str(r.get("rcaNameA") or r.get("rcaNameB") or "unknown")
            if not relaxed and rca_cap is not None and rca_used[rca] >= rca_cap:
                continue
            picked[key] = dict(r, cell="topup")
            rca_used[rca] += 1
            added += 1
        if added >= short:
            break
    print(f"  topped up {added} card(s) to reach the predeclared n={target}")
    return added


# --------------------------------------------------------------------------
class Tiles:
    """Fetch-and-cache for LINZ Basemaps tiles.

    The key goes on the wire and nowhere else. The cache holds decoded image
    bytes under scratch/_tiles, which the repository's `scratch/_*` rule
    already ignores.
    """

    def __init__(self) -> None:
        import requests
        self.dir = REPO_ROOT / "scratch/_tiles" / str(ZOOM)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key = linz_key()
        self.session = requests.Session()
        self.failed: set[tuple[int, int]] = set()

    def path(self, tx: int, ty: int) -> Path:
        return self.dir / f"{tx}_{ty}.webp"

    def fetch(self, tx: int, ty: int) -> None:
        p = self.path(tx, ty)
        if p.exists() and p.stat().st_size > 0:
            return
        url = (f"https://basemaps.linz.govt.nz/v1/tiles/aerial/"
               f"WebMercatorQuad/{ZOOM}/{tx}/{ty}.webp")
        for attempt in range(4):
            try:
                r = self.session.get(url, params={"api": self.key}, timeout=45)
                if r.status_code == 200 and r.content:
                    p.write_bytes(r.content)
                    return
                if r.status_code in (404, 204):
                    self.failed.add((tx, ty))
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.5 * (attempt + 1))
        self.failed.add((tx, ty))

    def image(self, tx: int, ty: int):
        from PIL import Image
        p = self.path(tx, ty)
        if not p.exists() or p.stat().st_size == 0:
            return None
        try:
            return Image.open(io.BytesIO(p.read_bytes())).convert("RGB")
        except Exception:  # noqa: BLE001
            return None


def tiles_for(cx: float, cy: float) -> list[tuple[int, int]]:
    left, top = cx - IMG / 2, cy - IMG / 2
    tx0, ty0 = int(left // TILE), int(top // TILE)
    tx1, ty1 = int((left + IMG) // TILE), int((top + IMG) // TILE)
    return [(tx, ty) for tx in range(tx0, tx1 + 1)
            for ty in range(ty0, ty1 + 1)]


# --------------------------------------------------------------------------
def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold
             else ["DejaVuSans.ttf", "arial.ttf"])
    roots = ["/usr/share/fonts/truetype/dejavu/", "/mnt/c/Windows/Fonts/",
             "C:/Windows/Fonts/", ""]
    for root in roots:
        for n in names:
            try:
                return ImageFont.truetype(root + n, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _clip(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Keep only what lands on the card. A source feature can be kilometres
    long; drawing all of it would be slow and would tell PIL to rasterise
    lines a hundred thousand pixels away."""
    from shapely.geometry import LineString, box
    if len(points) < 2:
        return []
    g = LineString(points).intersection(box(-64, -64, IMG + 64, IMG + 64))
    if g.is_empty:
        return []
    parts = [g] if g.geom_type == "LineString" else \
        [p for p in getattr(g, "geoms", []) if p.geom_type == "LineString"]
    return [list(p.coords) for p in parts if len(p.coords) >= 2]


def render_card(code: str, row: dict, geo: dict, tiles: Tiles,
                out: Path) -> dict:
    """One card, self-contained, with nothing on it but what a reviewer needs."""
    from PIL import Image, ImageDraw

    lon, lat = row["_lon"], row["_lat"]
    cx, cy = lonlat_to_px(lon, lat)
    left, top = cx - IMG / 2, cy - IMG / 2

    base = Image.new("RGB", (IMG, IMG), (12, 14, 18))
    missing = 0
    for tx, ty in tiles_for(cx, cy):
        im = tiles.image(tx, ty)
        if im is None:
            missing += 1
            continue
        base.paste(im, (int(round(tx * TILE - left)), int(round(ty * TILE - top))))

    d = ImageDraw.Draw(base, "RGBA")

    # Which centreline gets which colour is randomised per card, so colour
    # cannot correlate with anything the classifier used.
    swap = int(hashlib.md5(f"{SEED}|swap|{code}".encode()).hexdigest(), 16) & 1
    groups = [row["groupA"], row["groupB"]]
    if swap:
        groups.reverse()

    for colour, grp in zip((COL_A, COL_B), groups):
        for coords in geo.get(grp, ()):
            px = [tuple(v - o for v, o in zip(lonlat_to_px(lo, la), (left, top)))
                  for lo, la in coords]
            for part in _clip(px):
                d.line(part, fill=CASING + (200,), width=7, joint="curve")
        for coords in geo.get(grp, ()):
            px = [tuple(v - o for v, o in zip(lonlat_to_px(lo, la), (left, top)))
                  for lo, la in coords]
            for part in _clip(px):
                d.line(part, fill=colour + (235,), width=3, joint="curve")

    # The crossing point. A ring rather than a blob: the reviewer has to be
    # able to see the ground it is pointing at.
    m = IMG / 2
    d.ellipse((m - 17, m - 17, m + 17, m + 17), outline=CASING + (210,), width=5)
    d.ellipse((m - 17, m - 17, m + 17, m + 17), outline=COL_MARK + (255,), width=3)
    d.ellipse((m - 2.5, m - 2.5, m + 2.5, m + 2.5), fill=COL_MARK + (255,))

    # Scale bar, bottom left, on a plate so it reads over any ground cover.
    mpp = metres_per_pixel(lat)
    for metres in (100, 50, 25, 20, 10):
        bar = metres / mpp
        if 55 <= bar <= 190:
            break
    f_small = _font(13, bold=True)
    bx, by = 14, IMG - 22
    d.rectangle((bx - 8, by - 22, bx + bar + 58, by + 12), fill=(0, 0, 0, 150))
    d.line((bx, by, bx + bar, by), fill=(255, 255, 255), width=3)
    for xx in (bx, bx + bar):
        d.line((xx, by - 6, xx, by + 6), fill=(255, 255, 255), width=3)
    d.text((bx + bar + 8, by - 8), f"{metres} m", font=f_small, fill=(255, 255, 255))
    d.text((bx, by - 21), "north is up", font=_font(11), fill=(225, 225, 225))

    # Header strip: the anonymous id and the legend. Nothing else.
    head = 44
    card = Image.new("RGB", (IMG, IMG + head), STRIP)
    card.paste(base, (0, head))
    dh = ImageDraw.Draw(card)
    dh.text((14, 12), code, font=_font(21, bold=True), fill=INK)
    f_leg = _font(13)
    x0 = 96
    for colour, label in ((COL_A, "centreline A"), (COL_B, "centreline B")):
        dh.line((x0, 22, x0 + 26, 22), fill=colour, width=4)
        dh.text((x0 + 32, 15), label, font=f_leg, fill=INK)
        x0 += 132
    dh.ellipse((x0 + 3, 15, x0 + 17, 29), outline=COL_MARK, width=2)
    dh.text((x0 + 24, 15), "crossing point", font=f_leg, fill=INK)

    path = out / f"{code}.jpg"
    card.save(path, "JPEG", quality=92, subsampling=0, optimize=True)
    return {"missingTiles": missing, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "colourA": groups[0], "metresPerPixel": round(mpp, 4),
            "scaleBarM": metres}


# --------------------------------------------------------------------------
REVIEWER_INSTRUCTIONS = """\
# Crossing review

You are looking at {n} aerial photographs of places in New Zealand where two
road centrelines cross each other. Each image is one place. For each one,
decide what is on the ground.

## What is drawn on each image

- the aerial imagery, north up, with a scale bar;
- **centreline A** in pink and **centreline B** in cyan - these are the two
  mapped road centrelines that cross here;
- a **yellow ring** around the exact point where the two centrelines cross;
- a card id at the top left.

Nothing else is drawn, and nothing about the two roads other than their
geometry is available to you. That is deliberate.

## The question

**At the yellow ring, can a vehicle travel from one road onto the other?**

Answer with exactly one of:

- `a` - **at grade**. The two roads meet on the ground and a vehicle can pass
  between them. An ordinary intersection, crossroads, T-junction under the
  ring, staggered junction, or a roundabout the ring sits on.
- `g` - **grade separated**. Both roads are there, but one passes over or
  under the other. A bridge, an overbridge, an underpass, a flyover, a tunnel.
  A vehicle cannot pass between them at this point.
- `n` - **not a junction**. There is no place here where two roads meet. Use
  this when what you see is one single road that has been drawn twice (both
  centrelines run along the same piece of tarmac), when one of the
  centrelines does not correspond to any visible road, or when the two lines
  cross somewhere no road exists at all.
- `u` - **unclear**. You cannot tell from this image. Cloud, shadow, tree
  canopy, the imagery is too old or too coarse, or the centrelines plainly do
  not line up with anything you can see.

Use `u` honestly. It is recorded as its own outcome and it is not a
throwaway. Do NOT guess in order to avoid it, and do NOT use it for a card
that is merely unusual - only for one you genuinely cannot read.

## How to decide between them

- **at grade vs grade separated**: look for a structure. Parapets or bridge
  rails, an abrupt change in the shape of the road edge, an embankment or
  cutting, one road's shadow falling on the other, ramps leaving and rejoining.
  If the two carriageways clearly touch and there is a visible connection -
  corner radii, a painted intersection, worn turning paths - it is at grade.
- **at grade vs not a junction**: ask whether there are TWO roads. If both
  coloured lines run along the same single strip of tarmac, or one line
  wanders across paddocks, buildings or water with no road under it, that is
  `n`, not `a`.
- A junction slightly offset from the ring is still `a` if the two roads
  connect at that place. The ring marks where the two lines cross, which is
  not always exactly where the tarmac meets.

## Output

Return one line per card, in card order:

    T001 a
    T002 g
    T003 u

If you want to record a reason, put it after the letter:

    T004 n  both lines run down the same farm track

Review every card. Do not skip any. If you are unsure, that is what `u` is
for.
"""


def build(outdir: Path, workers: int = 8) -> int:
    rows = load_rows()
    eligible, prov = independent(rows)
    print()
    attach_structure_distance(eligible)
    attach_imagery_year(eligible)
    n_withdrawn = attach_corridor_withdrawn(eligible)

    at_grade_pool = [r for r in eligible if r["disposition"] == "AT_GRADE"]
    cap = max(4, int(AT_GRADE_TARGET * RCA_CAP_FRAC))
    print(f"\nAT_GRADE draw, target {AT_GRADE_TARGET}, "
          f"RCA cap {cap} per authority, pool {len(at_grade_pool)}:")
    picked: dict = {}
    cell_report = draw(at_grade_pool, AT_GRADE_CELLS, cap, picked)
    topped = top_up(at_grade_pool, picked, AT_GRADE_TARGET, cap)
    at_grade = list(picked.values())
    if len(at_grade) != AT_GRADE_TARGET:
        raise SystemExit(
            f"drew {len(at_grade)} AT_GRADE cards, and the predeclaration says "
            f"{AT_GRADE_TARGET}. The declared number may not move. Stop.")

    print(f"\ndecoy draw (not part of n, scored separately), "
          f"{n_withdrawn} corridor-withdrawn crossings eligible:")
    decoy_picked: dict = dict(picked)
    decoy_report = draw(eligible, DECOY_CELLS, None, decoy_picked)
    decoys = [r for k, r in decoy_picked.items() if k not in picked]

    sample = at_grade + decoys
    print(f"\ndrew {len(at_grade)} AT_GRADE + {len(decoys)} decoys "
          f"= {len(sample)} cards")

    rng = random.Random(SEED)
    rng.shuffle(sample)
    for i, r in enumerate(sample, 1):
        r["code"] = f"T{i:03d}"

    # Geometry, per SOURCE FEATURE. A feature is drawn as its link pieces
    # rather than re-merged: on a card they are the same line, and merging
    # can only introduce an ordering question that changes nothing visible.
    groups = sorted({g for r in sample for g in (r["groupA"], r["groupB"])})
    print(f"\nfetching geometry for {len(groups)} source features")
    geo: dict[str, list] = collections.defaultdict(list)
    CHUNK = 400
    for i in range(0, len(groups), CHUNK):
        for g in db.query(
                "SELECT closure_group_id, ST_AsGeoJSON(geom_4326, 7) AS gj "
                "  FROM links WHERE snapshot_id = %s "
                "   AND closure_group_id = ANY(%s)",
                (SNAP, groups[i:i + CHUNK])):
            geo[g["closure_group_id"]].append(
                json.loads(g["gj"])["coordinates"])
    print(f"  {sum(len(v) for v in geo.values())} link geometries")

    cards_dir = outdir / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)

    tiles = Tiles()
    need: set[tuple[int, int]] = set()
    for r in sample:
        cx, cy = lonlat_to_px(r["_lon"], r["_lat"])
        need.update(tiles_for(cx, cy))
    todo = [t for t in sorted(need) if not tiles.path(*t).exists()]
    print(f"\n{len(need)} distinct tiles, {len(todo)} not cached")
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for n, _ in enumerate(ex.map(lambda t: tiles.fetch(*t), todo), 1):
            if n % 250 == 0:
                print(f"  {n}/{len(todo)} in {time.perf_counter()-t0:.0f}s")
    print(f"  fetched in {time.perf_counter()-t0:.0f}s; "
          f"{len(tiles.failed)} tiles unavailable")

    print(f"\nrendering {len(sample)} cards into {cards_dir}")
    t0 = time.perf_counter()
    meta: dict[str, dict] = {}
    for n, r in enumerate(sorted(sample, key=lambda r: r["code"]), 1):
        meta[r["code"]] = render_card(r["code"], r, geo, tiles, cards_dir)
        if n % 100 == 0:
            print(f"  {n}/{len(sample)} in {time.perf_counter()-t0:.0f}s")
    total_bytes = sum(m["bytes"] for m in meta.values())
    holes = sum(1 for m in meta.values() if m["missingTiles"])
    print(f"  rendered in {time.perf_counter()-t0:.0f}s, "
          f"{total_bytes/1e6:.1f} MB, {holes} cards with a missing tile")

    (cards_dir / "INSTRUCTIONS.md").write_text(
        REVIEWER_INSTRUCTIONS.format(n=len(sample)), encoding="utf-8")

    # ---- the answer key. The reviewer never receives this file. ----------
    key = {
        "WARNING": ("ANSWER KEY. Do not show this file, or anything derived "
                    "from it, to the reviewer or to any agent that will "
                    "review these cards."),
        "pack": "holdout3",
        "seed": SEED,
        "snapshot": SNAP,
        "drawnFrom": "classified-v2.jsonl",
        "predeclaration": "docs/audits/at-grade-crossings/third-holdout-predeclaration.md",
        "zoom": ZOOM, "cardPx": IMG,
        "independenceM": INDEPENDENCE_M,
        "independence": prov,
        "atGradeDrawn": len(at_grade),
        "atGradeTarget": AT_GRADE_TARGET,
        "atGradeToppedUp": topped,
        "decoysDrawn": len(decoys),
        "cells": cell_report,
        "decoyCells": decoy_report,
        "cards": [{
            "code": r["code"],
            "cell": r["cell"],
            "disposition": r["disposition"],
            "reason": r["reason"],
            "confidence": r["confidence"],
            "safeToNode": r["safeToNode"],
            "wasBeforePlaceRule": r["wasBeforePlaceRule"],
            "groupA": r["groupA"], "groupB": r["groupB"],
            "x": r["x"], "y": r["y"],
            "angleDeg": r["angleDeg"],
            "nameA": r["nameA"], "nameB": r["nameB"],
            "rcaA": r["rcaA"], "rcaB": r["rcaB"],
            "rcaNameA": r["rcaNameA"], "rcaNameB": r["rcaNameB"],
            "surfA": r["surfA"], "surfB": r["surfB"],
            "urA": r["urA"], "urB": r["urB"],
            "structDistM": r.get("structDistM"),
            "imageryYear": r.get("imageryYear"),
            "corridorWithdrawn": r.get("corridorWithdrawn"),
            "colourA": meta[r["code"]]["colourA"],
        } for r in sorted(sample, key=lambda r: r["code"])],
    }
    (outdir / "answer-key.json").write_text(json.dumps(key, indent=2) + "\n",
                                            encoding="utf-8")

    manifest = {
        "pack": "holdout3",
        "producedBy": "scratch/holdout3_review.py build",
        "seed": SEED, "snapshot": SNAP, "zoom": ZOOM, "cardPx": IMG,
        "cards": len(sample),
        "atGrade": len(at_grade), "decoys": len(decoys),
        "totalBytes": total_bytes,
        "why_not_committed": (
            "440 rendered JPEGs, reproducible exactly from this repository "
            "plus a LINZ Basemaps key. Same convention as "
            "classified-v2-manifest.json: the sha256 of every card is here, "
            "so a card handed to a reviewer can be proved to be the card that "
            "was drawn."),
        "regenerate": ("cd python && PYTHONPATH=src python "
                       "../scratch/holdout3_review.py build <outdir>"),
        "files": {c: {"bytes": m["bytes"], "sha256": m["sha256"],
                      "missingTiles": m["missingTiles"]}
                  for c, m in sorted(meta.items())},
    }
    (outdir / "cards-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    _report_spread(sample, at_grade)
    print(f"\nwrote {len(sample)} cards, answer-key.json and "
          f"cards-manifest.json to {outdir}")
    print("The reviewer is given ONLY the cards/ directory.")
    return 0


def _report_spread(sample: list[dict], at_grade: list[dict]) -> None:
    ys = sorted(r["y"] for r in sample)
    print(f"\nspread: northing {ys[0]:.0f} .. {ys[-1]:.0f} "
          f"({(ys[-1]-ys[0])/1000:.0f} km of New Zealand), "
          f"{len({int(r['y'] // 200000) for r in sample})} occupied "
          f"200 km bands")
    rca = collections.Counter(str(r.get("rcaNameA") or "unknown")
                              for r in at_grade)
    print(f"  AT_GRADE spans {len(rca)} road controlling authorities")
    for name, n in rca.most_common(8):
        print(f"    {n:>4}  {name}")
    for label, fn in (("imagery year", lambda r: r.get("imageryYear")),
                      ("urban/rural A", lambda r: r.get("urA")),
                      ("deciding rule", lambda r: r["reason"])):
        c = collections.Counter(fn(r) for r in at_grade)
        print(f"  {label}: " + ", ".join(
            f"{k}:{v}" for k, v in sorted(
                c.items(), key=lambda kv: (kv[0] is None, str(kv[0])))))


# --------------------------------------------------------------------------
#: What a reviewer verdict is allowed to be, per disposition. Unchanged from
#: the previous packs, and stated here so scoring cannot quietly re-interpret
#: it: AT_GRADE is the only disposition that creates a node, so only "at
#: grade" confirms it.
ACCEPTS = {
    "AT_GRADE": {"at_grade"},
    "GRADE_SEPARATED": {"grade_separated", "not_a_junction"},
    "UNRESOLVED": {"at_grade", "grade_separated", "not_a_junction"},
}
LEGEND = {"a": "at_grade", "g": "grade_separated",
          "n": "not_a_junction", "u": "unclear"}


def _load_verdicts(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        out = []
        for line in text.splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2 and parts[0].startswith("T"):
                out.append({"code": parts[0],
                            "verdict": LEGEND.get(parts[1], parts[1]),
                            "note": parts[2] if len(parts) > 2 else ""})
        return out
    v = doc["verdicts"] if isinstance(doc, dict) else doc
    if isinstance(v, str):
        toks = v.split()
        return [{"code": toks[i], "verdict": LEGEND.get(toks[i + 1], toks[i + 1])}
                for i in range(0, len(toks) - 1, 2)]
    return [{**r, "verdict": LEGEND.get(r["verdict"], r["verdict"])} for r in v]


def score(outdir: Path, verdicts_path: Path) -> int:
    from nzcl import promotion

    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in key["cards"]}
    verdicts = _load_verdicts(verdicts_path)

    rows, seen = [], set()
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
        print("  A card with no verdict is not a pass. The predeclaration "
              "counts every unreviewable card as a failure; a card that was "
              "never reviewed is at best the same thing.")

    ag = [r for r in rows if r["disposition"] == "AT_GRADE"]
    conf = sum(1 for r in ag if r["verdict"] in ACCEPTS["AT_GRADE"])
    unrev = sum(1 for r in ag if r["verdict"] == "unclear")
    contra = len(ag) - conf - unrev
    gs_fp = sum(1 for r in ag if r["verdict"] == "grade_separated")
    nj_fp = sum(1 for r in ag if r["verdict"] == "not_a_junction")

    gate = promotion.evaluate(
        confirmed=conf, contradicted=contra, unreviewable=unrev,
        grade_separated_false_positives=gs_fp,
        not_a_junction_false_positives=nj_fp)
    print("\n=== PROMOTION GATE ===")
    for c in gate.conditions:
        print(f"  [{'MET' if c.met else 'NOT MET'}] {c.name}: "
              f"{c.requirement}, observed {c.observed}")
    print(f"  overall: {'MET' if gate.met else 'NOT MET'}")
    print(f"  {gate.detail}")

    out = {"pack": "holdout3", "seed": SEED, "snapshot": SNAP,
           "cardsScored": len(rows), "notScored": missing,
           "atGrade": {"n": len(ag), "confirmed": conf,
                       "contradicted": contra, "unreviewable": unrev,
                       "gradeSeparatedFalsePositives": gs_fp,
                       "notAJunctionFalsePositives": nj_fp},
           "promotionGate": gate.as_dict(),
           "byCell": {}, "byRule": {}, "decoys": {}, "verdicts": rows}
    for cell in sorted({r["cell"] for r in ag}):
        sub = [r for r in ag if r["cell"] == cell]
        out["byCell"][cell] = {
            "n": len(sub),
            "confirmed": sum(1 for r in sub if r["verdict"] in ACCEPTS["AT_GRADE"]),
            "unreviewable": sum(1 for r in sub if r["verdict"] == "unclear")}
    for reason in sorted({r["reason"] for r in ag}):
        sub = [r for r in ag if r["reason"] == reason]
        out["byRule"][reason] = {
            "n": len(sub),
            "confirmed": sum(1 for r in sub if r["verdict"] in ACCEPTS["AT_GRADE"]),
            "unreviewable": sum(1 for r in sub if r["verdict"] == "unclear")}
    for disp in ("GRADE_SEPARATED", "UNRESOLVED"):
        sub = [r for r in rows if r["disposition"] == disp]
        out["decoys"][disp] = {
            "n": len(sub),
            "confirmed": sum(1 for r in sub if r["verdict"] in ACCEPTS[disp]),
            "unreviewable": sum(1 for r in sub if r["verdict"] == "unclear")}
    (outdir / "holdout3-result.json").write_text(json.dumps(out, indent=2) + "\n",
                                                 encoding="utf-8")
    print(f"\nwrote {outdir/'holdout3-result.json'}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "build":
        raise SystemExit(build(Path(sys.argv[2])))
    if cmd == "score":
        raise SystemExit(score(Path(sys.argv[2]), Path(sys.argv[3])))
    raise SystemExit("build <outdir> | score <outdir> <verdicts>")
