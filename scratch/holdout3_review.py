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
import re
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
    if not v1.exists():
        raise SystemExit(
            f"{v1} is absent, so the 81 cards of the earlier unblinded pack "
            f"cannot be located and the 50 m exclusion cannot be applied to "
            f"them. The declaration promises exclusion around EVERY prior "
            f"reviewed place; a warning here would leave that promise "
            f"unenforced. Regenerate the v1 record before drawing.")
    by_pair = {}
    for line in v1.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            by_pair[(int(r["linkA"]), int(r["linkB"]))] = r
    located, unlocatable = 0, []
    for v in unblinded:
        p = (int(v["linkA"]), int(v["linkB"]))
        link_pairs.add(p)
        r = by_pair.get(p) or by_pair.get((p[1], p[0]))
        if r is None:
            unlocatable.append(p)
            continue
        pts.append((float(r["x"]), float(r["y"])))
        located += 1
    provenance["unblinded81"] = len(unblinded)
    provenance["unblindedLocatable"] = located

    # FAIL CLOSED. Every prior reviewed card must be locatable as a point,
    # because the 50 m rule is what stops a holdout card being a near
    # neighbour of a burned one, and a card whose position is unknown cannot
    # have a radius drawn around it. A short count here means the exclusion is
    # weaker than the declaration says it is, and the only honest response is
    # to stop rather than to draw a pack whose independence is partly assumed.
    expected_points = sum(provenance[k] for k in
                          ("blind208", "holdout248", "unblinded81"))
    provenance["priorCardsExpected"] = expected_points
    provenance["priorCardsLocated"] = len(pts)
    if unlocatable or len(pts) != expected_points:
        raise SystemExit(
            f"STOPPED: {expected_points - len(pts)} of {expected_points} prior "
            f"reviewed cards could not be located, so the 50 m exclusion "
            f"cannot be applied to them.\n  unlocatable link pairs: "
            f"{unlocatable[:20]}\nThe declared independence is exact-pair "
            f"exclusion AND a 50 m radius around every prior reviewed place. "
            f"A warning is not enough.")

    group_pairs: set[frozenset] = set()
    ids = sorted({i for p in link_pairs for i in p})
    rows = db.query("SELECT link_id, closure_group_id FROM links "
                    " WHERE snapshot_id = %s AND link_id = ANY(%s)",
                    (SNAP, ids))
    grp = {r["link_id"]: r["closure_group_id"] for r in rows}
    unmapped = [(a, b) for a, b in link_pairs if a not in grp or b not in grp]
    if unmapped:
        raise SystemExit(
            f"STOPPED: {len(unmapped)} prior link pair(s) could not be mapped "
            f"to AMDS source features, so they cannot be excluded by pair: "
            f"{unmapped[:20]}")
    for a, b in link_pairs:
        group_pairs.add(frozenset((grp[a], grp[b])))
    provenance["burnedLinkPairs"] = len(link_pairs)
    provenance["burnedLinkIds"] = len(ids)
    provenance["burnedLinkIdsMapped"] = len(grp)
    provenance["burnedSourceFeaturePairs"] = len(group_pairs)
    # Two link pairs can be two graph pieces of ONE source-feature pair, so
    # this number is allowed to be smaller. It is recorded rather than left to
    # be re-derived, because "535 from 536" otherwise reads like a lost row.
    provenance["linkPairsCollapsingOntoAnotherSourcePair"] = \
        len(link_pairs) - len(group_pairs)
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
    # Unordered pair: `load_sources` reads the links table with no ORDER BY,
    # so which side of a crossing is A is not stable between runs.
    want = {(round(c["x"], 3), round(c["y"], 3),
             frozenset((c["groupA"], c["groupB"]))) for c in doc["crossings"]}
    n = 0
    for r in rows:
        k = (round(r["x"], 3), round(r["y"], 3),
             frozenset((r["groupA"], r["groupB"])))
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


def qualifying_cells(r: dict) -> list[str]:
    """Every stratum a card belongs to, not just the one that claimed it.

    Cells overlap and a card is drawn once, so `cell` records which cell got
    there first. That is an artefact of iteration order, and reporting a
    stratum's result by it would understate the stratum: `same_name_not_dup`
    has 5 members in the whole eligible pool and four of them are claimed by
    an angle cell before it is reached. Scoring joins on THIS list.
    """
    out = []
    for name, (expr, _) in {**AT_GRADE_CELLS, **DECOY_CELLS}.items():
        try:
            if eval(expr, {"__builtins__": {}}, dict(r)):  # noqa: S307
                out.append(name)
        except Exception:  # noqa: BLE001
            continue
    return out


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


