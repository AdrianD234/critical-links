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

  Topo50 bridges     THE BEST AVAILABLE, and the only AUTHORITATIVE evidence
  and tunnels        of a structure anywhere in reach. LINZ layer-50244 and
                     layer-50366, 18,007 centrelines nationally. Fires on 599
                     crossings (4.6%). It CONFIRMS a structure; its absence
                     confirms nothing, because Topo50 is 1:50k cartography and
                     a short urban overbridge is generalised away. Measured
                     against grade-separation candidates on the SH1 Southern
                     Motorway it recovers about 45%, so it is never read as
                     evidence of an at-grade crossing.

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

Measured precision, and what changed because of it
---------------------------------------------------
81 crossings were drawn as a disproportionate stratified sample - every
deciding rule, including the rare ones - and judged against LINZ aerial
photography with both centrelines drawn on it. Verdicts, screenshots and
intervals are in `docs/audits/at-grade-crossings/`.

    AT_GRADE            35/35 = 100% [90-100], one further case unclear
    GRADE_SEPARATED     17/23 =  74% [54- 87]
    UNRESOLVED          16/16 = 100% [81-100], four further cases unclear

The AT_GRADE figure is the one that governs the graph, because AT_GRADE is the
only disposition that creates a node. It was measured on the rules as they
stand and NONE of those rules was changed afterwards, so it describes what
actually ships.

Three GRADE_SEPARATED rules were changed, and every change moves crossings to
UNRESOLVED - which cannot make the graph wrong, only more cautious, because
GRADE_SEPARATED and UNRESOLVED leave the crossing disconnected either way. The
whole difference between them is confidence.

    RAMP_CONTEXT    0/3   demoted to UNRESOLVED. "A ramp within 300 m" is true
                          of ordinary signalised corners all over Auckland.
    HEIGHT_LIMIT    2/3   demoted to UNRESOLVED, on a reason rather than on
                          three samples: AMDS publishes startMeasure and
                          endMeasure with each restriction and the ingest keeps
                          neither, so a height limit cannot be placed on the
                          link it belongs to.
    NAMED_STRUCTURE 1/2   kept, with "roundabout" now vetoing the match. NZTA
                          names at-grade rural roundabouts "... Interchange 45
                          Roundabout".

MOTORWAY_CARRIAGEWAY measured 4/5 excluding one unclear case. Its single
failure was SH76 Brougham Street crossing Opawa Road, a signalised at-grade
intersection on a one-way-coded urban arterial. It is kept, and that is the
weakest surviving GRADE_SEPARATED rule.

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

#: A LINZ Topo50 bridge or tunnel centreline this close to the crossing, and
#: this closely aligned with one of the two roads, means that road is on a
#: structure. Both halves are load-bearing: within 15 m nationally there are
#: 1,056 structures, but only 599 line up with a road - the other 420 cross
#: both roads, which is what a river bridge beside a junction looks like.
STRUCTURE_MATCH_M = 15.0
STRUCTURE_ALIGN_DEG = 20.0

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

#: ...except that "interchange" is also how NZTA names AT-GRADE roundabouts on
#: rural state highways. "State Highway 5 Interchange 45 Roundabout" is a
#: roundabout, and the manual review caught the classifier calling it a
#: structure. A name that says roundabout is describing an at-grade junction,
#: whatever else it says.
_NOT_A_STRUCTURE_WORDS = ("roundabout",)


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

    #: Metres to the nearest LINZ Topo50 bridge or tunnel centreline, and the
    #: angle between that centreline and whichever of the two roads it lines up
    #: with better. `None` when no structure layer was loaded.
    structure_dist_m: float | None = None
    structure_align_deg: float | None = None
    structure_kind: str | None = None


