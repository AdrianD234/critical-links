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
from typing import Any, Literal, get_args

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .config import (
    ALGORITHM,
    ALGORITHM_VERSION,
    LIMITATIONS,
    PROCESSING_VERSION,
    get_settings,
)
from .detour import compute
from .naming import display_label
from .routing import Metric, Profile

settings = get_settings()

# Closure scopes this build can honour, in the product's vocabulary rather than
# the wire enum.
#
# `segment` is deliberately absent. The engine removes every link derived from
# one AMDS source feature; it has no notion of a road segment independent of
# how AMDS happens to split it. Advertising the scope and silently falling back
# to whole-feature closure would report more of the network closed than the
# caller asked for, and label the answer as if it were what they requested.
SUPPORTED_CLOSURE_SCOPES = ["amds-feature", "direction"]

# Above this many stranded links the geometry is omitted from the response.
# The figures are still exact; only the drawing is skipped.
MAX_DRAWN_STRANDED_LINKS = 2_000

_ACTIVE: dict[str, Any] = {}


def _extent_wgs84(snapshot: str, column: str) -> dict[str, Any] | None:
    """A snapshot extent as a WGS84 corner pair, or None if it has none.

    A national snapshot has no extent - it is not clipped - and the caller is
    expected to fall back to the country's own bounds rather than treating the
    absence as an error.
    """
    if column not in {"display_extent_2193", "analysis_extent_2193", "extent_2193"}:
        raise ValueError(f"refusing to interpolate unknown column {column!r}")
    row = db.query_one(
        f"""
        SELECT ST_XMin(w) AS lonmin, ST_YMin(w) AS latmin,
               ST_XMax(w) AS lonmax, ST_YMax(w) AS latmax
          FROM (SELECT ST_Transform({column}, 4326) AS w
                  FROM network_snapshots WHERE snapshot_id = %s) q
        """,
        (snapshot,),
    )
    if not row or row["lonmin"] is None:
        return None
    return {
        "southWest": {"lat": row["latmin"], "lon": row["lonmin"]},
        "northEast": {"lat": row["latmax"], "lon": row["lonmax"]},
    }


def _default_snapshot() -> tuple[str | None, str]:
    """Choose the snapshot to serve, and say why.

    "Whichever non-failed snapshot was retrieved most recently" was too
    accidental to be a product default: a fresh pilot ingest, a partial run, or
    a CI fixture built on a developer's machine would all silently become what
    the application serves. That is how a national tool ended up presenting a
    Wellington extract as its normal view.

    The order is explicit instead:

      1. an operator-supplied SNAPSHOT_ID, which always wins;
      2. the newest COMPLETE national snapshot;
      3. a regional snapshot, only when no national one exists, and only as a
         clearly labelled fallback.

    A partial or failed snapshot is never chosen automatically, and a synthetic
    fixture never is at all - it exists for tests, and serving it to a person
    would present a seven-link toy as the road network.
    """
    national = db.query_one(
        """
        SELECT snapshot_id FROM network_snapshots
         WHERE status = 'complete' AND coverage_kind = 'national'
         ORDER BY retrieved_at_utc DESC LIMIT 1
        """
    )
    if national:
        return national["snapshot_id"], "newest complete national snapshot"

    regional = db.query_one(
        """
        SELECT snapshot_id FROM network_snapshots
         WHERE status = 'complete' AND coverage_kind = 'regional'
         ORDER BY retrieved_at_utc DESC LIMIT 1
        """
    )
    if regional:
        return regional["snapshot_id"], (
            "no national snapshot available; falling back to a regional extract"
        )

    # Nothing complete. Say so rather than quietly serving a partial ingest.
    any_snapshot = db.query_one(
        "SELECT snapshot_id, status, coverage_kind FROM network_snapshots "
        "WHERE status <> 'failed' ORDER BY retrieved_at_utc DESC LIMIT 1"
    )
    if any_snapshot:
        return any_snapshot["snapshot_id"], (
            f"no complete snapshot available; serving "
            f"{any_snapshot['coverage_kind'] or 'unknown'} snapshot with status "
            f"{any_snapshot['status']!r}"
        )
    return None, "no snapshots in the database"


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    explicit = os.environ.get("SNAPSHOT_ID")
    if explicit:
        snap, why = explicit, "SNAPSHOT_ID supplied explicitly"
    else:
        snap, why = _default_snapshot()

    if not snap:
        raise RuntimeError(
            "No snapshots in the database. Run: nzcl-ingest --national "
            "(or --pilot wellington for the fast validation extract)"
        )

    _ACTIVE["snapshot_id"] = snap
    meta = db.query_one(
        "SELECT * FROM network_snapshots WHERE snapshot_id=%s", (snap,))
    if not meta:
        raise RuntimeError(f"snapshot {snap!r} is not in the database")
    _ACTIVE["meta"] = meta
    _ACTIVE["selection_reason"] = why

    # Say which snapshot and why, at startup. A national tool quietly serving a
    # regional extract is the failure this line exists to make obvious.
    print(
        f"active snapshot: {snap} "
        f"[{meta.get('coverage_kind') or 'unknown'}: "
        f"{meta.get('coverage_name') or 'unnamed'}] "
        f"({meta['routable_link_count']} links) - {why}"
    )
    if (meta.get("coverage_kind") or "") != "national":
        print(
            "  WARNING: this is not national coverage. Results are limited to "
            "the extract, and a replacement path that would leave it cannot "
            "be found."
        )
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


