"""Acquisition and local caching of every source that can supply a road name.

Naming is a separate concern from topology. Nothing in this module may be
allowed to trigger a network rebuild: it downloads attributes and reference
geometry, caches them under DATA_DIR/raw/external (gitignored), and loads them
into staging tables that the graph does not depend on.

Sources, and what each is authoritative for, are documented in
docs/ROAD_NAME_SOURCES.md. Two rules are enforced here rather than trusted:

* the LINZ Data Service key is read from the environment, never logged, and
  never written into a cached file, a metadata snapshot or an error message;
* cached payloads live under data/raw/, which .gitignore excludes.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlencode

import requests

from .arcgis import download_by_ids, fetch_json, get_layer_meta, get_object_ids
from .config import get_settings

# --------------------------------------------------------------------------
# Service endpoints. Verified 6 August 2026; see docs/ROAD_NAME_SOURCES.md for
# the evidence and for why the NZTA street-names layer is NOT on the ArcGIS
# Online organisation that hosts AMDS.
# --------------------------------------------------------------------------

NZTA_STREET_NAMES_URL = (
    "https://spatial.nzta.govt.nz/portal/rest/services/Hosted/Street_names/FeatureServer"
)
NZTA_STREET_NAMES_LAYER = 0

NZTA_RAMM_CARRIAGEWAY_URL = (
    "https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services"
    "/GEO_MASTER_GIS_Carriageway/FeatureServer"
)
NZTA_RAMM_CARRIAGEWAY_LAYER = 0

LINZ_WFS_URL = "https://data.linz.govt.nz/services;key={key}/wfs"
#: "NZ Addresses: Road Sections". Found by reading GetCapabilities from the
#: live service rather than by assuming a documented name or a historical id.
LINZ_ROAD_SECTIONS_TYPE = "layer-123109"

#: Fields worth carrying. Requesting a named list rather than "*" keeps the
#: cache small and makes an upstream schema change visible as a hard failure
#: instead of a silent column that nothing reads.
#: The object-id column is added per layer from the service's own metadata.
#: NZTA's Enterprise Portal calls it `objectid`; ArcGIS Online calls it
#: `OBJECTID`, and asking either for the other's spelling is a hard 500.
STREET_NAME_FIELDS = [
    "fullprimaryroadname", "primaryname", "alternateroadname",
    "pseudonymroadname", "isunnamed", "isstatehighway", "isprivate",
    "linzrdsegid", "leftlocalityname", "rightlocalityname", "lefttaname",
    "righttaname", "referencestation", "bridgename", "tunnelname", "oneway",
    "isdualcarriageway", "status", "retireddate", "changedate",
    "classification", "hierarchy", "surfacetype",
]

RAMM_FIELDS = [
    "roadName", "roadID", "roadCorridor", "carrWayNo",
    "carrwayStartM", "carrwayEndM", "startName", "endName", "roadClass",
    "roadClassification", "shRampType", "roadGroup",
]

LINZ_ROAD_SECTION_FIELDS = [
    "road_section_id", "road_id", "full_road_name", "full_road_name_ascii",
    "road_name_label", "road_name_body", "road_name_type", "road_name_suffix",
    "secondary_road_name", "tertiary_road_name", "suburb_locality",
    "town_city", "territorial_authority",
]


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------

def cache_root() -> Path:
    """Where downloaded source payloads live. Under data/raw/, so gitignored."""
    return get_settings().data_dir / "raw" / "external"


def _cache_path(source: str, name: str, acquired: str) -> Path:
    return cache_root() / source / acquired / f"{name}.ndjson.gz"


def write_ndjson(path: Path, rows: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    n = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            n += 1
    tmp.replace(path)
    return n


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def latest_cache(source: str, name: str) -> Path | None:
    """Newest cached copy of a source, by acquisition date directory."""
    base = cache_root() / source
    if not base.is_dir():
        return None
    found = sorted(
        (d / f"{name}.ndjson.gz" for d in base.iterdir() if d.is_dir()),
        key=lambda p: p.parent.name,
    )
    live = [p for p in found if p.exists()]
    return live[-1] if live else None


def _today() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------------
# AMDS RouteName — native, already licensed, already attributed
# --------------------------------------------------------------------------

#: Every field of table 11 that bears on which name is correct, when it applied,
#: and whether it is a road name at all. The previous ingest read four of them.
ROUTE_NAME_FIELDS = [
    "OBJECTID", "amdsIDRouteName", "routeGroup", "routeSubGroup",
    "routeNumber1", "routeAlpha1", "routeNumber2", "routeAlpha2",
    "namePrefix", "nameBody1", "nameType", "nameSuffix", "nameBody2",
    "isMacronated", "routeNameFull", "routeNameAbbreviated",
    "routeNameFullASCII", "routeNameAbbreviatedASCII",
    "referenceStation", "rampNumber", "rampType", "interchangeNumber",
    "localityName", "includeLocalityInName", "direction",
    "authorisedAlternative", "status", "effectiveFrom", "effectiveTo",
    "dataManagingOrganisation",
]

ROUTE_JOIN_FIELDS = ["amdsIDNetworkModel", "amdsIDRouteName", "isPrimary"]


def fetch_amds_table(
    table_id: int,
    fields: list[str],
    label: str,
    *,
    concurrency: int = 6,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    s = get_settings()
    meta = get_layer_meta(s.amds_feature_service_url, table_id)
    ids = get_object_ids(s.amds_feature_service_url, table_id, "1=1")
    if not ids:
        return []
    res = download_by_ids(
        s.amds_feature_service_url, table_id, ids,
        out_fields=fields, return_geometry=False, out_sr=s.analysis_srid,
        batch_size=meta.max_record_count, object_id_field=meta.object_id_field,
        concurrency=concurrency, on_progress=on_progress,
    )
    if res.missing_ids:
        print(f"  WARNING: {label}: {len(res.missing_ids)} ids did not come back",
              file=sys.stderr)
    return [f.get("attributes", {}) for f in res.features]


def acquire_amds_route_names(*, refresh: bool = False,
                             concurrency: int = 6) -> tuple[Path, Path]:
    """Cache tables 11 (RouteName) and 13 (NetworkModel<->RouteName) in full."""
    s = get_settings()
    today = _today()
    detail = _cache_path("amds-routename", "table-11-routename", today)
    join = _cache_path("amds-routename", "table-13-join", today)

    if not refresh:
        have_detail = latest_cache("amds-routename", "table-11-routename")
        have_join = latest_cache("amds-routename", "table-13-join")
        if have_detail and have_join:
            return have_detail, have_join

    print(f"  AMDS table {s.amds_routename_detail_table_id} (RouteName)...",
          end="", flush=True)
    rows = fetch_amds_table(s.amds_routename_detail_table_id, ROUTE_NAME_FIELDS,
                            "route names", concurrency=concurrency)
    print(f" {write_ndjson(detail, rows)} rows")

    print(f"  AMDS table {s.amds_routename_table_id} (join)...", end="", flush=True)
    rows = fetch_amds_table(s.amds_routename_table_id, ROUTE_JOIN_FIELDS,
                            "route-name join", concurrency=concurrency)
    print(f" {write_ndjson(join, rows)} rows")

    return detail, join


# --------------------------------------------------------------------------
# ArcGIS feature layers with geometry (NZTA street names, RAMM)
# --------------------------------------------------------------------------

def _paged_download(service_url: str, layer_id: int, fields: list[str],
                    out_sr: int, label: str) -> Iterator[dict[str, Any]]:
    """Download a whole layer by OBJECTID batches, yielding Esri features.

    OBJECTID batching rather than resultOffset paging: offset paging over a
    live service can skip or repeat rows if the underlying data is edited
    mid-download, and a road-name gap that appears at random is worse than a
    slower download.
    """
    meta = get_layer_meta(service_url, layer_id)
    fields = list(fields)
    if meta.object_id_field not in fields:
        fields.insert(0, meta.object_id_field)
    ids = get_object_ids(service_url, layer_id, "1=1")
    batch = max(1, meta.max_record_count)
    total = len(ids)
    done = 0
    for i in range(0, total, batch):
        chunk = ids[i:i + batch]
        body = fetch_json(
            f"{service_url}/{layer_id}/query",
            data={
                "objectIds": ",".join(str(x) for x in chunk),
                "outFields": ",".join(fields),
                "returnGeometry": "true",
                "outSR": str(out_sr),
                "f": "json",
            },
            label=f"{label} {i // batch + 1}",
        )
        feats = body.get("features") or []
        done += len(feats)
        print(f"\r  {label}: {done}/{total}", end="", flush=True)
        yield from feats
    print()


def _esri_to_row(feat: dict[str, Any]) -> dict[str, Any]:
    """Flatten an Esri polyline feature to attributes + paths."""
    geom = feat.get("geometry") or {}
    return {
        "attributes": feat.get("attributes") or {},
        "paths": geom.get("paths") or [],
    }


def acquire_nzta_street_names(*, refresh: bool = False) -> Path:
    path = _cache_path("nzta-street-names", "features", _today())
    if not refresh:
        have = latest_cache("nzta-street-names", "features")
        if have:
            return have
    rows = (_esri_to_row(f) for f in _paged_download(
        NZTA_STREET_NAMES_URL, NZTA_STREET_NAMES_LAYER, STREET_NAME_FIELDS,
        get_settings().analysis_srid, "NZTA street names"))
    print(f"  cached {write_ndjson(path, rows)} street-name features")
    return path


def acquire_nzta_ramm(*, refresh: bool = False) -> Path:
    path = _cache_path("nzta-ramm-carriageway", "features", _today())
    if not refresh:
        have = latest_cache("nzta-ramm-carriageway", "features")
        if have:
            return have
    rows = (_esri_to_row(f) for f in _paged_download(
        NZTA_RAMM_CARRIAGEWAY_URL, NZTA_RAMM_CARRIAGEWAY_LAYER, RAMM_FIELDS,
        get_settings().analysis_srid, "NZTA RAMM carriageway"))
    print(f"  cached {write_ndjson(path, rows)} RAMM carriageways")
    return path


# --------------------------------------------------------------------------
# LINZ Data Service WFS
# --------------------------------------------------------------------------

class MissingKey(RuntimeError):
    pass


def _lds_key() -> str:
    key = os.environ.get("LINZ_LDS_API_KEY", "").strip()
    if not key:
        # Fall back to the .env the rest of the settings come from, without
        # putting the key on a Settings object that gets printed anywhere.
        env = get_settings().model_config.get("env_file")
        if env and Path(env).exists():
            for line in Path(env).read_text(encoding="utf-8").splitlines():
                if line.startswith("LINZ_LDS_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise MissingKey(
            "LINZ_LDS_API_KEY is not set. Add it to .env (no VITE_ prefix - it "
            "must never reach the browser). See docs/ROAD_NAME_SOURCES.md."
        )
    return key


def _redact(text: str, key: str) -> str:
    return text.replace(key, "<LINZ_LDS_API_KEY>") if key else text


@dataclass
class WfsPage:
    features: list[dict[str, Any]]
    total: int | None


def acquire_linz_road_sections(*, refresh: bool = False,
                               page_size: int = 10_000) -> Path:
    """Page the LINZ road-sections layer over WFS into the local cache.

    The key appears only in the request URL. Every printed line and every
    exception message is redacted, and nothing derived from the URL is written
    to the cache.
    """
    path = _cache_path("linz-road-sections", "features", _today())
    if not refresh:
        have = latest_cache("linz-road-sections", "features")
        if have:
            return have

    key = _lds_key()
    base = LINZ_WFS_URL.format(key=key)
    srid = get_settings().analysis_srid
    session = requests.Session()
    out: list[dict[str, Any]] = []
    start = 0

    while True:
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": LINZ_ROAD_SECTIONS_TYPE,
            "outputFormat": "application/json",
            "srsName": f"EPSG:{srid}",
            "count": str(page_size),
            "startIndex": str(start),
        }
        url = f"{base}?{urlencode(params)}"
        for attempt in range(4):
            try:
                resp = session.get(url, timeout=300)
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"LINZ WFS HTTP {resp.status_code}: "
                        f"{_redact(resp.text[:300], key)}"
                    )
                body = resp.json()
                break
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                if attempt == 3:
                    raise RuntimeError(_redact(str(exc), key)) from None
                time.sleep(2 ** attempt)

        feats = body.get("features") or []
        if not feats:
            break
        for f in feats:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            out.append({
                "attributes": {k: props.get(k) for k in LINZ_ROAD_SECTION_FIELDS},
                "geojson": geom,
            })
        start += len(feats)
        print(f"\r  LINZ road sections: {start}", end="", flush=True)
        if len(feats) < page_size:
            break
    print()
    print(f"  cached {write_ndjson(path, out)} LINZ road sections")
    return path


# --------------------------------------------------------------------------
# staging into PostGIS
# --------------------------------------------------------------------------

def _bool(v: Any) -> bool | None:
    """ArcGIS hands booleans back as True/False, "True"/"False", or 1/0."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().casefold()
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