#: Reasons that forbid creating a shared node, under EVERY policy including
#: the POSSIBLE sensitivity graph. Two different arguments end up here:
#:
#:   TANGENTIAL, SAME_SOURCE_FEATURE
#:     These are not two roads meeting. A 4-degree graze between two
#:     carriageways of one road is not doubt about a junction, it is the
#:     absence of one, and connecting it fabricates a turn across a median
#:     rather than measuring sensitivity.
#:
#:   MIXED_PLACE
#:     These MIGHT be two roads meeting - but not in a way a plain graph node
#:     can express. See `demote_mixed_places`.
NEVER_NODE_REASONS = frozenset({"TANGENTIAL", "SAME_SOURCE_FEATURE",
                                "MIXED_PLACE"})

#: How much the classifier is claiming. Reported alongside the disposition,
#: because two AT_GRADE verdicts are not equally well founded.
#:
#:   HIGH    positive evidence that this specific point is what it is called.
#:   MEDIUM  a defensible reading with no contrary evidence - which is not the
#:           same thing as evidence FOR the conclusion.
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"

#: Every AT_GRADE rule and what it is actually entitled to claim.
#:
#: JUNCTION_WITNESS is HIGH: a third link ENDS at the crossing, so the source
#: data itself already calls that point a junction. That is positive evidence.
#:
#: ORDINARY_CROSSROADS is MEDIUM and must be described as probable, not
#: established. Its rule is "two two-way Roadway features crossing at 20
#: degrees or more with no contrary structure, ramp or motorway evidence" -
#: absence-of-evidence reasoning. It is the right default for a flat rural grid
#: and it is what the Darfield case needed, but it is not proof, and the
#: language around it must not imply otherwise. It earns HIGH only when backed
#: by a junction witness, by authoritative road-section segmentation, or by an
#: evidence-backed override.
_AT_GRADE_CONFIDENCE = {
    "JUNCTION_WITNESS": CONFIDENCE_HIGH,
    "ORDINARY_CROSSROADS": CONFIDENCE_MEDIUM,
}


@dataclass
class Classification:
    disposition: str
    #: The single rule that decided it. Machine-readable, stable.
    reason: str
    #: Human sentence, for the audit trail and the UI.
    detail: str
    #: Every rule that fired, decisive or not.
    evidence: list[str] = field(default_factory=list)
    #: HIGH or MEDIUM. How much this verdict is entitled to claim.
    confidence: str = CONFIDENCE_MEDIUM

    @property
    def safe_to_node(self) -> bool:
        """May a shared graph node be created here, under ANY policy?

        Distinct from the disposition. The disposition says what the evidence
        supports; this says whether acting on it is representable. A mixed
        place is UNRESOLVED not because the evidence is weak but because a
        plain node would express a movement that does not exist.
        """
        return self.reason not in NEVER_NODE_REASONS


def _has_height_limit(flags: Iterable[str]) -> bool:
    return any(str(f).startswith("HEIGHT_LIMIT") for f in flags or ())