def _name_attributions() -> list[dict[str, Any]]:
    """Attribution for every source whose names are actually being displayed.

    Read from the database rather than hard-coded, so a source cannot start
    appearing in the interface without its attribution appearing with it -
    which is the condition its licence is granted on.
    """
    try:
        rows = db.query(
            "SELECT source, licence, attribution FROM name_source_licences "
            " WHERE display_cleared AND attribution IS NOT NULL "
            " ORDER BY source")
    except Exception:  # noqa: BLE001 - a snapshot predating the naming layer
        return []
    return [{"source": r["source"], "licence": r["licence"],
             "attribution": r["attribution"]} for r in rows]


def _naming_coverage(snap: str) -> dict[str, Any] | None:
    """How many links have a name, where it came from, and what is held back.

    Reported so the interface can state its own naming coverage rather than
    leave a reader to infer it from how many labels they happen to see. The
    withheld figure is the one that matters most: those links have a name, and
    saying nothing about them would understate what is known and hide a
    decision that is waiting on someone.
    """
    try:
        rows = db.query(
            "SELECT name_status, count(*) AS n, "
            "       count(display_name) AS named FROM link_display_names "
            " WHERE snapshot_id = %s GROUP BY 1", (snap,))
        withheld = db.query(
            "SELECT withheld_name_source AS src, count(*) AS n "
            "  FROM link_display_names WHERE snapshot_id = %s "
            "   AND withheld_name_source IS NOT NULL GROUP BY 1", (snap,))
    except Exception:  # noqa: BLE001 - snapshot predating the naming layer
        return None
    if not rows:
        return None
    by_status = {r["name_status"]: r["n"] for r in rows}
    total = sum(by_status.values())
    # Counted, not derived by subtraction: a handful of designation-only rows
    # have no display string, and subtracting the states that "should" be
    # nameless silently counted them as named.
    named = sum(r["named"] for r in rows)
    return {
        "graphLinks": total,
        "namedLinks": named,
        "byStatus": by_status,
        "withheldBySource": {r["src"]: r["n"] for r in withheld},
        "withheldTotal": sum(r["n"] for r in withheld),
    }