def select(verbose: bool = True) -> tuple[list[dict], list[dict], dict, dict, dict, int]:
    """The draw, with nothing rendered and nothing written.

    `plan` runs this and stops. It is deterministic from SEED, so it selects
    the identical cards `build` will, and it exists so a rendering bug does
    not cost 3,500 tile fetches. It reports which cells could not be filled -
    which is a fact about the eligible pool, not licence to change a cell
    count. The counts were committed before the first draw and they stay put.
    """
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
    print(f"\ndrew {len(at_grade)} AT_GRADE + {len(decoys)} decoys "
          f"= {len(at_grade) + len(decoys)} cards")

    print("\nstratum MEMBERSHIP of the 350 AT_GRADE cards (cells overlap, so "
          "these do not sum to 350):")
    member = collections.Counter(c for r in at_grade
                                 for c in qualifying_cells(r))
    pool_member = collections.Counter(c for r in at_grade_pool
                                      for c in qualifying_cells(r))
    for cell in AT_GRADE_CELLS:
        print(f"  {cell:<20} in pack {member[cell]:>3}  "
              f"of {pool_member[cell]:>5} nationally eligible")
    print(f"  {'topup (no cell)':<20} in pack "
          f"{sum(1 for r in at_grade if r['cell'] == 'topup'):>3}")

    prov["cellReport"] = cell_report
    return at_grade, decoys, cell_report, decoy_report, prov, topped


def plan() -> int:
    at_grade, decoys, _, _, _, topped = select()
    _report_spread(at_grade + decoys, at_grade)
    print(f"\nplan only: nothing rendered, nothing written. "
          f"{topped} top-up card(s).")
    return 0


def build(outdir: Path, workers: int = 8) -> int:
    at_grade, decoys, cell_report, decoy_report, prov, topped = select()
    sample = at_grade + decoys

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
                "  FROM links WHERE snapshot_id = %s AND mode_vehicle "
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
    holed = sorted(c for c, m in meta.items() if m["missingTiles"])
    print(f"  rendered in {time.perf_counter()-t0:.0f}s, "
          f"{total_bytes/1e6:.1f} MB, {len(holed)} cards with a missing tile")

    # A transient tile failure is retried as THE SAME CARD. It is never
    # answered by dropping the card or drawing a replacement: the cards that
    # fail to render are not a random subset, and substituting a more
    # convenient case for an awkward one is sample selection performed by the
    # network. If a card still will not render after the retry, the build
    # stops and the pack is not handed over.
    if holed:
        print(f"  retrying {len(holed)} card(s) with a missing tile")
        retry: set[tuple[int, int]] = set()
        for code in holed:
            r = next(x for x in sample if x["code"] == code)
            cx, cy = lonlat_to_px(r["_lon"], r["_lat"])
            retry.update(tiles_for(cx, cy))
        tiles.failed.clear()
        for t in sorted(retry):
            p = tiles.path(*t)
            if not p.exists() or p.stat().st_size == 0:
                tiles.fetch(*t)
        for code in holed:
            r = next(x for x in sample if x["code"] == code)
            meta[code] = render_card(code, r, geo, tiles, cards_dir)
        total_bytes = sum(m["bytes"] for m in meta.values())
        still = sorted(c for c, m in meta.items() if m["missingTiles"])
        if still:
            raise SystemExit(
                f"STOPPED: {len(still)} card(s) still have a missing imagery "
                f"tile after a retry: {', '.join(still)}.\nThe pack is not "
                f"sealed and must not be handed to a reviewer. Fix the tile "
                f"source and re-run `build`; do NOT drop these cards and do "
                f"NOT substitute others for them.")
        print("  retry cleared every hole")

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
            "qualifyingCells": qualifying_cells(r),
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
        "unresolvedTileFailures": sum(m["missingTiles"] for m in meta.values()),
        "cardsWithAMissingTile": sorted(c for c, m in meta.items()
                                        if m["missingTiles"]),
        "independence": prov,
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
#: The verdict vocabulary and the disposition semantics live in
#: `nzcl.holdout`, which is under test. They are imported rather than restated
#: here: a second copy is a second thing to keep in step, and the copy in the
#: scratch script is the one nobody would notice drifting.
from nzcl.holdout import ACCEPTS, LEGEND  # noqa: E402


