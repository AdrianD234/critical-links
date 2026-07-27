"""Detour analysis API (FastAPI).

    uvicorn nzcl.api:app --port 8000

Every response carries its provenance - snapshot id, source dataset, retrieval
time, attribution - and the limitations that apply to the numbers in it, so a
figure cannot be lifted out of the UI without the caveats that belong to it.

The response contract matches the TypeScript service it replaces, so the
existing React/MapLibre client runs against either unchanged. Vector tiles come
from PostGIS `ST_AsMVT` rather than a separate tiling library.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import ALGORITHM, ALGORITHM_VERSION, LIMITATIONS, get_settings
from .detour import compute
from .routing import Metric, Profile

settings = get_settings()

_ACTIVE: dict[str, Any] = {}


def _latest_snapshot() -> str | None:
    row = db.query_one(
        "SELECT snapshot_id FROM network_snapshots WHERE status <> 'failed' "
        "ORDER BY retrieved_at_utc DESC LIMIT 1"
    )
    return row["snapshot_id"] if row else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    snap = os.environ.get("SNAPSHOT_ID") or _latest_snapshot()
    if not snap:
        raise RuntimeError(
            "No snapshots in the database. Run: nzcl-ingest --pilot wellington"
        )
    _ACTIVE["snapshot_id"] = snap
    meta = db.query_one(
        "SELECT * FROM network_snapshots WHERE snapshot_id=%s", (snap,))
    _ACTIVE["meta"] = meta
    print(f"active snapshot: {snap} ({meta['routable_link_count']} links)")
    yield
    db.close_pool()


app = FastAPI(
    title="NZ Road Criticality and Detour API",
    version="2.0.0",
    description=(
        "Structural road-network resilience over the NZTA AMDS Network Model. "
        "Returns shortest replacement paths for a closed road link. This is NOT "
        "a traffic assignment model: it does not predict traffic volumes on "
        "alternative routes."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["GET"],
    allow_headers=["*"],
)


def snapshot_id() -> str:
    return _ACTIVE["snapshot_id"]


def _round(v: float | None, dp: int) -> float | None:
    return None if v is None else round(v, dp)


def _provenance() -> dict[str, Any]:
    m = _ACTIVE["meta"]
    return {
        "snapshotId": m["snapshot_id"],
        "sourceDataset": m["source_dataset"],
        "sourceUrl": m["source_url"],
        "retrievedAtUtc": m["retrieved_at_utc"].isoformat(),
        "licence": m["licence"],
        "attribution": m["attribution"],
        "processingVersion": m["processing_version"],
        "algorithm": ALGORITHM,
        "algorithmVersion": ALGORITHM_VERSION,
        "snapshotStatus": m["status"],
        "clippedExtract": m["extent_2193"] is not None,
    }


STATUS_MEANING = {
    "OK": "A valid replacement path was found.",
    "DISCONNECTED": (
        "No replacement path exists between the closed link's own endpoints. "
        "Check the corridor and isolation fields before concluding traffic "
        "cannot get past: on a one-way carriageway this result is routine and "
        "does not mean the area is cut off."
    ),
    "UNRESOLVED_TIMEOUT": (
        "The search exceeded its budget. This is NOT a finding about the "
        "network - the answer is unknown."
    ),
    "INVALID_GRAPH": "The request referenced nodes outside the graph.",
    "SOURCE_DATA_ERROR": (
        "The source link cannot be routed on (for example it starts and ends "
        "at the same node)."
    ),
    "UNSUPPORTED_PROFILE": "The requested vehicle profile cannot use this link.",
    "API_ERROR": (
        "An application error occurred. This is not a statement about the network."
    ),
}


#: Bumped when the tile property contract changes. Part of the tile URL so a
#: cached tile can never be reinterpreted under a new schema.
TILE_SCHEMA_VERSION = 2


def _build_info() -> dict[str, str | None]:
    """Git commit and branch, so a running service is identifiable.

    Prefers values injected by the launcher. Asking git directly from the
    server process is unreliable: the service may run as a different user than
    the one that owns the checkout, and git then refuses with "dubious
    ownership" and returns nothing - which looks identical to "not a repo".
    """
    import os
    import subprocess
    from pathlib import Path

    injected = {
        "commit": os.environ.get("NZCL_BUILD_COMMIT"),
        "branch": os.environ.get("NZCL_BUILD_BRANCH"),
        "timestamp": os.environ.get("NZCL_BUILD_TIMESTAMP"),
    }
    if injected["commit"]:
        return {**injected, "source": "launcher"}

    repo = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() or None
        except Exception:  # noqa: BLE001
            return None

    out = {
        "commit": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "timestamp": git("log", "-1", "--format=%cI"),
    }
    # Say why it is unknown rather than reporting a bare null.
    out["source"] = "git" if out["commit"] else "unavailable"
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus enough identity to prove WHICH backend answered.

    Two implementations of this API exist in the repository. Without an
    explicit implementation marker there is no way to tell from a response, or
    a screenshot, which one served it.
    """
    m = _ACTIVE["meta"]
    return {
        "status": "ok",
        "implementation": "python-fastapi-postgis",
        "build": _build_info(),
        "algorithm": ALGORITHM,
        "algorithmVersion": ALGORITHM_VERSION,
        "processingVersion": m["processing_version"],
        "tileSchemaVersion": TILE_SCHEMA_VERSION,
        "activeSnapshotId": m["snapshot_id"],
        "activeSnapshotStatus": m["status"],
        # Retained for backwards compatibility with the existing client.
        "snapshotId": m["snapshot_id"],
        "links": m["routable_link_count"],
        "arcs": m["arc_count"],
        "nodes": m["node_count"],
        "database": db.server_versions(),
    }