def _linz_ids(v: Any) -> list[int]:
    """Parse NZTA's `linzrdsegid`, which holds a comma-separated LIST.

    "143560, 305857, 305858" is three LINZ road sections, not one identifier
    that happens to contain commas. Comparing it as a string would match
    nothing and quietly look like the sources have no lineage.
    """
    if v is None:
        return []
    out: list[int] = []
    for part in str(v).replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _lines_from_paths(paths: list[list[list[float]]]) -> Iterator[list[tuple[float, float]]]:
    for path in paths:
        if path and len(path) >= 2:
            yield [(float(p[0]), float(p[1])) for p in path]


def _lines_from_geojson(geom: dict[str, Any]) -> Iterator[list[tuple[float, float]]]:
    kind = geom.get("type")
    if kind == "LineString":
        coords = [geom.get("coordinates") or []]
    elif kind == "MultiLineString":
        coords = geom.get("coordinates") or []
    else:
        return
    for line in coords:
        if line and len(line) >= 2:
            yield [(float(p[0]), float(p[1])) for p in line]


def _first_name(*values: Any) -> str | None:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


@dataclass
class StagedRow:
    source: str
    feature_id: str
    part: int
    display_name: str | None
    is_unnamed: bool | None = None
    is_state_highway: bool | None = None
    is_private: bool | None = None
    is_dual_carriageway: bool | None = None
    oneway: str | None = None
    status: str | None = None
    locality: str | None = None
    locality_alt: str | None = None
    territorial_authority: str | None = None
    territorial_authority_alt: str | None = None
    linz_road_section_ids: list[int] = field(default_factory=list)
    corridor: str | None = None
    route_code: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    coords: list[tuple[float, float]] = field(default_factory=list)