def _load_verdicts(path: Path) -> list[dict]:
    """Parse a verdict file. Parses only - it never drops or resolves a row.

    Every line that looks like a verdict is returned, including duplicates and
    labels that are not in the legend, because deciding what to do about those
    is `nzcl.holdout.collate`'s job and silently discarding them here would
    defeat it. Lines that carry no verdict at all are returned with a `None`
    verdict rather than skipped, so an unparseable answer for a real card is
    an invalid verdict rather than a missing one.
    """
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if not re.fullmatch(r"[A-Za-z]\d{1,4}", parts[0]):
                continue
            out.append({"code": parts[0],
                        "verdict": parts[1] if len(parts) > 1 else None,
                        "note": parts[2] if len(parts) > 2 else ""})
        return out
    v = doc["verdicts"] if isinstance(doc, dict) else doc
    if isinstance(v, str):
        toks = v.split()
        return [{"code": toks[i], "verdict": toks[i + 1]}
                for i in range(0, len(toks) - 1, 2)]
    return list(v)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal(outdir: Path) -> int:
    """Freeze the pack before the reviewer receives anything.

    Everything a later reader would need to prove that the cards which were
    reviewed are the cards that were drawn, and that they were drawn by the
    classifier as it stood - and nothing they could use to work out an answer.

    The answer key appears here ONLY as a sha256. That is the point: the
    checkpoint can be pushed to a public repository and shown to anybody,
    including the reviewer, without disclosing a single disposition.

    Fails if any card is missing, if any card's bytes have moved since the
    manifest was written, or if any card still carries an unresolved tile
    failure. After this runs, no card may be redrawn, re-rendered or
    substituted.
    """
    cards_dir = outdir / "cards"
    man_path = outdir / "cards-manifest.json"
    key_path = outdir / "answer-key.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))

    missing, moved = [], []
    for code, info in man["files"].items():
        p = cards_dir / f"{code}.jpg"
        if not p.exists():
            missing.append(code)
            continue
        b = p.read_bytes()
        if len(b) != info["bytes"] or hashlib.sha256(b).hexdigest() != info["sha256"]:
            moved.append(code)
    stray = sorted(p.stem for p in cards_dir.glob("*.jpg")
                   if p.stem not in man["files"])
    holes = sorted(c for c, i in man["files"].items() if i.get("missingTiles"))

    print(f"cards in manifest: {len(man['files'])}")
    print(f"  missing from disk: {len(missing)} {missing[:10]}")
    print(f"  bytes changed since the manifest: {len(moved)} {moved[:10]}")
    print(f"  files on disk not in the manifest: {len(stray)} {stray[:10]}")
    print(f"  cards with an unresolved tile failure: {len(holes)} {holes[:10]}")
    if missing or moved or stray or holes:
        raise SystemExit(
            "STOPPED: the pack is not sealable. A card is missing, has "
            "changed, is unaccounted for, or never rendered completely. Do "
            "not hand it to a reviewer, and do not paper over it by dropping "
            "the offending cards - that is sample selection.")

    key = json.loads(key_path.read_text(encoding="utf-8"))
    n_at_grade = sum(1 for c in key["cards"] if c["disposition"] == "AT_GRADE")
    if n_at_grade != AT_GRADE_TARGET:
        raise SystemExit(
            f"STOPPED: the answer key holds {n_at_grade} AT_GRADE cards and "
            f"the predeclaration says {AT_GRADE_TARGET}.")

    # Independence is RE-VERIFIED here rather than quoted from the draw. The
    # draw's own report is the claim; this is the check on it, run against the
    # cards as they now stand and against every prior pack as it now stands.
    # `burned_points` fails closed, so reaching this line at all means every
    # prior card was locatable and mappable.
    pts, burned_pairs, prov = burned_points()
    independence = {k: v for k, v in prov.items() if k != "cellReport"}
    independence.setdefault("priorCardsExpected", sum(
        prov[k] for k in ("blind208", "holdout248", "unblinded81")))
    independence.setdefault("priorCardsLocated", prov["burnedPoints"])
    shares_pair = [c["code"] for c in key["cards"]
                   if frozenset((c["groupA"], c["groupB"])) in burned_pairs]
    nearest = {}
    too_close = []
    for c in key["cards"]:
        d = min(math.hypot(c["x"] - px, c["y"] - py) for px, py in pts)
        nearest[c["code"]] = d
        if d <= INDEPENDENCE_M:
            too_close.append((c["code"], round(d, 1)))
    closest_code = min(nearest, key=nearest.get)
    independence.update({
        "drawnCardsSharingABurnedSourceFeaturePair": len(shares_pair),
        "drawnCardsWithin50mOfABurnedPoint": len(too_close),
        "closestApproachToABurnedPointM": round(nearest[closest_code], 1),
    })
    print(f"independence re-verified against {len(pts)} burned points: "
          f"{len(shares_pair)} share a burned pair, {len(too_close)} within "
          f"{INDEPENDENCE_M:.0f} m, closest approach "
          f"{nearest[closest_code]:.1f} m")
    if shares_pair or too_close:
        raise SystemExit(
            f"STOPPED: the drawn pack is not independent of the prior packs. "
            f"sharing a burned source-feature pair: {shares_pair[:20]}; "
            f"within {INDEPENDENCE_M:.0f} m: {too_close[:20]}")

    src = REPO_ROOT / "python/src/nzcl"
    v2man = json.loads((AUDIT / "classified-v2-manifest.json")
                       .read_text(encoding="utf-8"))
    # `git -C` rather than cwd=, and stderr surfaced rather than swallowed.
    # The first version of this used cwd= and captured only stdout, so when
    # git declined the directory the field was written as an empty string and
    # the checkpoint recorded nothing while looking complete.
    import subprocess
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True)
    head = proc.stdout.strip()
    if not head:
        raise SystemExit(
            "STOPPED: could not read the git HEAD to stamp the seal "
            f"(exit {proc.returncode}): {proc.stderr.strip()!r}. A checkpoint "
            "that cannot say which commit it belongs to is not a checkpoint.")

    checkpoint = {
        "what": ("The draw is sealed. Every card below existed, in exactly "
                 "these bytes, before any reviewer saw anything. No card may "
                 "be redrawn, re-rendered or substituted after this point."),
        "sealedAtGitHead": head,
        "cards": len(man["files"]),
        "atGradeCards": n_at_grade,
        "decoyCards": len(key["cards"]) - n_at_grade,
        "unresolvedTileFailures": 0,
        "totalBytes": sum(i["bytes"] for i in man["files"].values()),
        "classifierSha256": {
            "python/src/nzcl/crossings.py": _sha256(src / "crossings.py"),
            "python/src/nzcl/topology.py": _sha256(src / "topology.py"),
            "python/src/nzcl/promotion.py": _sha256(src / "promotion.py"),
            "python/src/nzcl/holdout.py": _sha256(src / "holdout.py"),
        },
        "generatorSha256": {
            "scratch/holdout3_review.py":
                _sha256(REPO_ROOT / "scratch/holdout3_review.py"),
            "scratch/classify_national_v2.py":
                _sha256(REPO_ROOT / "scratch/classify_national_v2.py"),
            "scratch/corridor_withdrawn.py":
                _sha256(REPO_ROOT / "scratch/corridor_withdrawn.py"),
        },
        "recordSha256": {
            "classified-v2-manifest.json":
                _sha256(AUDIT / "classified-v2-manifest.json"),
            "classified-v2.jsonl (as pinned by that manifest)":
                v2man["sha256"],
            "classified-v2.jsonl (as on disk now)":
                _sha256(AUDIT / "classified-v2.jsonl")
                if (AUDIT / "classified-v2.jsonl").exists() else None,
        },
        # Counts only. Expected, located and excluded, plus the RE-VERIFIED
        # result of checking every drawn card against every prior one. It is
        # checkable from a public artefact rather than from the answer key,
        # and none of it says anything about any card's disposition.
        "independence": independence,
        "independenceRule": (
            "Every prior reviewed card must be locatable as a point and "
            "mappable to a source-feature pair, or the draw stops. "
            "priorCardsExpected must equal priorCardsLocated, and no drawn "
            "card may share a burned source-feature pair or lie within 50 m "
            "of a burned point."),
        "answerKeySha256": _sha256(key_path),
        "answerKeyNote": ("Recorded as a hash and nothing else, so this "
                          "checkpoint can be published, and shown to the "
                          "reviewer if they ask what they are being given, "
                          "without disclosing one disposition."),
        "cardSha256": {c: i["sha256"] for c, i in sorted(man["files"].items())},
    }
    out = AUDIT / "holdout3-seal.json"
    out.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    print(f"\nsealed. wrote {out}")
    print(f"  answer key sha256 {checkpoint['answerKeySha256']}")
    print("  push this checkpoint and confirm the remote matches BEFORE the "
          "reviewer receives a card.")
    return 0