@app.get("/api/v1/network/snapshots")
def snapshots() -> dict[str, Any]:
    rows = db.query(
        "SELECT snapshot_id, status, retrieved_at_utc, routable_link_count "
        "FROM network_snapshots ORDER BY retrieved_at_utc DESC"
    )
    return {
        "available": [r["snapshot_id"] for r in rows],
        "active": snapshot_id(),
        "detail": [
            {**r, "retrieved_at_utc": r["retrieved_at_utc"].isoformat()} for r in rows
        ],
    }


@app.get("/api/v1/network/metadata")
def metadata() -> dict[str, Any]:
    m = _ACTIVE["meta"]
    snap = snapshot_id()
    counts = db.query_one(
        """
        SELECT (SELECT count(*) FROM links WHERE snapshot_id=%(s)s) AS links,
               (SELECT count(*) FROM arcs  WHERE snapshot_id=%(s)s) AS arcs,
               (SELECT count(*) FROM nodes WHERE snapshot_id=%(s)s) AS nodes,
               (SELECT count(DISTINCT component_id) FROM nodes
                 WHERE snapshot_id=%(s)s) AS components,
               (SELECT count(*) FROM turn_restrictions
                 WHERE snapshot_id=%(s)s) AS restrictions
        """,
        {"s": snap},
    )
    extent = db.query_one(
        "SELECT ST_XMin(e) AS xmin, ST_YMin(e) AS ymin, ST_XMax(e) AS xmax, "
        "ST_YMax(e) AS ymax, ST_XMin(w) AS lonmin, ST_YMin(w) AS latmin, "
        "ST_XMax(w) AS lonmax, ST_YMax(w) AS latmax FROM ("
        "  SELECT analysis_extent_2193 AS e, ST_Transform(analysis_extent_2193, 4326) AS w"
        "  FROM network_snapshots WHERE snapshot_id=%s) q",
        (snap,),
    )
    return {
        **_provenance(),
        "where": m["where_clause"],
        "sourceFeatureCount": m["source_feature_count"],
        "downloadedFeatureCount": m["downloaded_feature_count"],
        "tileSchemaVersion": TILE_SCHEMA_VERSION,
        "graph": {
            "links": counts["links"],
            "arcs": counts["arcs"],
            "nodes": counts["nodes"],
            "components": counts["components"],
            "turnRestrictions": counts["restrictions"],
        },
        "analysisExtentWgs84": (
            {
                "southWest": {"lat": extent["latmin"], "lon": extent["lonmin"]},
                "northEast": {"lat": extent["latmax"], "lon": extent["lonmax"]},
            }
            if extent and extent["lonmin"] is not None
            else None
        ),
        "ingestNotes": list(m["notes"] or []),
        "limitations": LIMITATIONS,
    }