def _street_name_rows(rows: list[dict[str, Any]]) -> Iterator[StagedRow]:
    for r in rows:
        a = r["attributes"]
        fid = str(a.get("objectid") or a.get("OBJECTID"))
        unnamed = _bool(a.get("isunnamed"))
        name = _first_name(a.get("fullprimaryroadname"), a.get("primaryname"))
        # A road the source marks unnamed carries no display name, whatever
        # string happens to sit in the name column. Keeping the string would
        # let a placeholder leak into the interface as if it were a name.
        if unnamed:
            name = None
        for i, coords in enumerate(_lines_from_paths(r["paths"])):
            yield StagedRow(
                source="nzta_street_names", feature_id=fid, part=i,
                display_name=name,
                is_unnamed=unnamed,
                is_state_highway=_bool(a.get("isstatehighway")),
                is_private=_bool(a.get("isprivate")),
                is_dual_carriageway=_bool(a.get("isdualcarriageway")),
                oneway=_first_name(a.get("oneway")),
                status=_first_name(a.get("status")),
                locality=_first_name(a.get("leftlocalityname")),
                locality_alt=_first_name(a.get("rightlocalityname")),
                territorial_authority=_first_name(a.get("lefttaname")),
                territorial_authority_alt=_first_name(a.get("righttaname")),
                linz_road_section_ids=_linz_ids(a.get("linzrdsegid")),
                extra={k: a.get(k) for k in (
                    "alternateroadname", "pseudonymroadname", "referencestation",
                    "bridgename", "tunnelname", "classification", "hierarchy",
                    "surfacetype", "retireddate", "changedate")},
                coords=coords,
            )


