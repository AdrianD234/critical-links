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

  NEVER split where two links' INTERIORS cross.
    Neither road ends there. That is an overbridge, a tunnel, or a
    grade-separated interchange. AMDS publishes no z-level attribute, so
    refusing to node interior-interior crossings is the only thing preserving
    grade separation.

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

from .geo import polyline_length

Coord = tuple[float, float]


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


def split_at_junctions(
    sources: Sequence[SourceLink],
    *,
    split_tolerance_m: float = 0.05,
    review_tolerance_m: float = 5.0,
) -> SplitResult:
    """Cut every source link where another link's endpoint lands on its interior."""
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

    out: list[GraphLink] = []
    parents_split = 0
    cuts_made = 0

    for i, s in enumerate(sources):
        cuts = sorted(cuts_by_link.get(i, []), key=lambda c: c[0])
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
    )


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