def score(outdir: Path, verdicts_path: Path, strict: bool = True) -> int:
    """Score a completed review, and refuse to score an incomplete one.

    The collation rules, the completeness checks and the pack assertions live
    in `nzcl.holdout`, under test in `tests/test_holdout.py`, rather than
    here. The check that stops a short review from passing is not a
    scratch-script concern, and a guard with no test is a guard nobody has
    shown to work.
    """
    from nzcl import holdout, promotion

    key = json.loads((outdir / "answer-key.json").read_text(encoding="utf-8"))
    verdicts = _load_verdicts(verdicts_path)

    try:
        collated = holdout.collate(key["cards"], verdicts, strict=strict)
    except holdout.ReviewNotComplete as e:
        print("REFUSED TO SCORE.\n")
        print(f"  {e}\n")
        print("  No promotion verdict has been computed, and nothing has been "
              "written. A card with no\n"
              "  verdict is not a pass: the predeclaration counts every "
              "unreviewable card as a failure,\n"
              "  and a card that was never answered is at best the same "
              "thing. Obtain the missing\n"
              "  verdicts, or re-run with `--materialise-missing`, which "
              "records each one as `unclear`\n"
              "  and therefore as a FAILURE, and names every card it had to "
              "assume.")
        return 2

    rows = collated.rows
    if collated.materialised:
        print(f"MATERIALISED {len(collated.materialised)} missing, duplicated "
              f"or unparseable verdict(s) as `unclear`, counted as failures:")
        print("  " + ", ".join(collated.materialised) + "\n")
    print(f"scored {len(rows)} of {len(key['cards'])} cards")

    # Reviewer attribution, if the verdicts arrived as per-reviewer files
    # beside the combined one. Recorded so that a reviewer whose rates diverge
    # from the others is visible rather than averaged away - four agents
    # marking cards is four instruments, and an instrument that reads
    # differently is a fact about the measurement.
    by_reviewer_code: dict[str, str] = {}
    for p in sorted(verdicts_path.parent.glob("verdicts-*.txt")):
        who = p.stem.split("-", 1)[1]
        if who.upper() == "ALL" or p == verdicts_path:
            continue
        for v in _load_verdicts(p):
            by_reviewer_code[str(v.get("code", "")).strip()] = who
    for r in collated.rows:
        r["reviewer"] = by_reviewer_code.get(r["code"])

    ag = collated.of_disposition("AT_GRADE")
    counts = collated.counts("AT_GRADE")
    conf = counts["confirmed"]
    contra = counts["contradicted"]
    unrev = counts["unreviewable"]
    gs_fp = counts["grade_separated_false_positives"]
    nj_fp = counts["not_a_junction_false_positives"]
    # The denominator is the pack's, not the verdict file's. If this ever
    # fails, the bound below would be computed over a sample that is not the
    # one that was declared.
    assert conf + contra + unrev == len(ag) == holdout.DECLARED_AT_GRADE_N, (
        f"AT_GRADE denominator is {conf + contra + unrev} over {len(ag)} "
        f"cards; the declaration says {holdout.DECLARED_AT_GRADE_N}")

    gate = promotion.evaluate(**counts)
    print("\n=== PROMOTION GATE ===")
    for c in gate.conditions:
        print(f"  [{'MET' if c.met else 'NOT MET'}] {c.name}: "
              f"{c.requirement}, observed {c.observed}")
    print(f"  overall: {'MET' if gate.met else 'NOT MET'}")
    print(f"  {gate.detail}")

    out = {"pack": "holdout3", "seed": SEED, "snapshot": SNAP,
           "cardsScored": len(rows),
           "materialisedAsUnclear": list(collated.materialised),
           "howIncompleteInputWasHandled": (
               "STRICT: every card had exactly one valid verdict."
               if strict and not collated.materialised else
               "MATERIALISED: missing, duplicated and unparseable verdicts "
               "were recorded as `unclear` and counted as failures. They are "
               "listed in materialisedAsUnclear. The denominator is the "
               "pack's 350 AT_GRADE cards either way."),
           "readingTheNumbers": (
               "Performance on a deliberately difficult, stratified holdout. "
               "This is not a probability sample and is not an estimate or "
               "formal lower bound on national precision. The Wilson bound "
               "applies to this holdout's reviewed cases, not to a nationally "
               "weighted population."),
           "atGrade": {"n": len(ag), "confirmed": conf,
                       "contradicted": contra, "unreviewable": unrev,
                       "gradeSeparatedFalsePositives": gs_fp,
                       "notAJunctionFalsePositives": nj_fp},
           "promotionGate": gate.as_dict(),
           "byCell": {}, "byRule": {}, "decoys": {}, "decoysByReason": {},
           "byReviewer": {}, "unclearCards": [], "contradictions": [],
           "verdicts": rows}

    # Every card the reviewer could not read, with what it was. Five or more
    # of these landing on AT_GRADE fails the gate on their own at n=350.
    for r in sorted(rows, key=lambda r: r["code"]):
        if r["verdict"] == "unclear":
            out["unclearCards"].append(
                {"code": r["code"], "reviewer": r.get("reviewer"),
                 "disposition": r["disposition"], "reason": r["reason"],
                 "cell": r["cell"], "angleDeg": r.get("angleDeg"),
                 "note": r.get("note", "")})

    # Every contradiction, individually, both directions. An AT_GRADE card
    # the reviewer contradicts is a node the graph would gain and should not;
    # a decoy they contradict is connectivity the graph loses and should not.
    for r in sorted(rows, key=lambda r: r["code"]):
        if r["verdict"] != "unclear" and r["verdict"] not in ACCEPTS[r["disposition"]]:
            out["contradictions"].append(
                {"code": r["code"], "reviewer": r.get("reviewer"),
                 "classifierSaid": f"{r['disposition']}/{r['reason']}",
                 "reviewerSaid": r["verdict"],
                 "confidence": r.get("confidence"),
                 "cell": r["cell"], "qualifyingCells": r.get("qualifyingCells"),
                 "angleDeg": r.get("angleDeg"),
                 "nameA": r.get("nameA"), "nameB": r.get("nameB"),
                 "groupA": r.get("groupA"), "groupB": r.get("groupB"),
                 "x": r.get("x"), "y": r.get("y"),
                 "rcaNameA": r.get("rcaNameA"), "rcaNameB": r.get("rcaNameB"),
                 "structDistM": r.get("structDistM"),
                 "imageryYear": r.get("imageryYear"),
                 "note": r.get("note", "")})

    # Per reviewer. Four agents are four instruments; one that reads
    # differently from the others is a fact about the measurement, and
    # averaging it away would hide it. Split by AT_GRADE versus decoy,
    # because a reviewer who says "not a junction" more often is calibrated
    # differently if it lands on decoys and disagreeing if it lands on n.
    for who in sorted({r.get("reviewer") for r in rows if r.get("reviewer")}):
        sub = [r for r in rows if r.get("reviewer") == who]
        sub_ag = [r for r in sub if r["disposition"] == "AT_GRADE"]
        sub_de = [r for r in sub if r["disposition"] != "AT_GRADE"]
        out["byReviewer"][who] = {
            "cards": len(sub),
            "labels": dict(collections.Counter(r["verdict"] for r in sub)),
            "atGrade": {
                "n": len(sub_ag),
                "confirmed": sum(1 for r in sub_ag
                                 if r["verdict"] in ACCEPTS["AT_GRADE"]),
                "contradicted": sum(1 for r in sub_ag
                                    if r["verdict"] not in ACCEPTS["AT_GRADE"]
                                    and r["verdict"] != "unclear"),
                "unreviewable": sum(1 for r in sub_ag
                                    if r["verdict"] == "unclear"),
                "saidNotAJunction": sum(1 for r in sub_ag
                                        if r["verdict"] == "not_a_junction"),
                "saidGradeSeparated": sum(1 for r in sub_ag
                                          if r["verdict"] == "grade_separated"),
            },
            "decoys": {
                "n": len(sub_de),
                "confirmed": sum(1 for r in sub_de
                                 if r["verdict"] in ACCEPTS[r["disposition"]]),
                "unreviewable": sum(1 for r in sub_de
                                    if r["verdict"] == "unclear"),
                "saidNotAJunction": sum(1 for r in sub_de
                                        if r["verdict"] == "not_a_junction"),
            },
        }
    # By STRATUM MEMBERSHIP, not by which cell drew the card first. See
    # `qualifying_cells`. A card can appear in more than one row here; the
    # rows do not sum to n and are not meant to.
    for cell in sorted({c for r in ag for c in r.get("qualifyingCells", ())}
                       | {"topup"}):
        sub = [r for r in ag if cell in r.get("qualifyingCells", ())
               or (cell == "topup" and r["cell"] == "topup")]
        if not sub:
            continue
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
            "contradicted": sum(1 for r in sub
                                if r["verdict"] not in ACCEPTS[disp]
                                and r["verdict"] != "unclear"),
            "unreviewable": sum(1 for r in sub if r["verdict"] == "unclear")}
    # Decoys by deciding RULE as well as by disposition. UNRESOLVED accepts
    # every verdict but "unclear" by construction, so its confirmation rate is
    # not a meaningful number - what IS meaningful is what the reviewer
    # actually called each rule's crossings, so the labels are reported raw.
    for disp, reason in sorted({(r["disposition"], r["reason"]) for r in rows
                                if r["disposition"] != "AT_GRADE"}):
        sub = [r for r in rows if r["disposition"] == disp
               and r["reason"] == reason]
        out["decoysByReason"][f"{disp}/{reason}"] = {
            "n": len(sub),
            "labels": dict(collections.Counter(r["verdict"] for r in sub)),
            "confirmed": sum(1 for r in sub if r["verdict"] in ACCEPTS[disp]),
            "reviewerCalledItAtGrade": sum(1 for r in sub
                                           if r["verdict"] == "at_grade"),
            "codes": sorted(r["code"] for r in sub),
        }
    print("\n=== decoys, by deciding rule ===")
    for k, v in out["decoysByReason"].items():
        print(f"  {k:<38} n={v['n']:>3}  reviewer said at-grade: "
              f"{v['reviewerCalledItAtGrade']:>3}  labels {v['labels']}")
    print("\n=== per reviewer ===")
    for who, v in out["byReviewer"].items():
        a = v["atGrade"]
        print(f"  {who}: {v['cards']} cards, labels {v['labels']}")
        print(f"      AT_GRADE n={a['n']:>3} confirmed={a['confirmed']:>3} "
              f"contradicted={a['contradicted']:>2} unclear={a['unreviewable']} "
              f"(said not-a-junction {a['saidNotAJunction']}, "
              f"grade-separated {a['saidGradeSeparated']})")
        print(f"      decoys   n={v['decoys']['n']:>3} "
              f"confirmed={v['decoys']['confirmed']:>3} "
              f"said not-a-junction {v['decoys']['saidNotAJunction']}")
    print(f"\n=== unreadable cards: {len(out['unclearCards'])} "
          f"({sum(1 for c in out['unclearCards'] if c['disposition'] == 'AT_GRADE')} "
          f"of them AT_GRADE) ===")
    for c in out["unclearCards"]:
        print(f"  {c['code']} [{c['reviewer']}] {c['disposition']}/{c['reason']}")
    print(f"\n=== contradictions: {len(out['contradictions'])} ===")
    for c in out["contradictions"]:
        print(f"  {c['code']} [{c['reviewer']}] {c['classifierSaid']} "
              f"-> reviewer said {c['reviewerSaid']}  "
              f"({c['nameA']} x {c['nameB']}, {c['angleDeg']} deg)")

    (outdir / "holdout3-result.json").write_text(json.dumps(out, indent=2) + "\n",
                                                 encoding="utf-8")
    print(f"\nwrote {outdir/'holdout3-result.json'}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        raise SystemExit(plan())
    if cmd == "build":
        raise SystemExit(build(Path(sys.argv[2])))
    if cmd == "seal":
        raise SystemExit(seal(Path(sys.argv[2])))
    if cmd == "score":
        raise SystemExit(score(Path(sys.argv[2]), Path(sys.argv[3]),
                               strict="--materialise-missing" not in sys.argv))
    raise SystemExit("plan | build <outdir> | seal <outdir> | "
                     "score <outdir> <verdicts> [--materialise-missing]")