def _link_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "linkId": row["link_id"],
        "amdsId": row["amds_id"],
        "sourceObjectId": row.get("source_object_id"),
        "closureGroupId": row["closure_group_id"],
        "roadName": row.get("road_name"),
        "modelAssetTypeName": {1: "Roadway", 6: "Connector"}.get(
            row.get("model_asset_type"), f"type {row.get('model_asset_type')}"),
        "surfaceTypeName": {1: "Sealed", 2: "Metalled", 3: "Unsurfaced"}.get(
            row.get("surface_type"), f"type {row.get('surface_type')}"),
        "assetOwnerOrganisation": row.get("rca_code"),
        "rca": row.get("rca_name") or (
            "NZTA Waka Kotahi (state highway)" if row.get("rca_code") == 1 else None),
        "lengthM": _round(row["length_m"], 1),
        "oneway": row.get("oneway") == 1,
        "forwardAllowed": row["forward_allowed"],
        "reverseAllowed": row["reverse_allowed"],
        "modeVehicleHeavy": row["mode_vehicle_heavy"],
        "modeEmergency": row["mode_emergency"],
        "lifeLineRoute": row["lifeline_route"],
        "speedKph": row.get("speed_kph"),
        "speedSource": row["speed_source"],
        "qualityFlags": list(row.get("quality_flags") or []),
        "centroid": {"lat": row.get("clat"), "lon": row.get("clon")},
        "inAnalysisArea": row["in_analysis_area"],
    }


_LINK_COLUMNS = (
    "l.*, ST_Y(ST_Transform(ST_LineInterpolatePoint(l.geom_2193, 0.5), 4326)) AS clat, "
    "ST_X(ST_Transform(ST_LineInterpolatePoint(l.geom_2193, 0.5), 4326)) AS clon"
)