def _provenance() -> dict[str, Any]:
    m = _ACTIVE["meta"]
    return {
        "snapshotId": m["snapshot_id"],
        "sourceDataset": m["source_dataset"],
        "sourceUrl": m["source_url"],
        "retrievedAtUtc": m["retrieved_at_utc"].isoformat(),
        "licence": m["licence"],
        "attribution": m["attribution"],
        # Road names can come from outside AMDS, and those sources carry their
        # own attribution requirements.
        "nameAttributions": _name_attributions(),
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
        # What this snapshot covers, recorded at ingest rather than inferred.
        #
        # The client used to derive coverage from `clippedExtract` and label
        # anything clipped "Wellington pilot" - which would have announced an
        # Auckland extract as Wellington, and could not tell a national
        # snapshot from a very large regional one.
        "coverage": {
            "kind": m.get("coverage_kind") or "unknown",
            "name": m.get("coverage_name") or "Unnamed extract",
            # Where the map should sit with nothing selected.
            "displayExtentWgs84": _extent_wgs84(snap, "display_extent_2193"),
            "isNational": (m.get("coverage_kind") or "") == "national",
        },
        "selectionReason": _ACTIVE.get("selection_reason"),
        "naming": _naming_coverage(snap),
        # What this build can actually do, so the client does not have to
        # hard-code the parameter enums.
        #
        # Two things depend on this. First, the closure-scope control is built
        # from `closureScopes` rather than a literal list, so adding segment
        # scope is a backend change that the existing frontend picks up without
        # another visual rewrite. Second, the version pair participates in the
        # client's cache key: when restriction semantics or segment scope land,
        # `algorithmVersion` changes and every figure computed under the old one
        # becomes unreachable rather than being redisplayed under new settings.
        "capabilities": {
            "closureScopes": SUPPORTED_CLOSURE_SCOPES,
            "metrics": list(get_args(Metric)),
            "vehicles": list(get_args(Profile)),
            "algorithmVersion": ALGORITHM_VERSION,
            "processingVersion": PROCESSING_VERSION,
        },
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


#: What the interface should put in the road-name position for each name state,
#: when there is no name to put there. Held here rather than in the client so
#: that the wording cannot drift from the classification it describes.
NAME_STATE_LABELS = {
    "officially_unnamed": "Unnamed road",
    "unresolved": "Name not recorded",
    "ambiguous_conflict": "Name disputed",
}

NAME_STATE_EXPLANATIONS = {
    "amds_named": "Named in the AMDS Network Model.",
    "route_designation_only": (
        "The source records the route this road carries, but no street name."
    ),
    "externally_enriched": "Name matched from an external authoritative source.",
    "officially_unnamed": (
        "An authoritative source records that this road has no name."
    ),
    "ambiguous_conflict": (
        "Sources hold more than one name for this road. Both are shown; "
        "neither has been chosen over the other."
    ),
    "unresolved": (
        "No source consulted holds a name for this road. This is not the same "
        "as the road having no name."
    ),
}


def _naming_block(row: dict[str, Any]) -> dict[str, Any]:
    """Where the displayed name came from, and what it means when there is none.

    `withheldSource` is the case worth reading carefully: a name IS known for
    the link, and it is not being shown because that source's licence has not
    been confirmed. Reporting it as simply unnamed would understate what the
    project knows and hide a decision that someone needs to make.
    """
    status = row.get("name_status") or (
        "amds_named" if row.get("road_name") else "unresolved")
    return {
        "status": status,
        "label": NAME_STATE_LABELS.get(status),
        "explanation": NAME_STATE_EXPLANATIONS.get(status),
        "source": row.get("name_source"),
        "confidence": row.get("name_confidence"),
        "routeDesignation": row.get("route_designation"),
        "alternates": list(row.get("name_alternates") or []),
        "conflict": bool(row.get("name_conflict")),
        "withheldSource": row.get("withheld_name_source"),
    }


#: Distance within which a LINZ road section is accepted as describing the
#: locality of a graph link. Wide enough to survive the centreline offset
#: between two independently maintained datasets, tight enough that it cannot
#: pick up the next settlement.
LOCALITY_SEARCH_M = 250.0


def _locality_lookup(snap: str, link_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Locality per link, from the nearest LINZ road section.

    LINZ Road Sections is the only locality source in the database whose
    licence is confirmed for display (CC BY 4.0, recorded in
    `name_source_licences`), so the clearance is checked here rather than
    assumed. If it is ever withdrawn, links lose their locality and fall back
    to an authority-based label - they do not silently keep publishing it.

    A nearest-neighbour lookup per link, bounded by `LOCALITY_SEARCH_M` so the
    GIST index does the work. This runs for a handful of links at a time - one
    selected road, or a page of search results - never for a tile.

    WHAT THE TWO LOCALITY FIELDS ACTUALLY ARE
    -----------------------------------------
    `locality` is LINZ's `leftlocalityname` and `locality_alt` is
    `rightlocalityname`: the localities on either SIDE of the road section, not
    a primary and a fallback. Where a road runs along a boundary they differ
    legitimately - State Highway 1 south of Tokoroa has Kinleith on one side
    and Tokoroa on the other, and neither is more correct than the other.

    The label takes the left-hand value, deterministically, and both are
    returned so the interface can show the pair. That is a defensible choice
    rather than a good one: a road-section side locality is not the same thing
    as "the nearest named place", which is what a reader hears. A place-point
    gazetteer would answer the question properly and is not in this database.
    Recorded as a limitation rather than papered over.

    `ORDER BY ... <-> ..., feature_id, part` - the tiebreak is not decoration.
    Two sections at the same distance must not be able to swap places between
    runs and change a label.
    """
    if not link_ids:
        return {}
    cleared = db.query_one(
        "SELECT display_cleared FROM name_source_licences WHERE source=%s",
        ("linz_road_sections",))
    if not cleared or not cleared["display_cleared"]:
        return {}

    rows = db.query(
        """
        SELECT l.link_id, e.locality, e.locality_alt, e.territorial_authority
          FROM links l
     CROSS JOIN LATERAL (
              SELECT x.locality, x.locality_alt, x.territorial_authority
                FROM ext_road_names x
               WHERE x.source = 'linz_road_sections'
                 AND x.locality IS NOT NULL
                 AND ST_DWithin(x.geom_2193, l.geom_2193, %s)
            ORDER BY x.geom_2193 <-> l.geom_2193, x.feature_id, x.part
               LIMIT 1
          ) e
         WHERE l.snapshot_id = %s AND l.link_id = ANY(%s)
        """,
        (LOCALITY_SEARCH_M, snap, link_ids),
    )
    return {int(r["link_id"]): r for r in rows}


def _label_block(row: dict[str, Any], locality: dict[str, Any] | None) -> dict[str, Any]:
    """The one authoritative label, plus the fields it was derived from.

    The client is given both. It renders `displayLabel` and never rebuilds it;
    the separate fields exist so a provenance panel can show what is known
    without re-deriving the decision, and so two links that both read
    "State-highway section near Tokoroa" are still distinguishable.
    """
    loc = (locality or {}).get("locality") or None
    lab = display_label(
        road_name=(row["display_name"] if "display_name" in row
                   else row.get("road_name")),
        route_designation=row.get("route_designation"),
        name_status=row.get("name_status"),
        withheld_source=row.get("withheld_name_source"),
        rca_code=row.get("rca_code"),
        rca_name=row.get("rca_name"),
        locality=loc,
        amds_id=row.get("amds_id"),
        link_id=row.get("link_id"),
    )
    return {
        "displayLabel": lab.label,
        "displayLabelKind": lab.kind,
        "displayLabelBasis": lab.basis,
        "displayLabelSecondary": lab.secondary,
        "locality": loc,
        "localityAlt": (locality or {}).get("locality_alt"),
        "territorialAuthority": (locality or {}).get("territorial_authority"),
    }


def _link_summary(row: dict[str, Any],
                  locality: dict[str, Any] | None = None) -> dict[str, Any]:
    label = _label_block(row, locality)
    return {
        "linkId": row["link_id"],
        # The authoritative label. The map chip and the inspector headline both
        # read THIS; neither collapses a name state to "No name" any more.
        **label,
        "amdsId": row["amds_id"],
        "sourceObjectId": row.get("source_object_id"),
        "closureGroupId": row["closure_group_id"],
        # The naming layer decides this, and it may legitimately be null. The
        # `naming` block below says WHY it is null, which is the difference
        # between "we could not find a name" and "this road has none".
        # The view is authoritative when it is joined, INCLUDING when it says
        # NULL - that is how a withheld name stays withheld. Falling back to
        # links.road_name here would route around the licence gate.
        "roadName": (row["display_name"] if "display_name" in row
                     else row.get("road_name")),
        "naming": _naming_block(row),
        "modelAssetTypeName": {1: "Roadway", 6: "Connector"}.get(
            row.get("model_asset_type"), f"type {row.get('model_asset_type')}"),
        "surfaceTypeName": {1: "Sealed", 2: "Metalled", 3: "Unsurfaced"}.get(
            row.get("surface_type"), f"type {row.get('surface_type')}"),
        "assetOwnerOrganisation": row.get("rca_code"),
        "rca": row.get("rca_name") or (
            "NZTA Waka Kotahi (state highway)" if row.get("rca_code") == 1 else None),
        # Route number and urban/rural, so a national search can distinguish
        # the dozens of roads that share a name. "Main Road" is unhelpful;
        # "Main Road - SH 6 - Tasman District Council" is not.
        "roadNumber": row.get("road_number"),
        "urbanRural": {1: "Urban", 2: "Rural"}.get(row.get("urban_rural")),
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


# Names come from the naming layer, never from links.road_name. The view falls
# back to that column for any snapshot that has not been through a naming pass,
# and it applies the licence gate, so this is the only place a name should be
# read from.
_NAME_JOIN = (
    " LEFT JOIN link_display_names dn "
    "        ON dn.snapshot_id = l.snapshot_id AND dn.link_id = l.link_id "
)

_NAME_COLUMNS = (
    "dn.display_name, dn.name_status, dn.name_source, dn.route_designation, "
    "dn.alternates AS name_alternates, dn.conflict AS name_conflict, "
    "dn.withheld_name_source, dn.match_confidence AS name_confidence"
)

_LINK_COLUMNS = (
    f"l.*, {_NAME_COLUMNS}, "
    "ST_Y(ST_Transform(ST_LineInterpolatePoint(l.geom_2193, 0.5), 4326)) AS clat, "
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
    # Ranking expression, used in the ORDER BY below.
    #
    # The no-name fallback must be a typed NULL, not `0`. A bare integer
    # constant in ORDER BY is parsed as a POSITIONAL reference to a select-list
    # column, so `ORDER BY (0)` raises "ORDER BY position 0 is not in select
    # list" - which is what a bbox-only search did until this was fixed.
    rank = "NULL::int"
    if name:
        # Match the route number as well as the name: on a national snapshot
        # "SH 1" is a far more likely query than any road name, and it lives
        # in a different column.
        clauses.append(
            "(coalesce(dn.display_name, l.road_name) ILIKE %(like)s OR l.road_number ILIKE %(like)s)"
        )
        params["like"] = f"%{name}%"
        params["exact"] = name
        params["prefix"] = f"{name}%"
        # Exact, then prefix, then anywhere - and a road number match ranks
        # with the equivalent name match rather than below every name.
        #
        # Lower sorts first.
        rank = """
            CASE
              WHEN lower(coalesce(dn.display_name, l.road_name))
                     = lower(%(exact)s)                     THEN 0
              WHEN lower(l.road_number) = lower(%(exact)s)  THEN 1
              WHEN coalesce(dn.display_name, l.road_name)
                     ILIKE %(prefix)s                       THEN 2
              WHEN l.road_number ILIKE %(prefix)s           THEN 3
              ELSE 4
            END
        """
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

    # State highways and lifeline routes ahead of local roads at equal rank,
    # then longest first. On a national snapshot the alternative is that a
    # search for a common name returns whichever fragment the planner happened
    # to read first.
    rows = db.query(
        f"""
        SELECT {_LINK_COLUMNS}
          FROM links l {_NAME_JOIN}
         WHERE {' AND '.join(clauses)}
         ORDER BY ({rank}),
                  (l.rca_code = 1) DESC,
                  l.lifeline_route DESC,
                  l.length_m DESC
         LIMIT %(limit)s
        """,
        params,
    )
    loc = _locality_lookup(snapshot_id(), [int(r["link_id"]) for r in rows])
    return {
        "snapshotId": snapshot_id(),
        "count": len(rows),
        "truncated": len(rows) >= limit,
        "results": [_link_summary(r, loc.get(int(r["link_id"]))) for r in rows],
    }


def _resolve(link_ref: str) -> dict[str, Any]:
    snap = snapshot_id()
    row = db.query_one(
        f"SELECT {_LINK_COLUMNS} FROM links l {_NAME_JOIN} "
        f"WHERE l.snapshot_id=%s AND l.amds_id=%s",
        (snap, link_ref),
    )
    if row is None and link_ref.lstrip("-").isdigit():
        row = db.query_one(
            f"SELECT {_LINK_COLUMNS} FROM links l {_NAME_JOIN} "
            f"WHERE l.snapshot_id=%s AND l.link_id=%s", (snap, int(link_ref)))
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
            SELECT a.arc_id, a.link_id, a.direction, l.amds_id,
                   coalesce(dn.display_name, l.road_name) AS road_name,
                   dn.name_status, l.length_m,
                   ST_AsGeoJSON(CASE WHEN a.direction='reverse'
                                     THEN ST_Reverse(l.geom_4326)
                                     ELSE l.geom_4326 END, 7) AS geom
            FROM arcs a JOIN links l
              ON l.snapshot_id=a.snapshot_id AND l.link_id=a.link_id
         LEFT JOIN link_display_names dn
              ON dn.snapshot_id=l.snapshot_id AND dn.link_id=l.link_id
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
                    "nameStatus": r.get("name_status"),
                    "lengthM": _round(r["length_m"], 1),
                    "direction": r["direction"],
                },
            })
        return {"type": "FeatureCollection", "features": feats}

    def _links_geojson(snapshot: str, link_ids: list[int]) -> dict[str, Any] | None:
        """Geometry for a set of links, or None when there is nothing to draw.

        Capped: a pathological closure can strand a large part of the network,
        and shipping tens of thousands of geometries would make the response
        slower than the analysis that produced it. Past the cap the counts are
        still returned, and the client says the extent is not drawn rather than
        drawing part of it and implying that is all of it.
        """
        if not geometry or not link_ids:
            return None
        if len(link_ids) > MAX_DRAWN_STRANDED_LINKS:
            return None
        import json

        rows = db.query(
            """
            SELECT l.link_id, l.amds_id, l.road_name, l.length_m,
                   ST_AsGeoJSON(l.geom_4326, 7) AS geom
            FROM links l
            WHERE l.snapshot_id = %s AND l.link_id = ANY(%s)
            """,
            (snapshot, link_ids),
        )
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": json.loads(r["geom"]),
                    "properties": {
                        "role": "stranded",
                        "linkId": r["link_id"],
                        "amdsId": r["amds_id"],
                        "roadName": r["road_name"],
                        "lengthM": _round(r["length_m"], 1),
                    },
                }
                for r in rows
            ],
        }

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
                # The stranded links themselves, so the map can draw what is
                # cut off rather than only report how much of it there is.
                #
                # NOT a polygon, and deliberately never one: this is a set of
                # links that lose connectivity, not a service area or a
                # catchment. A hull drawn round them would enclose properties
                # still reachable by roads outside the set, and would read as a
                # claim about an area that the analysis does not make.
                "linkGeoJson": _links_geojson(snap, d.isolation.pocket_link_ids),
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
        "SELECT l.link_id, l.amds_id, "
        "       coalesce(dn.display_name, l.road_name) AS road_name, "
        "       ST_AsGeoJSON(l.geom_4326, 7) AS geom "
        "FROM links l LEFT JOIN link_display_names dn "
        "  ON dn.snapshot_id=l.snapshot_id AND dn.link_id=l.link_id "
        "WHERE l.snapshot_id=%s AND l.link_id = ANY(%s)",
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
        "selectedLink": _link_summary(
            link, _locality_lookup(snap, [int(link["link_id"])]).get(
                int(link["link_id"]))),
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


# ==========================================================================
# V2 - closure analysis
# ==========================================================================
# New paths, never in place of the V1 ones. Every V1 response above is
# byte-identical to what it was before this module learned about V2, and the
# V1 route does not import anything from here.
#
# V2 is a development preview. It is not the default anywhere, it advertises
# algorithmVersion 3.0.0-dev, and the client only reaches it behind a dev flag.

V2_SCOPES = ("segment", "direction", "source_feature")


@app.get("/api/v2/links/{link_ref:path}/closure-analysis")
def closure_analysis_v2(
    link_ref: str,
    scope: Literal["segment", "direction", "source_feature"] = "segment",
    direction: Literal["forward", "reverse", "both"] = "both",
    metric: Metric = "distance",
    vehicle: Profile = "car",
    geometry: bool = False,
    cache: bool = True,
) -> dict[str, Any]:
    """Closure impact under an explicit scope, with exact physical isolation.

    Default scope is `segment`: the exact graph link selected, both directions.
    That is a different question from the one V1 answers by default, and the
    response says so in `comparableToV1`.
    """
    from . import closure as closure_mod
    from . import detourv2

    link = _resolve(link_ref)
    snap = snapshot_id()

    if scope == "direction" and direction == "both":
        raise HTTPException(
            422, "scope=direction needs direction=forward or direction=reverse: "
                 "a single directed traversal has to say which one")

    try:
        result = detourv2.analyse(
            snap, int(link["link_id"]), scope=scope, direction=direction,
            metric=metric, profile=vehicle, use_cache=cache)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    body = detourv2.as_dict(result)
    body["snapshotId"] = snap
    body["attribution"] = _ACTIVE["meta"]["attribution"]
    body["limitations"] = LIMITATIONS
    body["comparableToV1"] = scope == "source_feature"
    body["selectedLink"] = _link_summary(
        link, _locality_lookup(snap, [int(link["link_id"])]).get(
            int(link["link_id"])))

    if geometry:
        sep = result.isolation.separated_link_ids
        body["isolation"]["separatedGeoJson"] = (
            _links_geojson_v2(snap, sep) if 0 < len(sep) <= MAX_DRAWN_STRANDED_LINKS
            else None)
    return body


def _links_geojson_v2(snap: str, link_ids: list[int]) -> dict[str, Any] | None:
    import json
    rows = db.query(
        "SELECT l.link_id, l.amds_id, l.length_m, "
        "       ST_AsGeoJSON(l.geom_4326, 7) AS geom "
        "  FROM links l WHERE l.snapshot_id=%s AND l.link_id = ANY(%s) "
        " ORDER BY l.link_id",
        (snap, link_ids))
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": json.loads(r["geom"]),
             "properties": {"linkId": r["link_id"], "amdsId": r["amds_id"],
                            "lengthM": _round(r["length_m"], 1)}}
            for r in rows
        ],
    }


@app.get("/api/v2/links/{link_ref:path}/shadow-comparison")
def shadow_comparison(
    link_ref: str,
    scope: Literal["segment", "direction", "source_feature"] = "source_feature",
    direction: Literal["forward", "reverse", "both"] = "both",
    metric: Metric = "distance",
    vehicle: Profile = "car",
    persist: bool = True,
) -> dict[str, Any]:
    """Run V1 and V2 over the same link and report every way they differ.

    The default scope is `source_feature`, because that is the only scope under
    which the two engines are answering the SAME question. Comparing V1's
    whole-source-feature closure against V2's segment closure would produce
    differences that are entirely explained by scope, and reporting those as
    engine disagreement would be worthless.
    """
    from . import shadow

    link = _resolve(link_ref)
    try:
        row = shadow.compare(
            snapshot_id(), int(link["link_id"]), scope=scope,
            direction=direction, metric=metric, profile=vehicle,
            persist=persist)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return row


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
                   coalesce(dn.display_name, l.road_name, '')
                                                   AS "roadName",
                   coalesce(dn.name_status, '')    AS "nameStatus",
                   coalesce(l.road_number, '')     AS "roadNumber",
                   (l.oneway = 1)::int             AS oneway,
                   (l.rca_code = 1)::int           AS "stateHighway",
                   l.lifeline_route::int           AS lifeline,
                   l.in_analysis_area::int         AS core,
                   round(l.length_m)::int          AS "lengthM",
                   coalesce(l.model_asset_type, 0) AS "roadClass",
                   ST_AsMVTGeom(ST_Transform(l.geom_2193, 3857),
                                bounds.merc, 4096, 64, true) AS geom
            FROM links l
            LEFT JOIN link_display_names dn
                   ON dn.snapshot_id = l.snapshot_id
                  AND dn.link_id = l.link_id,
                 bounds
            WHERE l.snapshot_id = %(snap)s
              AND l.geom_2193 && bounds.nztm
              -- Generalisation, applied here as well as in the client style.
              --
              -- The client filter fixes readability; this fixes tile SIZE. A
              -- national z6 tile covers most of an island, and encoding all
              -- 375,485 graph links into it produces a multi-megabyte
              -- protobuf that is slow to build, slow to ship and immediately
              -- thrown away by a filter that was never going to draw it.
              --
              -- A DRAWING RULE ONLY: nothing here affects the graph, search or
              -- any calculation. Those read `links` and `arcs` directly.
              AND (
                    %(z)s >= 12
                 OR l.rca_code = 1
                 OR l.lifeline_route
                 OR (%(z)s >= 9 AND l.length_m > 800)
              )
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
    # RELATIVE tile template. Behind the Vite proxy (changeOrigin) request.base_url
    # is the backend address, not the browser-facing one - verified: it returned
    # http://127.0.0.1:8000/... to a browser on :5173. A relative template is
    # resolved by the client against whatever origin actually served the page,
    # which is correct through a proxy, a reverse proxy, or none.
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
            f"/tiles/v{TILE_SCHEMA_VERSION}/{m['snapshot_id']}"
            + "/{z}/{x}/{y}.pbf"
        ],
    }