def _named_structure(name: str | None) -> bool:
    if not name:
        return False
    low = name.casefold()
    if any(w in low for w in _NOT_A_STRUCTURE_WORDS):
        return False
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
    if (ctx.structure_dist_m is not None
            and ctx.structure_dist_m <= STRUCTURE_MATCH_M
            and ctx.structure_align_deg is not None
            and ctx.structure_align_deg <= STRUCTURE_ALIGN_DEG):
        ev.append("STRUCTURE_MAPPED")
        return Classification(
            GRADE_SEPARATED, "STRUCTURE_MAPPED",
            f"LINZ Topo50 maps a {ctx.structure_kind or 'structure'} centreline "
            f"{ctx.structure_dist_m:.0f} m away, running within "
            f"{ctx.structure_align_deg:.0f} degrees of one of these two roads. "
            f"That road is on a structure here. Alignment is required: a "
            f"centreline that crosses BOTH roads is a river bridge that happens "
            f"to be nearby, and 420 of the 1,056 structures within "
            f"{STRUCTURE_MATCH_M:.0f} m nationally are exactly that.", ev)

    if _named_structure(name_a) or _named_structure(name_b):
        ev.append("NAMED_STRUCTURE")
        return Classification(
            GRADE_SEPARATED, "NAMED_STRUCTURE",
            "One of these roads is named as a structure or a ramp.", ev)

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

    if _has_height_limit(flags_a) or _has_height_limit(flags_b):
        ev.append("HEIGHT_LIMIT")
        return Classification(
            UNRESOLVED, "HEIGHT_LIMIT",
            "A height restriction is recorded on one of these links, which "
            "usually means something passes over it - but the ingest stores "
            "the restriction against the WHOLE source link, discarding the "
            "startMeasure and endMeasure AMDS publishes with it. So the limit "
            "may belong to a structure somewhere else on the same road. "
            "Reviewed at 2 of 3 in the manual sample, and one of those failures "
            "was exactly this: a rail overbridge on Railway Road being read as "
            "a structure at a plain T-junction 400 m away.", ev)

    if ctx.ramp_links_near > 0:
        ev.append("RAMP_CONTEXT")
        return Classification(
            UNRESOLVED, "RAMP_CONTEXT",
            f"{ctx.ramp_links_near} ramp link(s) lie within "
            f"{STRUCTURE_CONTEXT_M:.0f} m. In a city that is true of ordinary "
            f"signalised corners near a motorway, and the manual review found "
            f"it 0 correct out of 3 - Wakefield x Symonds, Khyber Pass x "
            f"Nugent and a suburban cul-de-sac were all called structures and "
            f"all meet at grade. Proximity to a ramp is not a structure.", ev)

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
            f"These two roads simply were not split there.", ev,
            confidence=_AT_GRADE_CONFIDENCE["JUNCTION_WITNESS"])

    if ordinary:
        ev.append("ORDINARY_CROSSROADS")
        return Classification(
            AT_GRADE, "ORDINARY_CROSSROADS",
            f"PROBABLY a junction. Two two-way roadways cross at "
            f"{ctx.angle_deg:.0f} degrees with no ramp, connector, motorway "
            f"carriageway or structure name anywhere near, so nothing in the "
            f"source describes a structure here - but nothing describes a "
            f"junction either. This is the absence of contrary evidence, not "
            f"evidence for the conclusion, and it is recorded at MEDIUM "
            f"confidence for that reason.", ev,
            confidence=_AT_GRADE_CONFIDENCE["ORDINARY_CROSSROADS"])

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
    #: What the evidence said BEFORE the mixed-place rule withdrew it, if it
    #: did. Kept because "this looked at grade and we declined to act on it"
    #: is a different fact from "we had no idea", and the audit needs both.
    classification_before_place_rule: Classification | None = None

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


#: A place is the set of crossing points within this distance of one another.
#: Wide enough to hold one physical intersection of two divided carriageways
#: (four points, about 20-30 m across) and narrower than an urban block.
PLACE_EPS_M = 25.0