@app.get("/api/v1/links/search")
def search(
    name: str | None = None,
    amdsId: str | None = None,
    rca: int | None = None,
    bbox: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    clauses = ["l.snapshot_id = %(snap)s"]
    params: dict[str, Any] = {"snap": snapshot_id(), "limit": limit}
    if name:
        clauses.append("l.road_name ILIKE %(name)s")
        params["name"] = f"%{name}%"
    if amdsId:
        clauses.append("l.amds_id LIKE %(amds)s")
        params["amds"] = f"%{amdsId}%"
    if rca is not None:
        clauses.append("l.rca_code = %(rca)s")
        params["rca"] = rca
    if bbox:
        try:
            minlon, minlat, maxlon, maxlat = (float(p) for p in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
        clauses.append(
            "ST_Intersects(l.geom_4326, ST_MakeEnvelope("
            "%(minlon)s,%(minlat)s,%(maxlon)s,%(maxlat)s,4326))"
        )
        params |= {"minlon": minlon, "minlat": minlat,
                   "maxlon": maxlon, "maxlat": maxlat}

    rows = db.query(
        f"SELECT {_LINK_COLUMNS} FROM links l WHERE {' AND '.join(clauses)} "
        f"ORDER BY l.length_m DESC LIMIT %(limit)s",
        params,
    )
    return {
        "snapshotId": snapshot_id(),
        "count": len(rows),
        "truncated": len(rows) >= limit,
        "results": [_link_summary(r) for r in rows],
    }


def _resolve(link_ref: str) -> dict[str, Any]:
    snap = snapshot_id()
    row = db.query_one(
        f"SELECT {_LINK_COLUMNS} FROM links l WHERE l.snapshot_id=%s AND l.amds_id=%s",
        (snap, link_ref),
    )
    if row is None and link_ref.lstrip("-").isdigit():
        row = db.query_one(
            f"SELECT {_LINK_COLUMNS} FROM links l WHERE l.snapshot_id=%s "
            f"AND l.link_id=%s", (snap, int(link_ref)))
    if row is None:
        raise HTTPException(404, f"unknown link {link_ref!r}")
    return row


@app.get("/api/v1/links/{link_ref:path}/detour")
def detour(
    link_ref: str,
    metric: Metric = "distance",
    vehicle: Profile = "car",
    closure_scope: Literal["physical", "directed"] = "physical",
    direction: Literal["forward", "reverse", "both"] = "both",
    geometry: bool = True,
) -> dict[str, Any]:
    link = _resolve(link_ref)
    snap = snapshot_id()

    directions = None
    if direction != "both":
        directions = [direction]

    try:
        result = compute(snap, link["link_id"], metric=metric, profile=vehicle,
                         closure_scope=closure_scope, directions=directions)
    except KeyError as exc:
        raise HTTPException(404, str(exc))

    def route_geojson(arc_ids: list[int]) -> dict[str, Any] | None:
        if not geometry or not arc_ids:
            return None
        rows = db.query(
            """
            SELECT a.arc_id, a.link_id, a.direction, l.amds_id, l.road_name,
                   l.length_m,
                   ST_AsGeoJSON(CASE WHEN a.direction='reverse'
                                     THEN ST_Reverse(l.geom_4326)
                                     ELSE l.geom_4326 END, 7) AS geom
            FROM arcs a JOIN links l
              ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id
            WHERE a.snapshot_id=%s AND a.arc_id = ANY(%s)
            """,
            (snap, arc_ids),
        )
        import json
        by_id = {r["arc_id"]: r for r in rows}
        feats = []
        for order, aid in enumerate(arc_ids):
            r = by_id.get(aid)
            if not r:
                continue
            feats.append({
                "type": "Feature",
                "geometry": json.loads(r["geom"]),
                "properties": {
                    "order": order, "arcId": aid, "linkId": r["link_id"],
                    "amdsId": r["amds_id"], "roadName": r["road_name"],
                    "lengthM": _round(r["length_m"], 1),
                    "direction": r["direction"],
                },
            })
        return {"type": "FeatureCollection", "features": feats}

    def serialise(d) -> dict[str, Any] | None:
        if d is None:
            return None
        return {
            "direction": d.direction,
            "status": d.status,
            "statusMeaning": STATUS_MEANING.get(d.status, ""),
            "sourceNode": d.source_node,
            "targetNode": d.target_node,
            "metrics": {
                "selectedLinkLengthM": _round(d.selected_link_length_m, 1),
                "normalPathDistanceM": _round(d.normal_path_distance_m, 1),
                "alternativeDistanceM": _round(d.alternative_distance_m, 1),
                "addedDistanceVsLinkM": _round(d.added_distance_vs_link_m, 1),
                "networkPenaltyM": _round(d.network_penalty_m, 1),
                "detourRatioVsLink": _round(d.detour_ratio_vs_link, 3),
                "normalPathTimeS": _round(d.normal_path_time_s, 1),
                "alternativeTimeS": _round(d.alternative_time_s, 1),
                "addedTimeS": _round(d.added_time_s, 1),
                "units": {"distance": "metres", "time": "seconds"},
            },
            "corridor": None if d.corridor is None else {
                "status": d.corridor.status,
                "hopsUpstream": d.corridor.hops_upstream,
                "hopsDownstream": d.corridor.hops_downstream,
                "normalDistanceM": _round(d.corridor.normal_distance_m, 1),
                "alternativeDistanceM": _round(d.corridor.alternative_distance_m, 1),
                "penaltyM": _round(d.corridor.penalty_m, 1),
                "penaltyTimeS": _round(d.corridor.penalty_time_s, 1),
                "truncated": d.corridor.truncated,
                "exitReachable": d.corridor.exit_reachable,
                "meaning": (
                    "Through-trip comparison between the nearest upstream and "
                    "downstream points at which a driver has a choice. Reported "
                    "when the link-endpoint measure is undefined, which is "
                    "routine on one-way carriageways."
                ),
            },
            "isolation": None if d.isolation is None else {
                "side": d.isolation.side,
                "pocketNodeCount": d.isolation.pocket_node_count,
                "pocketLinkCount": d.isolation.pocket_link_count,
                "pocketLengthM": _round(d.isolation.pocket_length_m, 1),
                "bounded": d.isolation.bounded,
                "exact": d.isolation.exact,
            },
            "removedArcIds": d.removed_arc_ids,
            "routeArcIds": d.route_arc_ids,
            "routeLinkIds": d.route_link_ids,
            "routeGeoJson": route_geojson(d.route_arc_ids),
            "qualityFlags": d.quality_flags,
            "errorDetail": d.error_detail,
            "runtimeMs": d.runtime_ms,
            "usedExpandedGraph": d.used_expanded_graph,
        }

    import json
    closed = db.query(
        "SELECT link_id, amds_id, road_name, ST_AsGeoJSON(geom_4326, 7) AS geom "
        "FROM links WHERE snapshot_id=%s AND link_id = ANY(%s)",
        (snap, result.removed_link_ids or [-1]),
    )
    closed_features = [
        {"type": "Feature", "geometry": json.loads(r["geom"]),
         "properties": {"role": "closed", "linkId": r["link_id"],
                        "amdsId": r["amds_id"], "roadName": r["road_name"]}}
        for r in closed
    ]

    fwd, rev = serialise(result.forward), serialise(result.reverse)
    all_arcs = (result.forward.route_arc_ids if result.forward else []) + \
               (result.reverse.route_arc_ids if result.reverse else [])
    bounds = db.query_one(
        """
        SELECT ST_XMin(g) AS w, ST_YMin(g) AS s, ST_XMax(g) AS e, ST_YMax(g) AS n
        FROM (
          SELECT ST_Extent(l.geom_4326) AS g FROM links l
          WHERE l.snapshot_id=%s AND (l.link_id = ANY(%s) OR l.link_id IN (
            SELECT link_id FROM arcs WHERE snapshot_id=%s AND arc_id = ANY(%s)))
        ) q
        """,
        (snap, result.removed_link_ids or [-1], snap, all_arcs or [-1]),
    )

    return {
        **_provenance(),
        "request": {"metric": metric, "vehicle": vehicle,
                    "closureScope": closure_scope,
                    "directions": [d for d in ("forward", "reverse")
                                   if (fwd if d == "forward" else rev)]},
        "cached": False,
        "calculatedAtUtc": result.calculated_at_utc,
        "selectedLink": _link_summary(link),
        "closure": {
            "scope": closure_scope,
            "closureGroupId": result.closure_group_id,
            "removedLinkCount": len(result.removed_link_ids),
            "removedArcCount": len(result.removed_arc_ids),
            "removedLinkIds": result.removed_link_ids,
            "removedAmdsIds": result.removed_amds_ids,
            "geoJson": {"type": "FeatureCollection", "features": closed_features},
        },
        "forward": fwd,
        "reverse": rev,
        "fitBounds": ([bounds["w"], bounds["s"], bounds["e"], bounds["n"]]
                      if bounds and bounds["w"] is not None else None),
        "limitations": LIMITATIONS,
        "permalink": (
            f"{settings.application_base_url}/?link={link['amds_id']}"
            f"&snapshot={snap}&metric={metric}&vehicle={vehicle}"
            f"&scope={closure_scope}&direction={direction}"
        ),
    }


@app.get("/api/v1/qa/summary")
def qa_summary() -> dict[str, Any]:
    snap = snapshot_id()
    issues = db.query(
        "SELECT severity, issue_type, count, detail, sample_ids FROM qa_issues "
        "WHERE snapshot_id=%s ORDER BY CASE severity WHEN 'error' THEN 0 "
        "WHEN 'warning' THEN 1 ELSE 2 END, count DESC",
        (snap,),
    )
    if not issues:
        return {"error": "QA report not generated for this snapshot",
                "hint": f"run: nzcl-qa {snap}"}
    return {"snapshotId": snap, "issues": issues}


# ------------------------------------------------------------- vector tiles
@app.get("/tiles/v{schema}/{snapshot}/{z}/{x}/{y}.pbf")
def tile_versioned(
    schema: int, snapshot: str, z: int, x: int, y: int, request: Request
) -> Response:
    """Snapshot- and schema-addressed tile.

    The schema version and snapshot are in the PATH, not just advertised in
    /health. A tile cached for an hour under a bare `/tiles/{z}/{x}/{y}` would
    otherwise be reinterpreted after a snapshot or schema change - serving stale
    geometry, or property names the client no longer reads, with no way for a
    browser to know. Encoding both makes a stale tile unreachable rather than
    wrong, which is what lets the response be cached aggressively.
    """
    if schema != TILE_SCHEMA_VERSION:
        raise HTTPException(
            404,
            f"tile schema v{schema} is not served; current is v{TILE_SCHEMA_VERSION}",
        )
    known = db.query_one(
        "SELECT 1 AS ok FROM network_snapshots WHERE snapshot_id=%s", (snapshot,)
    )
    if not known:
        raise HTTPException(404, f"unknown snapshot {snapshot!r}")
    return _render_tile(z, x, y, snapshot, request)


@app.get("/tiles/{z}/{x}/{y}.pbf")
def tile(z: int, x: int, y: int, request: Request) -> Response:
    """Unversioned tile against the active snapshot.

    Retained for convenience and ad-hoc inspection. It is deliberately NOT
    cacheable: without a snapshot or schema in the URL there is no safe way to
    let a cache keep it.
    """
    return _render_tile(z, x, y, snapshot_id(), request, cacheable=False)


def _render_tile(
    z: int, x: int, y: int, snapshot: str, request: Request,
    cacheable: bool = True,
) -> Response:
    """Mapbox Vector Tile of the routable network, straight from PostGIS.

    ST_AsMVT keeps the whole tile pipeline in the database: no separate tiling
    service, and the tile is always consistent with the data being routed on.
    """
    if not (0 <= z <= 22) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
        raise HTTPException(400, "tile coordinates out of range")

    # Property names are camelCase to match what the MapLibre client reads.
    # They were snake_case until a decoded-tile test caught it: the client
    # reads feature.properties.linkId and styles on stateHighway, so a
    # snake_case tile made every map click resolve undefined and silently
    # dropped state-highway styling. The style-spec test could not catch this
    # because it validates the style, never a real tile.
    #
    # Candidate selection filters on the NZTM geometry against a reprojected
    # tile envelope, so the existing 2193 GiST index does the work; only the
    # surviving candidates are transformed for output.
    row = db.query_one(
        """
        WITH bounds AS (
            SELECT ST_TileEnvelope(%(z)s, %(x)s, %(y)s) AS merc,
                   ST_Transform(ST_TileEnvelope(%(z)s, %(x)s, %(y)s), 2193) AS nztm
        ),
        src AS (
            -- link_id is selected twice on purpose. ST_AsMVT REMOVES the
            -- feature-id column from the property bag, so publishing it only
            -- as the id would leave properties.linkId undefined - the exact
            -- bug this schema fixes. `mvtId` becomes feature.id (the robust
            -- handle, used for selection and feature-state) and `linkId`
            -- remains a property for debugging and interoperability.
            SELECT l.link_id                       AS "mvtId",
                   l.link_id                       AS "linkId",
                   l.amds_id                       AS "amdsId",
                   l.closure_group_id              AS "closureGroupId",
                   coalesce(l.road_name, '')       AS "roadName",
                   coalesce(l.road_number, '')     AS "roadNumber",
                   (l.oneway = 1)::int             AS oneway,
                   (l.rca_code = 1)::int           AS "stateHighway",
                   l.lifeline_route::int           AS lifeline,
                   l.in_analysis_area::int         AS core,
                   round(l.length_m)::int          AS "lengthM",
                   coalesce(l.model_asset_type, 0) AS "roadClass",
                   ST_AsMVTGeom(ST_Transform(l.geom_2193, 3857),
                                bounds.merc, 4096, 64, true) AS geom
            FROM links l, bounds
            WHERE l.snapshot_id = %(snap)s
              AND l.geom_2193 && bounds.nztm
        )
        -- Aggregate ORDER BY is load-bearing, not tidiness. ST_AsMVT encodes
        -- features in the order rows arrive, so without it an identical request
        -- can return different bytes (PostgreSQL is free to reorder, especially
        -- under parallel scan). That was observed: two identical requests
        -- produced different ETags, which makes caching and revalidation
        -- useless. Ordering makes a tile byte-reproducible for a given
        -- (schema, snapshot, z, x, y).
        SELECT ST_AsMVT(src, 'network', 4096, 'geom', 'mvtId'
                        ORDER BY src."linkId") AS mvt
        FROM src WHERE geom IS NOT NULL
        """,
        {"z": z, "x": x, "y": y, "snap": snapshot},
    )
    data = bytes(row["mvt"]) if row and row["mvt"] else b""
    if not data:
        return Response(status_code=204)

    # The tile is fully determined by (schema, snapshot, z, x, y), so a hash of
    # the bytes is a sound validator and lets a browser revalidate cheaply.
    import hashlib
    etag = '"' + hashlib.sha256(data).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    headers = {"ETag": etag}
    if cacheable:
        # Safe to cache hard: schema and snapshot are in the path, so this URL
        # can never come to mean something else.
        headers["Cache-Control"] = "public, max-age=86400, immutable"
    else:
        headers["Cache-Control"] = "no-cache"
    return Response(content=data, media_type="application/x-protobuf", headers=headers)


@app.get("/tiles/tilejson.json")
def tilejson(request: Request) -> dict[str, Any]:
    """TileJSON built from the REQUEST origin.

    Hard-coding localhost breaks the moment the API is reached through a proxy,
    a different port, or any host that is not the developer machine.
    """
    m = _ACTIVE["meta"]
    base = str(request.base_url).rstrip("/")
    return {
        "tilejson": "2.2.0",
        "name": f"AMDS routable network - {m['snapshot_id']}",
        "attribution": m["attribution"],
        "scheme": "xyz",
        "minzoom": 0,
        "maxzoom": 16,
        "tileSchemaVersion": TILE_SCHEMA_VERSION,
        "snapshotId": m["snapshot_id"],
        "tiles": [
            f"{base}/tiles/v{TILE_SCHEMA_VERSION}/{m['snapshot_id']}"
            + "/{z}/{x}/{y}.pbf"
        ],
    }