def _linz_rows(rows: list[dict[str, Any]]) -> Iterator[StagedRow]:
    for r in rows:
        a = r["attributes"]
        fid = str(a.get("road_section_id"))
        for i, coords in enumerate(_lines_from_geojson(r["geojson"])):
            yield StagedRow(
                source="linz_road_sections", feature_id=fid, part=i,
                display_name=_first_name(a.get("full_road_name")),
                locality=_first_name(a.get("suburb_locality")),
                locality_alt=_first_name(a.get("town_city")),
                territorial_authority=_first_name(a.get("territorial_authority")),
                extra={k: a.get(k) for k in (
                    "road_id", "full_road_name_ascii", "road_name_label",
                    "road_name_body", "road_name_type", "road_name_suffix",
                    "secondary_road_name", "tertiary_road_name")},
                coords=coords,
            )


def _ramm_rows(rows: list[dict[str, Any]]) -> Iterator[StagedRow]:
    for r in rows:
        a = r["attributes"]
        fid = str(a.get("OBJECTID") or a.get("objectid"))
        for i, coords in enumerate(_lines_from_paths(r["paths"])):
            # display_name stays NULL by construction. RAMM's `roadName` is a
            # route-section code ("003-0076") and its `roadCorridor` spans
            # hundreds of kilometres ("Hamilton to New Plymouth"); neither is a
            # road name, so neither is stored anywhere the matcher could
            # mistake for one.
            yield StagedRow(
                source="nzta_ramm_carriageway", feature_id=fid, part=i,
                display_name=None,
                corridor=_first_name(a.get("roadCorridor")),
                route_code=_first_name(a.get("roadName")),
                extra={k: a.get(k) for k in (
                    "roadID", "carrWayNo", "carrwayStartM", "carrwayEndM",
                    "startName", "endName", "roadClass", "roadClassification",
                    "shRampType", "roadGroup")},
                coords=coords,
            )


