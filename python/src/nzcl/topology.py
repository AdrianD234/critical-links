"""Junction splitting.

Why this exists
---------------
The AMDS Network Model does not split a through road where a side road
terminates on it. Measured on the Wellington pilot extract: of 16,463 endpoints
touched by only one link, 7,119 sat within 10 mm of the INTERIOR of another
link. Endpoint-only noding therefore leaves the network shattered - the pilot
graph had 5,719 connected components with the largest holding only 21% of
links, which is not a road network.

The rule applied here, and the distinction that makes it safe:

  SPLIT when one link's ENDPOINT lies on another link's interior.
    A road that stops dead on another road's centreline is terminating at it.
    This is a T-junction, a ramp merge, or a side road - a real connection.

  Where two links' INTERIORS cross, ASK.
    This used to be a flat refusal, on the reasoning that neither road ends
    there so it must be an overbridge, a tunnel or a grade-separated
    interchange, and that AMDS publishes no z-level to tell them apart.

    The reasoning is false on a flat rural grid, where a through road crosses
    another through road at grade and neither terminates. It was proved false
    rather than argued false: near Darfield, Canterbury, Clintons Road and
    McLaughlins Road cross at 0.000 m separation with fully disjoint node sets,
    and closing 675 m of Greendale Road returned a 7,944 m replacement path
    where noding that one crossing returns 4,916 m. The counterfactual was run
    on an isolated copy of the national snapshot and rolled back; it is in
    docs/audits/at-grade-crossings/.

    (The z-level half of the old reasoning turned out to be wrong too, in the
    other direction: AMDS DOES publish z, the ingest simply never asked for it.
    It is a LiDAR terrain drape and would not have helped - known motorway
    interchange crossings come back 0.00-0.09 m apart. See nzcl.crossings.)

    So `nzcl.crossings` classifies each interior crossing AT_GRADE,
    GRADE_SEPARATED or UNRESOLVED on evidence, and only AT_GRADE is cut.
    `crossing_policy` chooses which dispositions are honoured, which is what
    makes the CONFIRMED and POSSIBLE graphs two runs of one function rather
    than two implementations that can drift apart.

The tolerance is deliberately tight (50 mm). Endpoints falling between the split
tolerance and a wider review distance are NOT connected; they are reported as
near misses so a genuine gap in the source stays visible rather than papered
over.

Every child link keeps its parent's AMDS id as `closure_group_id`, so closing a
road still closes the whole of it after splitting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from . import crossings as crossings_mod
from .geo import polyline_length

Coord = tuple[float, float]

#: Which crossing dispositions `split_at_junctions` is allowed to cut at.
#:
#:   none       endpoint junctions only. The rule as it stood before this
#:              change, kept so the two can be compared on one snapshot.
#:   confirmed  + AT_GRADE. THE CANONICAL GRAPH. Every published answer.
#:   possible   + UNRESOLVED. A SENSITIVITY INSTRUMENT ONLY. It exists to
#:              answer "would this result change if the crossings we cannot
#:              resolve turned out to be junctions?" and must never be shown
#:              as the official route.
#:
#: GRADE_SEPARATED is in no policy. Nothing cuts there.
CROSSING_POLICIES = ("none", "confirmed", "possible")

_POLICY_DISPOSITIONS: dict[str, frozenset[str]] = {
    "none": frozenset(),
    "confirmed": frozenset({crossings_mod.AT_GRADE}),
    "possible": frozenset({crossings_mod.AT_GRADE, crossings_mod.UNRESOLVED}),
}


@dataclass
class SourceLink:
    """One physical source record, before splitting."""
    amds_id: str
    coords: list[Coord]
    attrs: dict = field(default_factory=dict)


@dataclass
class GraphLink:
    """One graph link, after splitting. Several may share a closure group."""
    amds_id: str
    closure_group_id: str
    coords: list[Coord]
    length_m: float
    quality_flags: list[str]
    attrs: dict


@dataclass
class NearMiss:
    amds_id: str
    other_amds_id: str
    distance_m: float
    x: float
    y: float


@dataclass
class SplitResult:
    links: list[GraphLink]
    parents_split: int
    cuts_made: int
    near_misses: list[NearMiss]
    #: Every interior-to-interior crossing found, whatever was done with it.
    #: The ones NOT cut matter as much as the ones that were: a GRADE_SEPARATED
    #: crossing is a claim, and an UNRESOLVED one is a piece of doubt that has
    #: to reach the answer rather than being swallowed here.
    crossings: list = field(default_factory=list)
    crossing_cuts: int = 0
    crossing_policy: str = "none"
    #: Cluster label per crossing, parallel to `crossings`. Several pairs share
    #: one label where divided carriageways meet, which is why a pair count is
    #: not a count of intersections.
    crossing_places: list[int] = field(default_factory=list)
    #: Crossings withdrawn because other crossings at the same place disagreed.
    mixed_place_demotions: int = 0


def split_at_junctions(
    sources: Sequence[SourceLink],
    *,
    split_tolerance_m: float = 0.05,
    review_tolerance_m: float = 5.0,
    crossing_policy: str = "confirmed",
    structures: Sequence[tuple[LineString, str]] | None = None,
) -> SplitResult:
    """Cut every source link at a junction, and say what a junction is.

    Two kinds of cut are made, and they are found in different ways:

      * another link's ENDPOINT lands on this link's interior - a T-junction;
      * two links' INTERIORS cross and `nzcl.crossings` classifies the crossing
        as one this `crossing_policy` honours.

    `crossing_policy='none'` reproduces the behaviour this module had before
    at-grade crossings were handled at all, so a snapshot can be built both ways
    and the difference measured rather than asserted.
    """
    if crossing_policy not in CROSSING_POLICIES:
        raise ValueError(
            f"crossing_policy must be one of {CROSSING_POLICIES}, "
            f"not {crossing_policy!r}")
    geoms = [LineString(s.coords) for s in sources]

    # Index every endpoint, so for each line we can ask which endpoints are near.
    endpoints: list[Point] = []
    endpoint_owner: list[int] = []
    for i, s in enumerate(sources):
        endpoints.append(Point(s.coords[0]))
        endpoint_owner.append(i)
        endpoints.append(Point(s.coords[-1]))
        endpoint_owner.append(i)
    tree = STRtree(endpoints)

    # Each cut records both WHERE along the line to break, and the exact
    # coordinate to insert there. The inserted vertex is the terminating link's
    # own endpoint, not the projection of it onto this line. Using the
    # projection would cut the through road without connecting anything: the
    # two coordinates would sit up to `split_tolerance_m` apart, which is wider
    # than the node-assignment tolerance, so no shared node would form and the
    # network would stay severed at every junction.
    cuts_by_link: dict[int, list[tuple[float, Coord]]] = {}
    near_misses: list[NearMiss] = []

    for i, line in enumerate(geoms):
        own_start = Point(sources[i].coords[0])
        own_end = Point(sources[i].coords[-1])
        candidates = tree.query(line.buffer(review_tolerance_m))
        seen_positions: set[int] = set()

        for c in candidates:
            ci = int(c)
            if endpoint_owner[ci] == i:
                continue
            pt = endpoints[ci]
            dist = line.distance(pt)
            if dist > review_tolerance_m:
                continue

            # Coincident with this line's OWN endpoints: already a shared node,
            # no cut required.
            if pt.distance(own_start) <= split_tolerance_m or \
               pt.distance(own_end) <= split_tolerance_m:
                continue

            if dist > split_tolerance_m:
                if len(near_misses) < 50_000:
                    near_misses.append(
                        NearMiss(
                            amds_id=sources[i].amds_id,
                            other_amds_id=sources[endpoint_owner[ci]].amds_id,
                            distance_m=dist,
                            x=pt.x, y=pt.y,
                        )
                    )
                continue

            # Distance along the line at which to cut.
            pos = line.project(pt)
            if pos <= split_tolerance_m or pos >= line.length - split_tolerance_m:
                continue
            key = int(round(pos * 1000))
            if key in seen_positions:
                continue
            seen_positions.add(key)
            cuts_by_link.setdefault(i, []).append((pos, (pt.x, pt.y)))

    # --- interior-to-interior crossings -----------------------------------
    found = crossings_mod.detect(geoms, [s.amds_id for s in sources],
                                 end_guard_m=split_tolerance_m)
    honoured = _POLICY_DISPOSITIONS[crossing_policy]
    crossing_cuts = 0
    crossing_places: list[int] = []
    mixed_demoted = 0
    if found:
        attrs = [s.attrs for s in sources]
        motorway_tree, ramp_tree = _context_trees(sources, geoms)
        structure_tree = structure_kinds = None
        if structures:
            structure_tree = STRtree([g for g, _ in structures])
            structure_kinds = [k for _, k in structures]
        for x in found:
            x.classification = crossings_mod.classify(
                crossings_mod.build_context(
                    x, geoms, attrs,
                    endpoint_tree=tree, endpoint_owner=endpoint_owner,
                    motorway_tree=motorway_tree, ramp_tree=ramp_tree,
                    structure_tree=structure_tree,
                    structure_kinds=structure_kinds))

        # Places whose crossings disagree are withdrawn ENTIRELY, before any
        # cut is planned. A graph node grants every incident arc every
        # movement, so noding the at-grade pair of a mixed interchange would
        # hand a grade-separated third road the same turns - the exact defect
        # the never-node rule existed to prevent.
        crossing_places, mixed_demoted = crossings_mod.demote_mixed_places(found)

        for x in found:
            if x.disposition not in honoured:
                continue
            # `safe_to_node` is a separate question from the disposition: it
            # asks whether acting on the verdict is REPRESENTABLE. Tangential
            # grazes, a road crossing itself, and mixed places all fail it, and
            # none of them may be cut under any policy.
            if not x.classification.safe_to_node:
                continue
            # BOTH sides are cut at the SAME coordinate. That is the whole
            # mechanism: `assign_nodes` gives one node to coincident endpoints,
            # so cutting both lines at one point is what creates the junction.
            # Cutting only one would sever a road and connect nothing.
            for idx, along in ((x.index_a, x.along_a), (x.index_b, x.along_b)):
                cuts_by_link.setdefault(idx, []).append((along, (x.x, x.y)))
            crossing_cuts += 1

    out: list[GraphLink] = []
    parents_split = 0
    cuts_made = 0

    for i, s in enumerate(sources):
        # Endpoint cuts and crossing cuts are found independently and can land
        # on the same millimetre - a side road ending exactly where two through
        # roads cross is a real and common arrangement. Cutting twice there
        # would emit a zero-length piece, which `links_length_m_check` rejects.
        cuts = _dedupe_cuts(cuts_by_link.get(i, []), split_tolerance_m)
        if not cuts:
            out.append(_make_link(s, s.coords, 0, 1))
            continue

        parts = _cut_line(geoms[i], cuts)
        usable = [p for p in parts if len(p) >= 2 and polyline_length(p) > 0]
        if len(usable) <= 1:
            out.append(_make_link(s, s.coords, 0, 1))
            continue

        parents_split += 1
        cuts_made += len(usable) - 1
        for idx, coords in enumerate(usable):
            out.append(_make_link(s, coords, idx, len(usable)))

    return SplitResult(
        links=out,
        parents_split=parents_split,
        cuts_made=cuts_made,
        near_misses=near_misses,
        crossings=found,
        crossing_cuts=crossing_cuts,
        crossing_policy=crossing_policy,
        crossing_places=crossing_places,
        mixed_place_demotions=mixed_demoted,
    )


def audit_no_invented_movements(result: SplitResult,
                                node_tolerance_m: float = 0.01) -> list[str]:
    """Prove that no crossing left disconnected became connected anyway.

    The safety property this change has to hold is narrow and checkable:

        for every crossing NOT noded, the two source features must share no
        graph node at that crossing point.

    It is not implied by the splitting code. Nodes are assigned by coordinate
    proximity, so a crossing that was deliberately refused can still end up
    connected if some OTHER cut happens to land on the same coordinate and both
    roads touch it. That is the mixed-place hazard, and asserting the absence
    of the hazard is worth more than reasoning about when it can arise.

    Returns a list of violations, empty when the graph is sound.
    """
    if not result.crossings:
        return []

    pairs, coords = assign_nodes(result.links, tolerance_m=node_tolerance_m)
    # node id -> the source features that touch it
    touching: dict[int, set[str]] = {}
    for link, (a, b) in zip(result.links, pairs):
        touching.setdefault(a, set()).add(link.closure_group_id)
        touching.setdefault(b, set()).add(link.closure_group_id)

    grid: dict[tuple[int, int], list[int]] = {}
    for nid, (x, y) in enumerate(coords):
        grid.setdefault((int(x // 1.0), int(y // 1.0)), []).append(nid)

    violations: list[str] = []
    for x in result.crossings:
        if x.classification is not None and x.disposition in \
                _POLICY_DISPOSITIONS[result.crossing_policy] and \
                x.classification.safe_to_node:
            continue  # this one was meant to be noded
        cx, cy = int(x.x // 1.0), int(x.y // 1.0)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid in grid.get((cx + dx, cy + dy), ()):
                    px, py = coords[nid]
                    if (px - x.x) ** 2 + (py - x.y) ** 2 > 1.0:
                        continue
                    both = {x.amds_a, x.amds_b} <= touching.get(nid, set())
                    if both:
                        reason = (x.classification.reason
                                  if x.classification else "?")
                        violations.append(
                            f"{x.amds_a} x {x.amds_b} at "
                            f"({x.x:.3f}, {x.y:.3f}) was {x.disposition} "
                            f"({reason}) but shares node {nid}")
    return violations


def _dedupe_cuts(cuts: Sequence[tuple[float, Coord]], tolerance_m: float
                 ) -> list[tuple[float, Coord]]:
    """Sort cuts along the line and drop any that repeat a position."""
    out: list[tuple[float, Coord]] = []
    for pos, coord in sorted(cuts, key=lambda c: c[0]):
        if out and pos - out[-1][0] <= tolerance_m:
            continue
        out.append((pos, coord))
    return out


def _context_trees(sources: Sequence[SourceLink], geoms: Sequence[LineString]
                   ) -> tuple[STRtree | None, STRtree | None]:
    """Spatial indexes of motorway carriageways and of ramps.

    Built once per split rather than per crossing: at national scale there are
    13,056 crossings and 262,000 roadway links, and a linear scan per crossing
    is the difference between seconds and hours.
    """
    motorway = [g for s, g in zip(sources, geoms)
                if s.attrs.get("rca_code") == 1 and s.attrs.get("oneway") == 1]
    ramps = [g for s, g in zip(sources, geoms) if s.attrs.get("is_ramp")]
    return (STRtree(motorway) if motorway else None,
            STRtree(ramps) if ramps else None)


def _make_link(src: SourceLink, coords: Sequence[Coord], part: int,
               part_count: int) -> GraphLink:
    flags = list(src.attrs.get("quality_flags") or [])
    if part_count > 1:
        flags.append("SPLIT_AT_JUNCTION")
    return GraphLink(
        # The durable source id is kept, suffixed so each piece is addressable.
        amds_id=f"{src.amds_id}#{part}" if part_count > 1 else src.amds_id,
        # All pieces of one source link close together.
        closure_group_id=src.amds_id,
        coords=[(float(x), float(y)) for x, y in coords],
        length_m=polyline_length(coords),
        quality_flags=flags,
        attrs=src.attrs,
    )


def _cut_line(
    line: LineString, cuts: Sequence[tuple[float, Coord]]
) -> list[list[Coord]]:
    """Split `line` at the given distances, inserting the supplied coordinate.

    The inserted vertex is the junction point supplied by the caller (the
    terminating link's endpoint), so both sides of the junction end up on the
    exact same coordinate and node assignment produces a shared node.
    """
    coords = [(float(x), float(y)) for x, y in line.coords]
    cum = [0.0]
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        cum.append(cum[-1] + ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)

    parts: list[list[Coord]] = []
    current: list[Coord] = [coords[0]]
    cut_iter = iter(sorted(cuts, key=lambda c: c[0]))
    nxt = next(cut_iter, None)

    for vi in range(1, len(coords)):
        seg_start, seg_end = cum[vi - 1], cum[vi]
        # Cuts falling strictly inside this segment, or on its far vertex.
        while nxt is not None and seg_start < nxt[0] <= seg_end + 1e-9:
            pt = (float(nxt[1][0]), float(nxt[1][1]))
            if _dist(pt, current[-1]) > 1e-9:
                current.append(pt)
            if len(current) >= 2:
                parts.append(current)
                current = [pt]
            nxt = next(cut_iter, None)
        if _dist(coords[vi], current[-1]) > 1e-9:
            current.append(coords[vi])

    if len(current) >= 2:
        parts.append(current)
    return parts


def _dist(a: Coord, b: Coord) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def assign_nodes(
    links: Sequence[GraphLink], tolerance_m: float = 0.01
) -> tuple[list[tuple[int, int]], list[Coord]]:
    """Resolve link endpoints to node ids.

    Node identity comes from coincident endpoints only. Two lines that merely
    cross in plan view never share a node - that is what preserves grade
    separation, and it is why `split_at_junctions` must run first.

    The tolerance absorbs float round-tripping through JSON, not real gaps.
    """
    index: dict[tuple[int, int], list[int]] = {}
    coords: list[Coord] = []
    pairs: list[tuple[int, int]] = []
    tol2 = tolerance_m * tolerance_m

    def node_of(x: float, y: float) -> int:
        cx, cy = int(x // tolerance_m), int(y // tolerance_m)
        # Probe the 3x3 cell neighbourhood, not just the cell the point lands
        # in. Quantising to a single grid cell splits two points that straddle
        # a cell boundary no matter how close they are: a real case in the
        # Wellington pilot had two link endpoints 0.4 mm apart assigned to
        # different nodes, severing a junction and sending a detour 3.6 km the
        # wrong way. Found by cross-validating against the TypeScript engine.
        best = -1
        best_d2 = tol2
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid in index.get((cx + dx, cy + dy), ()):
                    px, py = coords[nid]
                    d2 = (px - x) ** 2 + (py - y) ** 2
                    if d2 <= best_d2:
                        best_d2 = d2
                        best = nid
        if best >= 0:
            return best

        nid = len(coords)
        coords.append((x, y))
        index.setdefault((cx, cy), []).append(nid)
        return nid

    for link in links:
        pairs.append((node_of(*link.coords[0]), node_of(*link.coords[-1])))
    return pairs, coords
