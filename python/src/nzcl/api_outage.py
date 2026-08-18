"""HTTP surface for the two-point outage span, kept in its own router.

WHY A SEPARATE MODULE
---------------------
`api.py` is 1,468 lines carrying the V1 and V2 contracts that the browser
suite, the TypeScript cross-check and the export pipeline all read. This
feature is a draft on a branch that must not disturb any of them, and it lands
while two other pull requests are moving. A router in its own file touches
`api.py` in exactly one place - the line that includes it - so the diff a
reviewer of those other branches has to reconcile is one line, not a thousand.

It is also OFF by default. `enable_outage_span_api` gates inclusion, matching
`VITE_ENABLE_OUTAGE_SPAN_EDITOR` on the client, so a deployment that has not
opted in serves exactly what it served before.

EVERYTHING IS GET
-----------------
Every endpoint here is addressable, which is the same requirement the rest of
this application meets: a span is a thing you can send someone. It also means
the CORS policy stays `allow_methods=["GET"]` rather than being widened for a
draft feature.

A handle is addressed by LINEAR REFERENCE - link and fraction - never by the
click that produced it. A permalink carrying a click would re-snap against
whatever the map looked like when it was reopened, which is how a shared span
comes to describe a different road.

WHAT IS NOT HERE
----------------
No caching. `detour_results` is keyed on a link id and has no column for a
partial span, and adding one means a numbered migration - which this branch
must not do until PR #10's migrations are settled. Every request is computed.
Latency is reported per request so the decision to cache can be made on
measurements rather than on a guess.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from . import db, geo, outage, snap, span_corridor
from .outage import HandleRef
from .routing import Metric, Profile
from .span_corridor import HandleOption
from .vsplit import DirectionMode

router = APIRouter(prefix="/api/v2/outage", tags=["outage span (draft)"])

#: Cap on how much geometry one request may ask to be drawn. A span across a
#: city would otherwise return a route with thousands of segments to a client
#: that only wanted the number.
MAX_DRAWN_ARCS = 2_000


def active_snapshot() -> str:
    """The snapshot the API is serving.

    A dependency rather than a direct call so tests can override it without
    starting the application's lifespan, which selects a snapshot from whatever
    happens to be in the database.
    """
    from .api import snapshot_id
    return snapshot_id()


def _provenance(snapshot: str) -> dict[str, Any]:
    """Attribution and limitations, on the same terms as the V2 endpoints.

    Read from the snapshot row rather than from `api._ACTIVE`, so this router
    stays independent of the application's module state - which is what lets
    it be mounted on a bare app and tested without running the lifespan.

    `limitations` travels with every figure on purpose: a number should not be
    liftable out of this API without the caveats that belong to it.
    """
    from .config import LIMITATIONS

    row = db.query_one(
        "SELECT attribution FROM network_snapshots WHERE snapshot_id=%s",
        (snapshot,))
    return {
        "attribution": (row or {}).get("attribution"),
        "limitations": LIMITATIONS,
    }


def _nztm(x: float | None, y: float | None, lon: float | None,
          lat: float | None) -> tuple[float, float]:
    """Accept a click in either frame, and measure in the projected one.

    The map speaks WGS84 and every distance in this system is NZTM metres.
    Converting here, once, keeps the rule that nothing measures against
    degrees.
    """
    if x is not None and y is not None:
        return float(x), float(y)
    if lon is not None and lat is not None:
        return geo.lonlat_to_nztm(float(lon), float(lat))
    raise HTTPException(
        422, "supply either x and y (EPSG:2193) or lon and lat (EPSG:4326)")


def _handle_options(link_id: int, fraction: float,
                    alternates: list[str] | None) -> list[HandleOption]:
    """A handle plus the other links legitimately occupying its coordinate.

    Alternates arrive as `linkId:fraction`. They exist because a click at a
    crossroads snaps to the identical point on several roads, and which one the
    handle belongs to decides which road the outage runs along - so the client
    hands back everything the snap offered rather than having chosen already.
    """
    options = [HandleOption(link_id, fraction)]
    for raw in alternates or []:
        try:
            other_link, other_fraction = raw.split(":", 1)
            options.append(HandleOption(int(other_link), float(other_fraction)))
        except (ValueError, TypeError):
            raise HTTPException(
                422, f"alternate {raw!r} is not in the form linkId:fraction")
    return options


@router.get("/snap")
def snap_point(
    x: float | None = None,
    y: float | None = None,
    lon: float | None = None,
    lat: float | None = None,
    vehicle: Profile = "car",
    radius: float = Query(snap.DEFAULT_SEARCH_RADIUS_M, gt=0,
                          le=snap.MAX_SEARCH_RADIUS_M),
    snapshot: str = Depends(active_snapshot),
) -> dict[str, Any]:
    """Resolve a click onto the nearest road centreline.

    The response separates two kinds of rival deliberately. `equivalentHosts`
    are at the SAME coordinate on different links - a crossroads - and are not
    a question for the user, but every one of them must be handed back with the
    corridor request. `alternatives` are somewhere else, and are what
    `ambiguous` is computed from: two carriageways of a divided road, where
    choosing by a metre chooses by pointing noise.
    """
    px, py = _nztm(x, y, lon, lat)
    try:
        result = snap.snap(snapshot, px, py, profile=vehicle,
                           search_radius_m=radius)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return snap.as_dict(result)


@router.get("/corridor")
def corridor(
    aLink: int,
    aFraction: float = Query(..., ge=0.0, le=1.0),
    bLink: int = Query(...),
    bFraction: float = Query(..., ge=0.0, le=1.0),
    aAlt: list[str] | None = Query(None),
    bAlt: list[str] | None = Query(None),
    vehicle: Profile = "car",
    snapshot: str = Depends(active_snapshot),
) -> dict[str, Any]:
    """Which roads the outage between these two handles could run along.

    Up to three, ranked on evidence a reader could check, with `ambiguous` set
    when the evidence does not separate the top two. The client draws the
    chosen corridor in red before any analysis runs, so what is about to be
    closed is visible before it is measured.
    """
    try:
        choice = span_corridor.select(
            snapshot,
            _handle_options(aLink, aFraction, aAlt),
            _handle_options(bLink, bFraction, bAlt),
            profile=vehicle)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    body = span_corridor.as_dict(choice)
    body["snapshotId"] = snapshot
    body.update(_provenance(snapshot))
    return body


@router.get("/analysis")
def analysis(
    aLink: int,
    aFraction: float = Query(..., ge=0.0, le=1.0),
    bLink: int = Query(...),
    bFraction: float = Query(..., ge=0.0, le=1.0),
    aAlt: list[str] | None = Query(None),
    bAlt: list[str] | None = Query(None),
    corridorId: str | None = None,
    direction: DirectionMode = "both",
    vehicle: Profile = "car",
    metric: Metric = "distance",
    geometry: bool = False,
    snapshot: str = Depends(active_snapshot),
) -> dict[str, Any]:
    """Close the span and measure the way round.

    `corridorId` pins which corridor to use and is what a permalink carries.
    An id that is no longer among the candidates is a 409 rather than a quiet
    substitution: reopening a shared span onto a different road would be worse
    than failing to open it, because nothing would say so.
    """
    a_options = _handle_options(aLink, aFraction, aAlt)
    b_options = _handle_options(bLink, bFraction, bAlt)

    try:
        result = outage.analyse(
            snapshot,
            HandleRef(aLink, aFraction), HandleRef(bLink, bFraction),
            profile=vehicle, metric=metric, direction_mode=direction,
            corridor_id=corridorId,
            a_alternates=[HandleRef(o.link_id, o.fraction)
                          for o in a_options[1:]],
            b_alternates=[HandleRef(o.link_id, o.fraction)
                          for o in b_options[1:]])
    except outage.UnknownCorridor as exc:
        raise HTTPException(409, str(exc))
    except outage.NoCorridor as exc:
        raise HTTPException(422, str(exc))
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    # Only a measure that RESOLVED contributes geometry. A withheld route -
    # one that crosses a banned manoeuvre across a split link - has arcs, and
    # drawing them would put a line on the map for a route the engine has just
    # refused to offer.
    drawn = sum(len(m.arc_ids) for m in result.measures if m.routed)
    if geometry and drawn > MAX_DRAWN_ARCS:
        body = outage.as_dict(result, with_geometry=False)
        body["geometryOmitted"] = {
            "reason": (
                f"the replacement paths total {drawn} arcs, over the "
                f"{MAX_DRAWN_ARCS} this endpoint will draw"),
            "arcCount": drawn,
        }
        body.update(_provenance(snapshot))
        return body

    body = outage.as_dict(result, with_geometry=geometry)
    body.update(_provenance(snapshot))
    return body