SOURCES = {
    "nzta_street_names": (NZTA_STREET_NAMES_URL, _street_name_rows),
    "linz_road_sections": (LINZ_WFS_URL.split(";")[0] + " (WFS " +
                           LINZ_ROAD_SECTIONS_TYPE + ")", _linz_rows),
    "nzta_ramm_carriageway": (NZTA_RAMM_CARRIAGEWAY_URL, _ramm_rows),
}

CACHE_NAMES = {
    "nzta_street_names": ("nzta-street-names", "features"),
    "linz_road_sections": ("linz-road-sections", "features"),
    "nzta_ramm_carriageway": ("nzta-ramm-carriageway", "features"),
}


def stage(source: str) -> dict[str, Any]:
    """Load one cached source into `ext_road_names`.

    Replaces that source's rows wholesale. Partial loads are the kind of thing
    that silently halves a match rate months later.
    """
    from shapely.geometry import LineString

    from . import db
    from .naming import search_key

    service_url, builder = SOURCES[source]
    folder, name = CACHE_NAMES[source]
    path = latest_cache(folder, name)
    if path is None:
        raise FileNotFoundError(
            f"no cached payload for {source}; run acquisition first")
    payload = read_ndjson(path)

    srid = get_settings().analysis_srid
    written = 0
    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ext_road_names WHERE source = %s", (source,))
            with cur.copy(
                "COPY ext_road_names (source, feature_id, part, display_name, "
                "name_key, is_unnamed, is_state_highway, is_private, "
                "is_dual_carriageway, oneway, status, locality, locality_alt, "
                "territorial_authority, territorial_authority_alt, "
                "linz_road_section_ids, corridor, route_code, extra, length_m, "
                "geom_2193) FROM STDIN"
            ) as cp:
                for row in builder(payload):
                    line = LineString(row.coords)
                    if line.length <= 0:
                        continue
                    cp.write_row((
                        row.source, row.feature_id, row.part, row.display_name,
                        search_key(row.display_name), row.is_unnamed,
                        row.is_state_highway, row.is_private,
                        row.is_dual_carriageway, row.oneway, row.status,
                        row.locality, row.locality_alt,
                        row.territorial_authority,
                        row.territorial_authority_alt,
                        row.linz_road_section_ids, row.corridor, row.route_code,
                        json.dumps(row.extra, default=str), line.length,
                        _ewkb_hex(line, srid),
                    ))
                    written += 1
        conn.commit()

    acquired = path.parent.name
    db.execute(
        "INSERT INTO ext_source_runs (source, acquired_at_utc, service_url, "
        "feature_count, row_count, srid, licence, attribution, notes) "
        "VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (source, acquired_at_utc) DO UPDATE SET "
        "row_count = EXCLUDED.row_count",
        (source, acquired, service_url, len(payload), written, srid,
         LICENCE_STATE[source], ATTRIBUTION[source], NOTES.get(source, [])),
    )
    return {"source": source, "features": len(payload), "rows": written}


def _ewkb_hex(geom: Any, srid: int) -> str:
    import shapely
    return shapely.to_wkb(shapely.set_srid(geom, srid), hex=True, include_srid=True)


#: Recorded as published. Both NZTA layers ship an empty `copyrightText`, and
#: that is a fact about the source, not an omission here. No external name
#: reaches the interface while its licence reads "unconfirmed".
LICENCE_STATE = {
    "nzta_street_names": "unconfirmed - copyrightText empty on the published layer",
    "linz_road_sections": "expected CC BY 4.0 - confirm on the LINZ dataset page",
    "nzta_ramm_carriageway": "unconfirmed - copyrightText empty on the published layer",
}

ATTRIBUTION = {
    "nzta_street_names": "NZTA Waka Kotahi Street names (Enterprise Portal)",
    "linz_road_sections": "Land Information New Zealand - NZ Addresses: Road Sections",
    "nzta_ramm_carriageway": "NZTA Waka Kotahi RAMM State Highway Carriageway",
}

NOTES = {
    "nzta_ramm_carriageway": [
        "roadName is a route-section code and roadCorridor spans the whole "
        "route; neither is stored as a display name",
    ],
    "nzta_street_names": [
        "isunnamed is the only available authoritative unnamed classification",
        "linzrdsegid is a comma-separated list and is parsed, not compared",
    ],
}

