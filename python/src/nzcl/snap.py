"""Where the user actually pointed: a click, resolved onto a road centreline.

WHAT A HANDLE IS
----------------
Every existing selection in this system is a WHOLE graph link: you click a
road, the engine closes that link. An outage does not respect graph links. A
slip closes 400 m of a 1.7 km link, and the link is the unit the data was
maintained in, not the unit the road was blocked in.

So a handle is a LINEAR REFERENCE - a position ALONG a centreline, not a link:

    link_id             which graph link hosts it
    distance_along_m    how far along that link's geometry, in NZTM metres
    fraction            the same thing as a 0..1 parameter, which is what
                        PostGIS interpolation actually takes

Both are carried because they answer different questions and rounding one to
recover the other loses precision. `fraction` is what splits geometry;
`distance_along_m` is what a human reads and what the drag interaction steps.

SNAPPING IS TO THE CENTRELINE, NOT TO THE MIDDLE OF THE ROAD
------------------------------------------------------------
The nearest point on the polyline, which is generally NOT a vertex and
generally NOT the link's midpoint. AMDS geometry already IS a centreline
representation, so no lateral offset is applied and none should be: this
network models a road as a line, and inventing a carriageway edge would be
asserting a width the data does not carry.

AMBIGUITY IS REPORTED, NEVER GUESSED
------------------------------------
A click between the two carriageways of a divided road is genuinely two
different answers, and picking the nearer by a metre is picking by measurement
noise. `SnapResult.ambiguous` says so and carries the rival candidates, so the
interface can ask instead of silently closing the wrong carriageway.

The test for ambiguity is deliberately NOT "two candidates have similar
offsets". Near a junction every click has several near-equidistant candidates -
the links that meet there - but they all snap to the SAME PLACE, so which one
hosts the handle does not change what the user sees or what gets closed. What
matters is two candidates that are equally plausible and land somewhere
DIFFERENT. That is the divided-carriageway case, and it is the only one worth
interrupting for.

STABILITY
---------
`link_id` is positional - handed out in ingest order - so a handle keyed on it
cannot survive a re-ingest. `stable_key` is hashed from the AMDS feature id and
the position along it in millimetres, both publisher-chosen, for exactly the
reasons `stableid.py` sets out.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Sequence

from . import db
from .routing import Profile

#: Bump when the snap RULE changes shape - a different search radius policy, a
#: different ambiguity test. Handle keys embed it, so a handle stored under an
#: older rule can never be silently reinterpreted under a newer one.
SNAP_MODEL_VERSION = "1.0.0"

_MODE_COLUMN = {"car": "mode_vehicle", "heavy": "mode_vehicle_heavy",
                "emergency": "mode_emergency"}

#: How far from the click to look for a centreline, in metres. Generous
#: because a click on a wide motorway carriageway at low zoom is legitimately
#: tens of metres from the line, and a snap that silently fails is worse than
#: one that reaches.
DEFAULT_SEARCH_RADIUS_M = 150.0

#: Beyond this the click is not "on a road" in any useful sense and the caller
#: is told so rather than handed the nearest road in the region.
MAX_SEARCH_RADIUS_M = 2_000.0

#: Two candidates whose offsets from the raw click differ by no more than this
#: are equally plausible: the user's pointing precision cannot separate them.
AMBIGUITY_TOLERANCE_M = 6.0

#: ...but they are only a genuine choice if they land this far apart. Closer
#: than this and they are the same place reached along different links, which
#: is a junction, not a decision. See the module docstring.
COINCIDENT_TOLERANCE_M = 3.0

#: How many rivals to carry. Three is the interface's budget for "which of
#: these did you mean"; more is a list nobody reads.
MAX_CANDIDATES = 4


@dataclass(frozen=True)
class SnapCandidate:
    """One centreline position a click could reasonably have meant."""

    link_id: int
    amds_id: str
    closure_group_id: str
    road_name: str | None
    road_number: str | None

    #: Position along the host link's geometry.
    distance_along_m: float
    fraction: float
    length_m: float

    #: The snapped point itself, in both frames. NZTM is what everything
    #: measures in; WGS84 exists only so the map can draw it.
    x: float
    y: float
    lon: float
    lat: float

    #: How far the raw click was from the centreline. This is the number that
    #: decides ranking, and the one the ambiguity test reads.
    offset_m: float

    forward_allowed: bool
    reverse_allowed: bool
    oneway: int | None

    stable_key: str

    @property
    def at_start(self) -> bool:
        return self.fraction <= 0.0

    @property
    def at_end(self) -> bool:
        return self.fraction >= 1.0


@dataclass
class SnapResult:
    """The chosen handle, the rivals, and whether choosing was safe."""

    snapshot_id: str
    #: The raw click, in NZTM metres.
    query_x: float
    query_y: float
    search_radius_m: float
    profile: Profile

    #: Best candidate by offset. None when nothing was in range.
    chosen: SnapCandidate | None
    candidates: list[SnapCandidate]

    #: True when a rival is equally plausible AND lands somewhere else.
    ambiguous: bool
    ambiguity_reason: str | None = None

    @property
    def found(self) -> bool:
        return self.chosen is not None


def handle_key(amds_id: str, distance_along_m: float) -> str:
    """Identity of a handle, in terms the publisher chose.

    Millimetre resolution: finer than any positioning the interface can
    express, and far finer than the 10 mm tolerance node assignment works to,
    so two deliberately distinct handles cannot collide onto one key.
    """
    payload = "|".join((
        "outage-handle",
        SNAP_MODEL_VERSION,
        amds_id,
        f"{distance_along_m:.3f}",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def snap(
    snapshot_id: str,
    x: float,
    y: float,
    *,
    profile: Profile = "car",
    search_radius_m: float = DEFAULT_SEARCH_RADIUS_M,
    limit: int = MAX_CANDIDATES,
) -> SnapResult:
    """Resolve an NZTM click onto the nearest usable road centreline.

    Ordering is by true distance, computed by `ST_Distance`, with the KNN
    operator used only to shortlist. `<->` on a geometry column orders by
    bounding-box distance at index level, which is not the same thing for a
    long diagonal link: the box of a 2 km link running north-east can be nearer
    a click than the box of the 40 m link the click is sitting on top of.
    Shortlisting on the index and re-ranking exactly is both correct and fast.
    """
    if search_radius_m <= 0 or search_radius_m > MAX_SEARCH_RADIUS_M:
        raise ValueError(
            f"search_radius_m must be in (0, {MAX_SEARCH_RADIUS_M}], got "
            f"{search_radius_m}")
    mode = _MODE_COLUMN.get(profile)
    if mode is None:
        raise ValueError(f"unsupported vehicle profile {profile!r}")

    rows = db.query(
        f"""
        WITH click AS (
            SELECT ST_SetSRID(ST_MakePoint(%s, %s), 2193) AS p
        ),
        near AS (
            SELECT l.link_id, l.amds_id, l.closure_group_id, l.road_name,
                   l.road_number, l.length_m, l.geom_2193,
                   l.forward_allowed, l.reverse_allowed, l.oneway
              FROM links l, click c
             WHERE l.snapshot_id = %s
               AND l.{mode}
               AND ST_DWithin(l.geom_2193, c.p, %s)
             ORDER BY l.geom_2193 <-> c.p
             LIMIT %s
        )
        SELECT n.link_id, n.amds_id, n.closure_group_id, n.road_name,
               n.road_number, n.length_m, n.forward_allowed,
               n.reverse_allowed, n.oneway,
               ST_Distance(n.geom_2193, c.p)              AS offset_m,
               ST_LineLocatePoint(n.geom_2193, c.p)       AS fraction,
               ST_X(ST_ClosestPoint(n.geom_2193, c.p))    AS x,
               ST_Y(ST_ClosestPoint(n.geom_2193, c.p))    AS y,
               ST_X(ST_Transform(ST_ClosestPoint(n.geom_2193, c.p), 4326)) AS lon,
               ST_Y(ST_Transform(ST_ClosestPoint(n.geom_2193, c.p), 4326)) AS lat
          FROM near n, click c
         ORDER BY offset_m, n.link_id
        """,
        # The shortlist is taken wider than the caller's limit so that exact
        # re-ranking has something to reorder; see the docstring.
        (x, y, snapshot_id, search_radius_m, max(limit * 4, 16)),
    )

    candidates = [_candidate(r) for r in rows][:limit]
    chosen = candidates[0] if candidates else None
    ambiguous, reason = _ambiguity(candidates)

    return SnapResult(
        snapshot_id=snapshot_id,
        query_x=float(x),
        query_y=float(y),
        search_radius_m=float(search_radius_m),
        profile=profile,
        chosen=chosen,
        candidates=candidates,
        ambiguous=ambiguous,
        ambiguity_reason=reason,
    )


def at_position(
    snapshot_id: str,
    link_id: int,
    *,
    distance_along_m: float | None = None,
    fraction: float | None = None,
) -> SnapCandidate:
    """Rebuild a handle from a stored linear reference, without a click.

    This is what a permalink and a drag both reload through: the handle is
    addressed by where it IS, not by where someone once pointed. Exactly one of
    `distance_along_m` or `fraction` is required - accepting both would invite
    a caller to pass an inconsistent pair and silently prefer one.
    """
    if (distance_along_m is None) == (fraction is None):
        raise ValueError(
            "supply exactly one of distance_along_m or fraction")

    row = db.query_one(
        "SELECT link_id, amds_id, closure_group_id, road_name, road_number, "
        "       length_m, forward_allowed, reverse_allowed, oneway "
        "  FROM links WHERE snapshot_id=%s AND link_id=%s",
        (snapshot_id, link_id),
    )
    if row is None:
        raise KeyError(f"unknown link {link_id} in snapshot {snapshot_id}")

    length = float(row["length_m"])
    if fraction is None:
        frac = _clamp(float(distance_along_m) / length, 0.0, 1.0)
    else:
        frac = _clamp(float(fraction), 0.0, 1.0)

    geom = db.query_one(
        """
        SELECT ST_X(pt) AS x, ST_Y(pt) AS y,
               ST_X(ST_Transform(pt, 4326)) AS lon,
               ST_Y(ST_Transform(pt, 4326)) AS lat
          FROM (SELECT ST_LineInterpolatePoint(geom_2193, %s) AS pt
                  FROM links WHERE snapshot_id=%s AND link_id=%s) q
        """,
        (frac, snapshot_id, link_id),
    )
    assert geom is not None  # the link row was just read under the same key

    return _candidate({
        **row,
        "fraction": frac,
        "x": geom["x"], "y": geom["y"],
        "lon": geom["lon"], "lat": geom["lat"],
        # A rebuilt handle has no raw click to be offset from. Zero is honest
        # here: the handle IS on the centreline by construction.
        "offset_m": 0.0,
    })


def _candidate(row: dict) -> SnapCandidate:
    length = float(row["length_m"])
    frac = _clamp(float(row["fraction"]), 0.0, 1.0)
    along = frac * length
    return SnapCandidate(
        link_id=int(row["link_id"]),
        amds_id=str(row["amds_id"]),
        closure_group_id=str(row["closure_group_id"]),
        road_name=row.get("road_name"),
        road_number=row.get("road_number"),
        distance_along_m=along,
        fraction=frac,
        length_m=length,
        x=float(row["x"]),
        y=float(row["y"]),
        lon=float(row["lon"]),
        lat=float(row["lat"]),
        offset_m=float(row["offset_m"]),
        forward_allowed=bool(row["forward_allowed"]),
        reverse_allowed=bool(row["reverse_allowed"]),
        oneway=None if row.get("oneway") is None else int(row["oneway"]),
        stable_key=handle_key(str(row["amds_id"]), along),
    )


def _ambiguity(candidates: Sequence[SnapCandidate]) -> tuple[bool, str | None]:
    """Is the best candidate a real choice, or the only sensible reading?

    Both conditions must hold, and the second is the one that stops junctions
    being reported as ambiguous:

      - a rival is within `AMBIGUITY_TOLERANCE_M` of the best offset, so the
        click cannot separate them; and
      - that rival snaps more than `COINCIDENT_TOLERANCE_M` away, so choosing
        wrongly puts the handle somewhere the user did not point.
    """
    if len(candidates) < 2:
        return False, None
    best = candidates[0]
    for rival in candidates[1:]:
        if rival.offset_m - best.offset_m > AMBIGUITY_TOLERANCE_M:
            break
        separation = ((rival.x - best.x) ** 2 + (rival.y - best.y) ** 2) ** 0.5
        if separation > COINCIDENT_TOLERANCE_M:
            best_name = best.road_name or best.amds_id
            rival_name = rival.road_name or rival.amds_id
            same_name = (best.road_name is not None
                         and best.road_name == rival.road_name)
            detail = (
                f"two carriageways of {best_name} are"
                if same_name else
                f"{best_name} and {rival_name} are"
            )
            return True, (
                f"{detail} within {AMBIGUITY_TOLERANCE_M:.0f} m of this click "
                f"but {separation:.0f} m apart. Choose which one the outage is "
                f"on."
            )
    return False, None


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def candidate_as_dict(c: SnapCandidate) -> dict:
    """API shape for one handle."""
    return {
        "linkId": c.link_id,
        "amdsId": c.amds_id,
        "closureGroupId": c.closure_group_id,
        "roadName": c.road_name,
        "roadNumber": c.road_number,
        "distanceAlongM": round(c.distance_along_m, 3),
        "fraction": round(c.fraction, 9),
        "linkLengthM": round(c.length_m, 3),
        "x": round(c.x, 3),
        "y": round(c.y, 3),
        "lon": c.lon,
        "lat": c.lat,
        "offsetM": round(c.offset_m, 3),
        "forwardAllowed": c.forward_allowed,
        "reverseAllowed": c.reverse_allowed,
        "oneway": c.oneway,
        "stableKey": c.stable_key,
    }


def as_dict(r: SnapResult) -> dict:
    """API shape for a snap request."""
    return {
        "snapshotId": r.snapshot_id,
        "query": {"x": round(r.query_x, 3), "y": round(r.query_y, 3)},
        "searchRadiusM": r.search_radius_m,
        "profile": r.profile,
        "found": r.found,
        "handle": candidate_as_dict(r.chosen) if r.chosen else None,
        "candidates": [candidate_as_dict(c) for c in r.candidates],
        "ambiguous": r.ambiguous,
        "ambiguityReason": r.ambiguity_reason,
        "snapModelVersion": SNAP_MODEL_VERSION,
    }