def demote_mixed_places(found: Sequence[DetectedCrossing],
                        eps_m: float = PLACE_EPS_M) -> tuple[list[int], int]:
    """Refuse to node anything at a place whose crossings disagree.

    WHY THIS EXISTS, AND WHY IT IS NOT OPTIONAL

    A graph node is a promise that every arc arriving may leave by every other
    arc. It has no way to say "A may turn into B here, but C passes overhead".

    At a complex interchange that distinction is the whole point. One pair of
    roads meets at grade while another pair, at essentially the same place,
    passes above or below. If the at-grade pair is cut and the pieces collapse
    onto one coordinate that a third road also touches, the node hands that
    third road every movement too - and the graph now contains a turn onto a
    motorway that exists nowhere on the ground.

    That is precisely the failure the original never-node rule was protecting
    against. Fixing the rural-crossroads defect by introducing it would be no
    improvement at all: it would move the error from "a detour is 3 km too
    long" to "the engine routed through an impossible turn", and the second is
    worse because it looks fine.

    So where the pairs at one place do not agree, NOTHING at that place is
    noded, under any policy including POSSIBLE. The crossings stay recorded,
    marked UNRESOLVED with reason MIXED_PLACE, and the doubt reaches the answer
    instead of being resolved by a coincidence of coordinates.

    This is the conservative option of the three available. The other two -
    level-specific nodes, or expressing permitted movements through the
    edge-expanded transition graph - are better answers and are not attempted
    here; a mixed place is 2.3% of places nationally, and the right way to
    spend that is on not being wrong.

    Returns (place labels, number of crossings demoted).
    """
    if not found:
        return [], 0
    labels = cluster([(x.x, x.y) for x in found], eps_m=eps_m)

    by_place: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        by_place.setdefault(lab, []).append(idx)

    demoted = 0
    for members in by_place.values():
        dispositions = {found[i].disposition for i in members}
        if len(dispositions) <= 1:
            continue
        for i in members:
            c = found[i].classification
            if c is None or c.reason == "MIXED_PLACE":
                continue
            found[i].classification_before_place_rule = c
            found[i].classification = Classification(
                UNRESOLVED, "MIXED_PLACE",
                f"Crossings within {eps_m:.0f} m of this point disagree "
                f"({', '.join(sorted(dispositions))}), so this is an "
                f"interchange where some pairs meet and others pass over. A "
                f"single graph node cannot express that: it would grant every "
                f"road here every movement, inventing the impossible turn the "
                f"never-node rule existed to prevent. Nothing at this place is "
                f"noded. Originally {c.disposition} ({c.reason}).",
                list(c.evidence) + [f"WAS_{c.disposition}_{c.reason}"],
                confidence=CONFIDENCE_MEDIUM)
            demoted += 1
    return labels, demoted


def structure_evidence(p: Point, line_a: LineString, along_a: float,
                       line_b: LineString, along_b: float,
                       structures: STRtree | None,
                       kinds: Sequence[str] | None = None,
                       ) -> tuple[float | None, float | None, str | None]:
    """Nearest mapped structure, and how well it lines up with either road.

    Alignment is required, not optional. Of the 1,056 Topo50 structures within
    15 m of a national crossing, 420 cross BOTH roads - those are river bridges
    beside a junction, and reading them as grade separation would sever real
    intersections. A structure that runs ALONG one of the two roads is the one
    saying "this road is on a bridge here".
    """
    if structures is None:
        return None, None, None
    buf = p.buffer(STRUCTURE_MATCH_M)
    best: tuple[float, float, str | None] | None = None
    for k in structures.query(buf):
        k = int(k)
        g = structures.geometries[k]
        d = g.distance(p)
        if d > STRUCTURE_MATCH_M:
            continue
        along = g.project(p)
        s_bearing = _angle_at(g, along)
        align = min(
            _fold90(math.degrees(s_bearing - _angle_at(line_a, along_a))),
            _fold90(math.degrees(s_bearing - _angle_at(line_b, along_b))),
        )
        kind = kinds[k] if kinds is not None and k < len(kinds) else None
        if best is None or d < best[0]:
            best = (d, align, kind)
    if best is None:
        return None, None, None
    return best


def _fold90(deg: float) -> float:
    """Fold a signed angle difference into 0..90 degrees."""
    d = deg % 180.0
    if d < 0:
        d += 180.0
    return 180.0 - d if d > 90.0 else d


def build_context(crossing: DetectedCrossing,
                  geoms: Sequence[LineString],
                  attrs: Sequence[dict],
                  *,
                  endpoint_tree: STRtree,
                  endpoint_owner: Sequence[int],
                  motorway_tree: STRtree | None = None,
                  ramp_tree: STRtree | None = None,
                  structure_tree: STRtree | None = None,
                  structure_kinds: Sequence[str] | None = None,
                  ) -> CrossingContext:
    """Assemble the classifier's inputs for one detected crossing."""
    i, j = crossing.index_a, crossing.index_b
    a_at, b_at = attrs[i], attrs[j]
    p = Point(crossing.x, crossing.y)
    s_dist, s_align, s_kind = structure_evidence(
        p, geoms[i], crossing.along_a, geoms[j], crossing.along_b,
        structure_tree, structure_kinds)

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
        structure_dist_m=s_dist,
        structure_align_deg=s_align,
        structure_kind=s_kind,
    )
