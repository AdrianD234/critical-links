"""Cutting links where the outage actually ends, without touching the snapshot.

THE PROBLEM
-----------
Every closure this engine could previously express was a whole graph link,
because a link is the smallest thing the `arcs` table can remove. A real
outage does not consult the data model: a slip closes 400 m of a 1.7 km link,
and closing the link closes 1.3 km of open road.

`closure.py` already refused to paper over the analogous error one level up -
`source_feature` scope closes seventeen kilometres to model five, and says so
in a warning. This module removes the need for that compromise at the bottom
level: the closure can now end exactly where the user put the handle.

WHAT A VIRTUAL SPLIT IS
-----------------------
A request-local graph. The link is left alone; what changes is the edge set ONE
search sees:

    real link            u ------------------------------ v
    handles A and B      u ------- A =========== B ------ v
    edges the search sees
                         u --p1--> A            B --p2--> v
                         (and the reverse pieces)
                         with A ==> B simply absent

`p1` and `p2` are `routing.VirtualArc`s carrying negative ids. They exist in a
literal VALUES list unioned into the edge query and nowhere else - not in a
table, not in a temporary table, not in the snapshot. Two concurrent requests
splitting the same link cannot see each other's pieces, because neither piece
was ever stored. That is the whole reason the design is shaped this way: the
national snapshot is immutable by contract, and a feature that needed to edit
it to answer a question would have broken that contract for every other reader.

WHERE THE IDS COME FROM
-----------------------
`vids`, and nowhere else. Real ids are all non-negative, so the negative range
is free, but "negative is free" is exactly the reasoning that produced an arc
numbered -1 and a replacement path that lost its last leg without saying so.
The reservations and the disjoint node/link/arc bands live in one module with
the argument for them written down.

Ids are drawn in sorted order of what they represent, so the same span produces
the same graph on every run and on every database - which is what makes a
fingerprint over it mean anything.

COST APPORTIONMENT
------------------
A piece is charged its parent arc's own cost scaled by the fraction of geometry
it covers. For distance that is exact. For time it is exact under this model
too, because `speed_kph` is a single derived value per link - there is no
within-link speed profile to lose. Nothing here invents a cost the parent arc
did not already carry.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not choose the corridor. It is handed the ordered links a corridor
occupies and cuts them; deciding WHICH links those are is a separate question
with its own evidence rules, and conflating the two is how a search for the
shortest path between two points comes to be presented as an outage the user
drew.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

from . import db, vids
from .routing import NO_OVERLAY, Profile, VirtualArc, VirtualOverlay
from .vids import VirtualIds

#: Bump when the SPLIT RULE changes shape. Span fingerprints embed it.
SPLIT_MODEL_VERSION = "1.0.0"

#: Which way traffic is stopped.
#:
#: `a_to_b` and `b_to_a` are contraflow-shaped: one direction of the carriageway
#: keeps running. `both` is the ordinary full closure and is the default,
#: because a slip that blocks a road blocks it both ways unless someone says
#: otherwise.
DirectionMode = Literal["both", "a_to_b", "b_to_a"]
DEFAULT_DIRECTION_MODE: DirectionMode = "both"

#: Which way the corridor runs along a link's OWN geometry. A corridor going
#: A -> B may traverse a link against the direction its coordinates are stored
#: in, and the closure has to know that to close the right arc.
Traversal = Literal["forward", "reverse"]

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}

#: A fraction this close to an end is treated as being AT that end. Below this
#: a "piece" would be a few millimetres of road with its own node, which is
#: not a thing the network should be asked to route through. 1e-9 of a 2 km
#: link is 2 micrometres.
END_EPSILON = 1e-9


@dataclass(frozen=True)
class LinkInterval:
    """The closed stretch of one link, in that link's own 0..1 parameter.

    `from_fraction` < `to_fraction` always: the interval is stated in the
    link's coordinate order, and `traversal` separately records which way the
    corridor runs through it. Keeping those apart means the geometry of the
    interval never depends on which handle was placed first.
    """

    link_id: int
    from_fraction: float
    to_fraction: float
    traversal: Traversal
    length_m: float

    @property
    def whole_link(self) -> bool:
        return (self.from_fraction <= END_EPSILON
                and self.to_fraction >= 1.0 - END_EPSILON)

    @property
    def closed_length_m(self) -> float:
        return (self.to_fraction - self.from_fraction) * self.length_m


@dataclass
class VirtualSplit:
    """Everything a search needs in order to see the partial closure."""

    snapshot_id: str
    profile: Profile
    direction_mode: DirectionMode

    overlay: VirtualOverlay
    #: Real arcs the base edge query must drop: superseded by pieces, or closed.
    excluded_arc_ids: list[int]

    #: The routing endpoints. Virtual when the handle is mid-link, real when it
    #: landed on a junction.
    node_at_a: int
    node_at_b: int

    intervals: list[LinkInterval]
    #: Total road actually closed. This is the number the red preview must
    #: agree with, and the one a partial closure exists to get right.
    closed_length_m: float

    fingerprint: str

    #: Pieces that stay OPEN, for drawing and for assertions.
    open_piece_ids: list[int] = field(default_factory=list)
    #: Arc ids that would have existed for the closed stretch. Never routable;
    #: carried so a test can assert no replacement path used one.
    closed_piece_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def splits_a_link(self) -> bool:
        """True when at least one link was cut rather than closed whole."""
        return bool(self.overlay.arcs) or any(
            not i.whole_link for i in self.intervals)


def build(
    snapshot_id: str,
    intervals: Sequence[LinkInterval],
    *,
    handle_a: tuple[int, float],
    handle_b: tuple[int, float],
    profile: Profile = "car",
    direction_mode: DirectionMode = DEFAULT_DIRECTION_MODE,
) -> VirtualSplit:
    """Cut every link the span touches and assemble the request-local graph.

    `handle_a` and `handle_b` are (link_id, fraction) - where the outage starts
    and stops. They are passed separately from `intervals` because they are the
    ROUTING ENDPOINTS as well as interval boundaries, and the search has to be
    able to name them after the split.
    """
    if not intervals:
        raise ValueError("a span with no intervals closes nothing")
    # Checked before the intervals because it is the mistake a USER can make -
    # dragging one handle onto the other - and it deserves the message that
    # says so rather than a complaint about a degenerate sub-range.
    if handle_a[0] == handle_b[0] and abs(handle_a[1] - handle_b[1]) <= END_EPSILON:
        # Two handles at one measure describe a point, not a stretch. Closing
        # it would remove no road while still splitting the link, which is an
        # analysis of nothing dressed up as an outage.
        raise ValueError(
            f"both handles are at the same position on link {handle_a[0]} "
            f"(fraction {handle_a[1]}): an outage needs a length")
    for i in intervals:
        if not (0.0 <= i.from_fraction <= i.to_fraction <= 1.0):
            raise ValueError(
                f"interval on link {i.link_id} is not an ordered sub-range of "
                f"0..1: {i.from_fraction}..{i.to_fraction}")
        if i.to_fraction - i.from_fraction <= END_EPSILON:
            raise ValueError(
                f"interval on link {i.link_id} closes nothing: "
                f"{i.from_fraction}..{i.to_fraction}")

    mode = _MODE_COLUMN.get(profile)
    if mode is None:
        raise ValueError(f"unsupported vehicle profile {profile!r}")

    link_ids = sorted({i.link_id for i in intervals}
                      | {handle_a[0], handle_b[0]})
    links = _links(snapshot_id, link_ids)
    arcs = _arcs(snapshot_id, link_ids, mode)

    # --- where every link gets cut ---------------------------------------
    # Interval ends that fall strictly inside a link, plus the handles
    # themselves. A handle sitting on a junction contributes no cut: it is
    # already a real node.
    cuts: dict[int, set[float]] = {}
    for i in intervals:
        for f in (i.from_fraction, i.to_fraction):
            if END_EPSILON < f < 1.0 - END_EPSILON:
                cuts.setdefault(i.link_id, set()).add(round(f, 12))
    for link_id, f in (handle_a, handle_b):
        if END_EPSILON < f < 1.0 - END_EPSILON:
            cuts.setdefault(link_id, set()).add(round(f, 12))

    # --- deterministic virtual node ids ----------------------------------
    # Sorted by (link_id, fraction) so the same span numbers its nodes the same
    # way on every run. -1 downwards.
    ids = VirtualIds()
    node_of: dict[tuple[int, float], int] = {}
    for key in sorted((lid, f) for lid, fs in cuts.items() for f in fs):
        node_of[key] = ids.node()

    def node_at(link_id: int, fraction: float) -> int:
        """The node id at a position on a link: real at the ends, else virtual."""
        link = links[link_id]
        if fraction <= END_EPSILON:
            return int(link["source_node"])
        if fraction >= 1.0 - END_EPSILON:
            return int(link["target_node"])
        return node_of[(link_id, round(fraction, 12))]

    by_link = _intervals_by_link(intervals)

    virtual_arcs: list[VirtualArc] = []
    anchors: dict[int, int] = {}
    excluded: set[int] = set()
    open_ids: list[int] = []
    closed_ids: list[int] = []

    for link_id in link_ids:
        link = links[link_id]
        link_arcs = arcs.get(link_id, [])
        link_cuts = sorted(cuts.get(link_id, ()))
        closed_here = by_link.get(link_id, [])

        for f in link_cuts:
            anchors[node_of[(link_id, f)]] = int(link["source_node"])

        if not link_cuts:
            # No cut: the link is either wholly closed or wholly untouched, and
            # either way its real arcs answer for it. Nothing virtual needed.
            for arc in link_arcs:
                if _arc_is_closed(arc, link, closed_here, direction_mode,
                                  whole=True):
                    excluded.add(int(arc["arc_id"]))
            continue

        # Cut. Every real arc of this link is superseded by its pieces - even
        # the ones that stay entirely open, because the whole arc spans the cut
        # and leaving it in place would let a route drive straight through the
        # closure on the un-split original.
        boundaries = [0.0, *link_cuts, 1.0]
        for arc in link_arcs:
            excluded.add(int(arc["arc_id"]))
            forward = str(arc["direction"]) == "forward"
            for f0, f1 in zip(boundaries, boundaries[1:]):
                span = f1 - f0
                if span <= END_EPSILON:
                    continue
                arc_id = ids.arc()

                if _piece_is_closed(f0, f1, arc, link, closed_here,
                                    direction_mode):
                    closed_ids.append(arc_id)
                    continue

                # A forward arc runs with the geometry, so its piece goes from
                # the lower fraction to the higher. A reverse arc runs against
                # it and its piece goes the other way.
                u = node_at(link_id, f0 if forward else f1)
                v = node_at(link_id, f1 if forward else f0)
                t = arc["cost_time_s"]
                virtual_arcs.append(VirtualArc(
                    arc_id=arc_id,
                    source=u,
                    target=v,
                    cost_distance_m=span * float(arc["cost_distance_m"]),
                    cost_time_s=None if t is None else span * float(t),
                    link_id=link_id,
                    parent_arc_id=int(arc["arc_id"]),
                    from_fraction=f0,
                    to_fraction=f1,
                ))
                open_ids.append(arc_id)

    closed_length = sum(i.closed_length_m for i in intervals)
    overlay = VirtualOverlay(arcs=tuple(virtual_arcs), component_anchor=anchors)

    return VirtualSplit(
        snapshot_id=snapshot_id,
        profile=profile,
        direction_mode=direction_mode,
        overlay=overlay,
        excluded_arc_ids=sorted(excluded),
        node_at_a=node_at(*handle_a),
        node_at_b=node_at(*handle_b),
        intervals=list(intervals),
        closed_length_m=closed_length,
        fingerprint=fingerprint(snapshot_id, profile, direction_mode, intervals),
        open_piece_ids=sorted(open_ids),
        closed_piece_ids=sorted(closed_ids),
    )


def _intervals_by_link(
        intervals: Sequence[LinkInterval]) -> dict[int, list[LinkInterval]]:
    out: dict[int, list[LinkInterval]] = {}
    for i in intervals:
        out.setdefault(i.link_id, []).append(i)
    return out


def _direction_closed(traversal: Traversal, arc_direction: str,
                      mode: DirectionMode) -> bool:
    """Is an arc running `arc_direction` stopped by a closure of this interval?

    The corridor runs A -> B through this interval in `traversal`, expressed
    against the link's own geometry. So an arc whose direction equals the
    traversal is the A -> B movement, and the opposite arc is B -> A.
    """
    if mode == "both":
        return True
    a_to_b = arc_direction == traversal
    return a_to_b if mode == "a_to_b" else not a_to_b


def _covers(intervals: Sequence[LinkInterval], midpoint: float
            ) -> LinkInterval | None:
    """The interval containing `midpoint`, tested at the midpoint deliberately.

    Comparing piece ends against interval ends invites a float equality that is
    true on one machine and false on another - and a piece that falls out of
    the closure on a rounding difference is a stretch of road that silently
    stays open. A midpoint is unambiguously inside or outside.
    """
    for i in intervals:
        if i.from_fraction <= midpoint <= i.to_fraction:
            return i
    return None


def _piece_is_closed(f0: float, f1: float, arc: dict, link: dict,
                     closed: Sequence[LinkInterval],
                     mode: DirectionMode) -> bool:
    covering = _covers(closed, (f0 + f1) / 2.0)
    if covering is None:
        return False
    return _direction_closed(covering.traversal, str(arc["direction"]), mode)


def _arc_is_closed(arc: dict, link: dict, closed: Sequence[LinkInterval],
                   mode: DirectionMode, *, whole: bool) -> bool:
    covering = _covers(closed, 0.5)
    if covering is None:
        return False
    return _direction_closed(covering.traversal, str(arc["direction"]), mode)


def _links(snapshot_id: str, link_ids: Sequence[int]) -> dict[int, dict]:
    rows = db.query(
        "SELECT link_id, amds_id, closure_group_id, road_name, road_number, "
        "       source_node, target_node, length_m, forward_allowed, "
        "       reverse_allowed "
        "  FROM links WHERE snapshot_id=%s AND link_id = ANY(%s) "
        " ORDER BY link_id",
        (snapshot_id, list(link_ids)),
    )
    found = {int(r["link_id"]): r for r in rows}
    missing = sorted(set(link_ids) - set(found))
    if missing:
        raise KeyError(
            f"links {missing} are not in snapshot {snapshot_id!r}")
    return found


def _arcs(snapshot_id: str, link_ids: Sequence[int],
          mode_column: str) -> dict[int, list[dict]]:
    rows = db.query(
        f"SELECT arc_id, link_id, source, target, direction, cost_distance_m, "
        f"       cost_time_s "
        f"  FROM arcs WHERE snapshot_id=%s AND link_id = ANY(%s) AND {mode_column} "
        f" ORDER BY arc_id",
        (snapshot_id, list(link_ids)),
    )
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(int(r["link_id"]), []).append(r)
    return out


def span_geometry(snapshot_id: str, intervals: Sequence[LinkInterval]) -> dict:
    """The closed stretch as drawable geometry, and its measured length.

    This is what the red preview draws, and it is cut from the SAME fractions
    the closure is built from - one `ST_LineSubstring` per interval, in the
    link's own parameter space. Deriving the picture and the closure from one
    number is what makes "the red line is exactly what got closed" a property
    rather than a hope.

    `measuredLengthM` is the length of the geometry as PostGIS measures it,
    returned alongside the arithmetic total so a caller can compare them. They
    agree to well under a millimetre: `ST_LineSubstring` cuts by fraction of
    2D length, so a substring of a curve is exactly that fraction of it, and
    curvature does not enter.
    """
    if not intervals:
        raise ValueError("no intervals to draw")

    rows = db.query(
        """
        SELECT v.link_id,
               ST_AsGeoJSON(ST_Transform(sub, 4326))::json AS geojson,
               ST_Length(sub)                              AS length_m
          FROM (
            SELECT l.link_id,
                   ST_LineSubstring(l.geom_2193, v.f0, v.f1) AS sub
              FROM unnest(%s::bigint[], %s::double precision[],
                          %s::double precision[]) AS v(link_id, f0, f1)
              JOIN links l
                ON l.snapshot_id = %s AND l.link_id = v.link_id
          ) v
         ORDER BY v.link_id
        """,
        ([i.link_id for i in intervals],
         [i.from_fraction for i in intervals],
         [i.to_fraction for i in intervals],
         snapshot_id),
    )

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": r["geojson"],
                "properties": {
                    "linkId": int(r["link_id"]),
                    "lengthM": round(float(r["length_m"]), 3),
                },
            }
            for r in rows
        ],
        "measuredLengthM": round(
            sum(float(r["length_m"]) for r in rows), 3),
        "arithmeticLengthM": round(
            sum(i.closed_length_m for i in intervals), 3),
    }


def fingerprint(snapshot_id: str, profile: str, direction_mode: str,
                intervals: Iterable[LinkInterval]) -> str:
    """Deterministic identity of WHAT IS CLOSED, for caching and for drag.

    Keyed on the closed intervals rather than on the handles, because two
    different drags that arrive at the same closed road are the same closure
    and deserve the same answer. Fractions are rounded to nine places - about a
    nanometre on a kilometre - so a re-computed fraction that differs in the
    last float bit does not invent a new cache key on every mouse move.

    `link_id` is positional, so this is stable WITHIN a snapshot and not
    across ingests. The snapshot id is in the payload, which is what makes that
    safe: a key from another snapshot can never be read as one of these.
    """
    parts = sorted(
        f"{int(i.link_id)}:{i.from_fraction:.9f}:{i.to_fraction:.9f}:{i.traversal}"
        for i in intervals
    )
    payload = "|".join((
        "outage-span", SPLIT_MODEL_VERSION, snapshot_id, profile,
        direction_mode, ",".join(parts),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def as_dict(s: VirtualSplit) -> dict:
    """API shape for the split itself. Geometry is added by the caller."""
    return {
        "directionMode": s.direction_mode,
        "closedLengthM": round(s.closed_length_m, 1),
        "intervals": [
            {
                "linkId": i.link_id,
                "fromFraction": round(i.from_fraction, 9),
                "toFraction": round(i.to_fraction, 9),
                "traversal": i.traversal,
                "wholeLink": i.whole_link,
                "closedLengthM": round(i.closed_length_m, 1),
            }
            for i in s.intervals
        ],
        "splitsALink": s.splits_a_link,
        "virtualArcCount": len(s.overlay.arcs),
        "excludedArcCount": len(s.excluded_arc_ids),
        "fingerprint": s.fingerprint,
        "splitModelVersion": SPLIT_MODEL_VERSION,
    }
