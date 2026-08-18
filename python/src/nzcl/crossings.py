"""Where two roads cross without either one ending: junction, or structure?

The problem
-----------
`topology.split_at_junctions` used to answer this with one inference:

    NEVER split where two links' INTERIORS cross.
    Neither road ends there. That is an overbridge, a tunnel, or a
    grade-separated interchange.

The inference does not hold. On a flat rural grid a through road crosses
another through road at grade and neither terminates. Near Darfield,
Canterbury, Clintons Road and McLaughlins Road cross at 0.000 m separation with
disjoint node sets; treating that as a flyover made the replacement path for a
675 m closure 3.0 km longer than it should be. The full counterfactual is in
`docs/audits/at-grade-crossings/`.

What replaced it
----------------
Three dispositions, decided on evidence:

    AT_GRADE          create a shared node
    GRADE_SEPARATED   leave disconnected
    UNRESOLVED        leave disconnected, flag it, and lower the confidence of
                      any route that a different answer here would change

The third is the important one. It is not a failure of the classifier; it is
the classifier declining to invent a fact. Connecting everything invents
motorway turns. Connecting nothing treats rural crossroads as flyovers.
Saying "I do not know, and here is what it would cost you if I am wrong" is the
only one of the three that is honest at national scale.

What evidence is actually available - and what is not
-----------------------------------------------------
Measured on `amds-national-2026-07-28-5b359d84`, 13,056 point-like crossing
pairs. See `docs/audits/at-grade-crossings/evidence.md`.

  z-values           PUBLISHED BUT USELESS. AMDS layer 1 has hasZ=true and the
                     ingest simply never asked for it. Asking gets real
                     elevations - but they are a TERRAIN DRAPE. Known motorway
                     interchange crossings, which are grade separated by
                     construction, come back 0.00-0.09 m apart in z. A
                     digitising-density artefact on a hillside produces ten
                     times that. z cannot classify anything here, and the
                     ingest is left 2D deliberately rather than by omission.

  modelAssetType     NO structure value exists. The domain is Roadway,
                     Pathway-Unformed, Pathway-Formed, Railway, Waterway,
                     Connector, Railway-Yard Track, Railway-Crossover.
                     `Connector` is useful: it marks ramps and link roads.

  height limits      STRONG, RARE. A height restriction on a link means
                     something passes over it. 115 crossing pairs nationally.

  ramp context       STRONG. Motorway access is controlled; a crossing on a
                     motorway carriageway or a ramp that carries no node is a
                     structure, not an unnoded junction.

  crossing angle     USEFUL AS A VETO. 806 pairs cross at under 10 degrees.
                     Those are parallel carriageways grazing each other, not
                     junctions; noding them would fabricate a turn.

  a node within 1 m  POSITIVE AT-GRADE EVIDENCE. A third link ENDS at the
                     crossing point. The source data already treats that spot
                     as a junction; the two through roads were simply never
                     split there.

  state highway      NOT A CLASSIFIER, and never used as one here. Many state
                     highway crossings are ordinary at-grade intersections and
                     some local roads pass over others. It is carried for
                     PRIORITISATION only.

  digitised vertex   MEASURED, AND REJECTED. Only 15.4% of crossings have a
                     vertex on both lines, and the proved Darfield case has a
                     vertex on neither (1.51 m and 0.10 m away). It does not
                     separate the classes.

Everything here is a pure function of source-link attributes and geometry, so
it can run inside the ingest, before any graph exists, and be tested without a
database.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

Coord = tuple[float, float]

AT_GRADE = "AT_GRADE"
GRADE_SEPARATED = "GRADE_SEPARATED"
UNRESOLVED = "UNRESOLVED"

#: Below this the two lines graze rather than cross. Noding a 5-degree
#: "crossing" between two carriageways of the same road fabricates a turn that
#: does not exist on the ground.
TANGENTIAL_ANGLE_DEG = 20.0

#: A crossing this far from a genuine junction still counts as being at one.
#: A third road ending within a metre of the crossing means the source data
#: already calls that spot a junction.
JUNCTION_WITNESS_M = 1.0

#: Motorway / ramp context is looked for within this radius.
STRUCTURE_CONTEXT_M = 300.0

#: `modelAssetType` values. Only 1 and 6 matter here.
MAT_ROADWAY = 1
MAT_CONNECTOR = 6

#: `oneway` values: 1 = one way, 2 = both directions.
ONEWAY_ONE = 1
ONEWAY_BOTH = 2

#: Names that assert a structure outright.
_STRUCTURE_WORDS = ("interchange", "overbridge", "over bridge", "flyover",
                    "fly over", "underpass", "viaduct", "off ramp", "on ramp",
                    "offramp", "onramp")


@dataclass(frozen=True)
class CrossingContext:
    """Everything the classifier is allowed to look at, for one crossing."""

    #: Degrees, folded to 0..90. 90 is a square crossroads.
    angle_deg: float

    #: Per-side attributes, in the order (a, b).
    model_asset_type: tuple[int | None, int | None]
    oneway: tuple[int | None, int | None]
    rca_code: tuple[int | None, int | None]
    is_ramp: tuple[bool, bool]
    road_name: tuple[str | None, str | None]
    quality_flags: tuple[Sequence[str], Sequence[str]]

    #: Does a THIRD link end within `JUNCTION_WITNESS_M` of the crossing?
    junction_witness: bool

    #: One-way state-highway carriageways within `STRUCTURE_CONTEXT_M`.
    motorway_links_near: int
    #: Ramp links within `STRUCTURE_CONTEXT_M`.
    ramp_links_near: int

    #: True when the two links come from the same AMDS source feature - a road
    #: crossing itself. A different question, and not one this answers.
    same_source_feature: bool = False


@dataclass
class Classification:
    disposition: str
    #: The single rule that decided it. Machine-readable, stable.
    reason: str
    #: Human sentence, for the audit trail and the UI.
    detail: str
    #: Every rule that fired, decisive or not.
    evidence: list[str] = field(default_factory=list)


def _has_height_limit(flags: Iterable[str]) -> bool:
    return any(str(f).startswith("HEIGHT_LIMIT") for f in flags or ())


def _named_structure(name: str | None) -> bool:
    if not name:
        return False
    low = name.casefold()
    return any(w in low for w in _STRUCTURE_WORDS)


def classify(ctx: CrossingContext) -> Classification:
    """Decide one crossing. Pure; no database, no network, no globals.

    Rule order is deliberate. Evidence that a STRUCTURE exists beats evidence
    that a junction exists, because the cost of the two mistakes is not
    symmetric: inventing a motorway turn produces a confident wrong answer,
    while missing a rural crossroads produces an answer this system now marks
    as topology-sensitive rather than presenting as fact.
    """
    ev: list[str] = []

    mat_a, mat_b = ctx.model_asset_type
    ow_a, ow_b = ctx.oneway
    rca_a, rca_b = ctx.rca_code
    ramp_a, ramp_b = ctx.is_ramp
    name_a, name_b = ctx.road_name
    flags_a, flags_b = ctx.quality_flags

    # --- things that are not a road-to-road crossing at all ---------------
    if ctx.same_source_feature:
        return Classification(
            UNRESOLVED, "SAME_SOURCE_FEATURE",
            "Both sides come from one AMDS source feature: a road crossing "
            "itself, not two roads meeting.", ev)

    if ctx.angle_deg < TANGENTIAL_ANGLE_DEG:
        return Classification(
            UNRESOLVED, "TANGENTIAL",
            f"The two centrelines meet at {ctx.angle_deg:.0f} degrees. Below "
            f"{TANGENTIAL_ANGLE_DEG:.0f} they graze rather than cross, which "
            f"is the signature of two carriageways of one road, not a "
            f"junction. Noding it would fabricate a turn.", ev)

    # --- positive evidence of a structure ---------------------------------
    if _has_height_limit(flags_a) or _has_height_limit(flags_b):
        ev.append("HEIGHT_LIMIT")
        return Classification(
            GRADE_SEPARATED, "HEIGHT_LIMIT",
            "A height restriction is recorded on one of these links. A height "
            "limit exists because something passes over it.", ev)

    if _named_structure(name_a) or _named_structure(name_b):
        ev.append("NAMED_STRUCTURE")
        return Classification(
            GRADE_SEPARATED, "NAMED_STRUCTURE",
            "One of these roads is named as a structure or an interchange.", ev)

    if ramp_a or ramp_b:
        ev.append("RAMP")
        return Classification(
            GRADE_SEPARATED, "RAMP",
            "One side is a ramp. Ramps exist to separate movements that do "
            "not meet at grade.", ev)

    if mat_a == MAT_CONNECTOR or mat_b == MAT_CONNECTOR:
        ev.append("CONNECTOR")
        return Classification(
            GRADE_SEPARATED, "CONNECTOR",
            "One side is a Connector in the AMDS model asset type, which is "
            "how ramps and interchange link roads are recorded.", ev)

    motorway_side = ((rca_a == 1 and ow_a == ONEWAY_ONE)
                     or (rca_b == 1 and ow_b == ONEWAY_ONE))
    if motorway_side:
        ev.append("MOTORWAY_CARRIAGEWAY")
        return Classification(
            GRADE_SEPARATED, "MOTORWAY_CARRIAGEWAY",
            "One side is a one-way state-highway carriageway. Access to a "
            "divided state highway is controlled, so a crossing that carries "
            "no junction is a structure. Note this is not the rule 'it is a "
            "state highway' - an ordinary two-way state highway is not "
            "treated as separated by this.", ev)

    if ctx.ramp_links_near > 0:
        ev.append("RAMP_CONTEXT")
        return Classification(
            GRADE_SEPARATED, "RAMP_CONTEXT",
            f"{ctx.ramp_links_near} ramp link(s) lie within "
            f"{STRUCTURE_CONTEXT_M:.0f} m. This crossing is inside an "
            f"interchange.", ev)

    if ctx.motorway_links_near > 0:
        ev.append("MOTORWAY_CONTEXT")
        return Classification(
            UNRESOLVED, "MOTORWAY_CONTEXT",
            f"{ctx.motorway_links_near} one-way state-highway carriageway "
            f"link(s) lie within {STRUCTURE_CONTEXT_M:.0f} m. Neither of "
            f"these two roads is one, so this may be an ordinary street "
            f"crossing beside a motorway or a service road under it. Not "
            f"resolved either way.", ev)

    # --- positive evidence of a junction ----------------------------------
    ordinary = (mat_a == MAT_ROADWAY and mat_b == MAT_ROADWAY
                and ow_a == ONEWAY_BOTH and ow_b == ONEWAY_BOTH)

    if ctx.junction_witness and ordinary:
        ev.append("JUNCTION_WITNESS")
        return Classification(
            AT_GRADE, "JUNCTION_WITNESS",
            f"A third link ends within {JUNCTION_WITNESS_M:.0f} m of this "
            f"point, so the source data already treats it as a junction. "
            f"These two roads simply were not split there.", ev)

    if ordinary:
        ev.append("ORDINARY_CROSSROADS")
        return Classification(
            AT_GRADE, "ORDINARY_CROSSROADS",
            f"Two two-way roadways cross at {ctx.angle_deg:.0f} degrees with "
            f"no ramp, connector, motorway carriageway, height restriction or "
            f"structure name anywhere near. Nothing in the source describes a "
            f"structure here.", ev)

    if not ordinary:
        ev.append("NOT_ORDINARY_ROADWAY")
    return Classification(
        UNRESOLVED, "NO_EVIDENCE_EITHER_WAY",
        "Neither a structure nor a junction is evidenced here. One or both "
        "sides is one-way or is not an ordinary roadway, and nothing else "
        "settles it.", ev)


# ---------------------------------------------------------------------------
# Detection over source geometry. Pure shapely; runs before any graph exists.
# ---------------------------------------------------------------------------

@dataclass
class DetectedCrossing:
    """One point at which two source links cross without either one ending."""
    index_a: int
    index_b: int
    amds_a: str
    amds_b: str
    x: float
    y: float
    #: Distance along each line, in metres.
    along_a: float
    along_b: float
    angle_deg: float
    classification: Classification | None = None

    @property
    def disposition(self) -> str:
        return self.classification.disposition if self.classification else UNRESOLVED


def _angle_at(line: LineString, along: float, window_m: float = 10.0) -> float:
    """Bearing of `line` over a short window centred on `along`."""
    lo = max(0.0, along - window_m)
    hi = min(line.length, along + window_m)
    if hi - lo <= 0:
        return 0.0
    p0 = line.interpolate(lo)
    p1 = line.interpolate(hi)
    return math.atan2(p1.y - p0.y, p1.x - p0.x)


def crossing_angle_deg(line_a: LineString, along_a: float,
                       line_b: LineString, along_b: float) -> float:
    """Angle between two lines at a crossing, folded to 0..90 degrees.

    Folded because a crossing has no direction: two roads meeting at 91 degrees
    and at 89 degrees are the same junction.
    """
    d = _angle_at(line_a, along_a) - _angle_at(line_b, along_b)
    deg = math.degrees(d) % 180.0
    return 180.0 - deg if deg > 90.0 else deg


def detect(geoms: Sequence[LineString], amds_ids: Sequence[str], *,
           end_guard_m: float = 0.05) -> list[DetectedCrossing]:
    """Every interior-to-interior crossing among `geoms`.

    A crossing where either line ENDS is excluded: that is an endpoint
    junction, and `split_at_junctions` already handles it. A collinear overlap
    is excluded too - two records describing the same stretch of road is a
    duplicate-geometry problem, not a junction.
    """
    tree = STRtree(list(geoms))
    out: list[DetectedCrossing] = []
    seen: set[tuple[int, int]] = set()

    for i, a in enumerate(geoms):
        for j in tree.query(a):
            j = int(j)
            if j <= i:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            b = geoms[j]
            if not a.intersects(b):
                continue
            inter = a.intersection(b)
            if inter.is_empty or inter.geom_type not in ("Point", "MultiPoint"):
                # LineString / MultiLineString: collinear overlap, not a
                # crossing. GeometryCollection: mixed; take only its points.
                if inter.geom_type != "GeometryCollection":
                    continue
                pts = [g for g in inter.geoms if g.geom_type == "Point"]
            else:
                pts = [inter] if inter.geom_type == "Point" else list(inter.geoms)

            for p in pts:
                along_a = a.project(p)
                along_b = b.project(p)
                if (along_a <= end_guard_m or along_a >= a.length - end_guard_m
                        or along_b <= end_guard_m
                        or along_b >= b.length - end_guard_m):
                    continue  # an endpoint junction, not an interior crossing
                out.append(DetectedCrossing(
                    index_a=i, index_b=j,
                    amds_a=amds_ids[i], amds_b=amds_ids[j],
                    x=float(p.x), y=float(p.y),
                    along_a=along_a, along_b=along_b,
                    angle_deg=crossing_angle_deg(a, along_a, b, along_b),
                ))
    return out


def cluster(points: Sequence[tuple[float, float]], eps_m: float = 25.0
            ) -> list[int]:
    """Group crossing points into unique PLACES.

    A crossing PAIR is not a crossing PLACE. One physical intersection of two
    divided carriageways produces four pairs and four points; a road crossing a
    dual carriageway produces two. Reporting the pair count as though it were a
    count of places overstates the problem, which is exactly what a previous
    investigation did.

    Single-link DBSCAN with `minpoints=1`, so every point lands in a cluster
    and nothing is discarded as noise.
    """
    n = len(points)
    parent = list(range(n))

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    # Grid the points so this stays near-linear instead of quadratic.
    cell = eps_m
    grid: dict[tuple[int, int], list[int]] = {}
    for idx, (x, y) in enumerate(points):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(idx)

    eps2 = eps_m * eps_m
    for (cx, cy), members in grid.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid.get((cx + dx, cy + dy), ()):
                    for m in members:
                        if other <= m:
                            continue
                        x1, y1 = points[m]
                        x2, y2 = points[other]
                        if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= eps2:
                            ra, rb = find(m), find(other)
                            if ra != rb:
                                parent[ra] = rb

    labels: dict[int, int] = {}
    out = [0] * n
    for i in range(n):
        r = find(i)
        if r not in labels:
            labels[r] = len(labels)
        out[i] = labels[r]
    return out


def build_context(crossing: DetectedCrossing,
                  geoms: Sequence[LineString],
                  attrs: Sequence[dict],
                  *,
                  endpoint_tree: STRtree,
                  endpoint_owner: Sequence[int],
                  motorway_tree: STRtree | None = None,
                  ramp_tree: STRtree | None = None) -> CrossingContext:
    """Assemble the classifier's inputs for one detected crossing."""
    i, j = crossing.index_a, crossing.index_b
    a_at, b_at = attrs[i], attrs[j]
    p = Point(crossing.x, crossing.y)

    witness = False
    for k in endpoint_tree.query(p.buffer(JUNCTION_WITNESS_M)):
        owner = endpoint_owner[int(k)]
        if owner in (i, j):
            continue
        if endpoint_tree.geometries[int(k)].distance(p) <= JUNCTION_WITNESS_M:
            witness = True
            break

    def near(tree: STRtree | None) -> int:
        if tree is None:
            return 0
        buf = p.buffer(STRUCTURE_CONTEXT_M)
        return sum(1 for k in tree.query(buf)
                   if tree.geometries[int(k)].distance(p) <= STRUCTURE_CONTEXT_M)

    return CrossingContext(
        angle_deg=crossing.angle_deg,
        model_asset_type=(a_at.get("model_asset_type"), b_at.get("model_asset_type")),
        oneway=(a_at.get("oneway"), b_at.get("oneway")),
        rca_code=(a_at.get("rca_code"), b_at.get("rca_code")),
        is_ramp=(bool(a_at.get("is_ramp")), bool(b_at.get("is_ramp"))),
        road_name=(a_at.get("road_name"), b_at.get("road_name")),
        quality_flags=(a_at.get("quality_flags") or (),
                       b_at.get("quality_flags") or ()),
        junction_witness=witness,
        motorway_links_near=near(motorway_tree),
        ramp_links_near=near(ramp_tree),
        same_source_feature=crossing.amds_a == crossing.amds_b,
    )
