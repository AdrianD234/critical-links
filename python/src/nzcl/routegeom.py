"""Ordered route geometry, with the gaps left as gaps.

THE RULE
--------
Never draw a straight line across a hole in the data.

A route is an ordered arc sequence, and pgRouting guarantees it is
TOPOLOGICALLY continuous: each arc's target is the next arc's source. It
guarantees nothing about the GEOMETRY. Two links can share a node and still
have their drawn ends a few metres apart, because nodes were assigned within a
10 mm tolerance while junction splitting worked to 50 mm, and because AMDS
geometry is maintained by many authorities to differing standards.

The tempting fix is to join the ends. That draws a road that does not exist,
across ground nobody surveyed, in the one place the data is known to be weak -
and it is indistinguishable on screen from a road that does. So this module
emits SEPARATE PIECES either side of a gap, records where each gap is and how
wide, and lets the interface say so. A gapped route is drawn as the pieces that
are real, with the animation off and a warning shown.

Coordinates come out in EPSG:4326 because that is what the map draws. Gap
WIDTHS are measured in EPSG:2193 metres, because a gap measured in degrees is
not a distance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Sequence

from . import db

#: Above this, two consecutive pieces are not joined. Set just at the junction
#: splitter's own tolerance (50 mm): anything wider than the rule that decided
#: two ends were the same place is, by that rule's own standard, not the same
#: place.
GAP_TOLERANCE_M = 0.05


@dataclass
class Gap:
    """One discontinuity, named precisely enough to check on the ground."""

    after_arc_id: int
    before_arc_id: int
    at_node: int
    distance_m: float
    #: 4326, so an interface can zoom to it without another round trip.
    from_lon_lat: tuple[float, float]
    to_lon_lat: tuple[float, float]


@dataclass
class RouteGeometry:
    """Contiguous drawable pieces of one route, plus what is missing between them."""

    #: One coordinate list per contiguous piece, in route order. A route with
    #: no gaps has exactly one piece.
    pieces: list[list[tuple[float, float]]] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    #: Arcs asked for that had no geometry at all. Distinct from a gap: a gap
    #: is two pieces that do not meet, this is a piece that is not there.
    missing_arc_ids: list[int] = field(default_factory=list)
    total_drawn_length_m: float = 0.0
    quality_flags: list[str] = field(default_factory=list)

    @property
    def has_gaps(self) -> bool:
        return bool(self.gaps) or bool(self.missing_arc_ids)

    @property
    def continuous(self) -> bool:
        return not self.has_gaps and len(self.pieces) <= 1

    @property
    def animation_safe(self) -> bool:
        """A reveal animation implies one continuous line. A gapped route is not."""
        return self.continuous


def assemble(snapshot_id: str, arc_ids: Sequence[int], *,
             tolerance_m: float = GAP_TOLERANCE_M) -> RouteGeometry:
    """Ordered 4326 geometry for an arc sequence, split at every real gap.

    `arc_ids` is used IN ORDER and is not sorted: the order is the route. The
    lookup is by id so the database is free to return the rows however it
    likes, which keeps the drawn line independent of the query plan.
    """
    out = RouteGeometry()
    if not arc_ids:
        return out

    rows = db.query(
        """
        SELECT a.arc_id, a.link_id, a.direction, a.cost_distance_m,
               ST_AsGeoJSON(l.geom_4326, 7) AS geom,
               ST_X(ST_StartPoint(l.geom_2193)) AS sx,
               ST_Y(ST_StartPoint(l.geom_2193)) AS sy,
               ST_X(ST_EndPoint(l.geom_2193))   AS ex,
               ST_Y(ST_EndPoint(l.geom_2193))   AS ey
          FROM arcs a
          JOIN links l ON l.snapshot_id = a.snapshot_id AND l.link_id = a.link_id
         WHERE a.snapshot_id = %s AND a.arc_id = ANY(%s)
        """,
        (snapshot_id, sorted({int(a) for a in arc_ids})),
    )
    by_arc = {int(r["arc_id"]): r for r in rows}

    node_rows = db.query(
        "SELECT arc_id, source, target FROM arcs "
        " WHERE snapshot_id=%s AND arc_id = ANY(%s)",
        (snapshot_id, sorted({int(a) for a in arc_ids})))
    nodes = {int(r["arc_id"]): (int(r["source"]), int(r["target"]))
             for r in node_rows}

    current: list[tuple[float, float]] = []
    prev_arc: int | None = None
    prev_end_2193: tuple[float, float] | None = None
    prev_end_ll: tuple[float, float] | None = None

    for raw in arc_ids:
        arc = int(raw)
        r = by_arc.get(arc)
        if r is None or not r["geom"]:
            out.missing_arc_ids.append(arc)
            # A missing piece breaks the line. Close what we have; do not
            # bridge across the hole.
            if current:
                out.pieces.append(current)
                current = []
            prev_arc, prev_end_2193, prev_end_ll = None, None, None
            continue

        coords = [(float(x), float(y))
                  for x, y in json.loads(r["geom"])["coordinates"]]
        start_2193 = (float(r["sx"]), float(r["sy"]))
        end_2193 = (float(r["ex"]), float(r["ey"]))
        if r["direction"] == "reverse":
            coords = list(reversed(coords))
            start_2193, end_2193 = end_2193, start_2193
        if len(coords) < 2:
            out.missing_arc_ids.append(arc)
            if current:
                out.pieces.append(current)
                current = []
            prev_arc, prev_end_2193, prev_end_ll = None, None, None
            continue

        out.total_drawn_length_m += float(r["cost_distance_m"] or 0.0)

        if not current:
            current = list(coords)
        else:
            d = _dist(prev_end_2193, start_2193) if prev_end_2193 else 0.0
            if d > tolerance_m:
                shared = nodes.get(arc, (0, 0))[0]
                out.gaps.append(Gap(
                    after_arc_id=prev_arc if prev_arc is not None else -1,
                    before_arc_id=arc, at_node=shared, distance_m=d,
                    from_lon_lat=prev_end_ll or coords[0],
                    to_lon_lat=coords[0]))
                out.pieces.append(current)
                current = list(coords)
            else:
                # Same place within tolerance: drop the duplicated vertex so
                # the line has no zero-length segment.
                current.extend(coords[1:])

        prev_arc = arc
        prev_end_2193 = end_2193
        prev_end_ll = coords[-1]

    if current:
        out.pieces.append(current)

    if out.gaps:
        out.quality_flags.append("GEOMETRY_GAP")
    if out.missing_arc_ids:
        out.quality_flags.append("GEOMETRY_MISSING")
    if len(out.pieces) > 1:
        out.quality_flags.append("GEOMETRY_DISCONTINUOUS")
    return out


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5


def as_geojson(g: RouteGeometry) -> dict | None:
    """MultiLineString of the DRAWABLE pieces only.

    MultiLineString and not LineString even when there is one piece, so a
    client has one shape to handle and cannot accidentally concatenate two
    pieces into a line across a gap by treating the geometry as a single
    coordinate list.
    """
    if not g.pieces:
        return None
    return {
        "type": "MultiLineString",
        "coordinates": [[[round(x, 7), round(y, 7)] for x, y in piece]
                        for piece in g.pieces],
    }


def as_dict(g: RouteGeometry) -> dict:
    return {
        "geometry": as_geojson(g),
        "pieceCount": len(g.pieces),
        "continuous": g.continuous,
        "hasGaps": g.has_gaps,
        # Named so a client cannot mistake it for a styling hint: a gapped
        # route must not be revealed by an animation that implies one line.
        "animationSafe": g.animation_safe,
        "gapCount": len(g.gaps),
        "gaps": [
            {
                "afterArcId": gap.after_arc_id,
                "beforeArcId": gap.before_arc_id,
                "atNode": gap.at_node,
                "distanceM": round(gap.distance_m, 3),
                "fromLonLat": [round(gap.from_lon_lat[0], 7),
                               round(gap.from_lon_lat[1], 7)],
                "toLonLat": [round(gap.to_lon_lat[0], 7),
                             round(gap.to_lon_lat[1], 7)],
            }
            for gap in g.gaps
        ],
        "missingArcIds": g.missing_arc_ids,
        "totalDrawnLengthM": round(g.total_drawn_length_m, 1),
        "qualityFlags": g.quality_flags,
        "gapToleranceM": GAP_TOLERANCE_M,
    }
