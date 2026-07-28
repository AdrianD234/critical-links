"""Ingest the AMDS Network Model into an immutable PostGIS snapshot.

    nzcl-ingest --pilot wellington
    nzcl-ingest --national
    nzcl-ingest --bbox 1710000,5390000,1800000,5480000 --name my-area

Order of operations, and why:
  1. pin the exact OBJECTID set that matches the filter
  2. download those ids in bounded batches with retry/backoff
  3. reconcile what came back - a shortfall marks the snapshot `partial`,
     it never passes silently
  4. join the attribute tables that make the result usable
  5. split at junctions (AMDS does not, and the graph is unusable without it)
  6. assign nodes, generate directed arcs, compute components
  7. bulk load into PostGIS

Extent handling: a clipped extract is downloaded with a generous BUFFER around
the analysis area. Without it, every link near the edge would appear to have no
detour simply because the road carrying the detour was not downloaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import shapely
from shapely.geometry import LineString, Point

from . import db
from .arcgis import download_by_ids, get_count, get_layer_meta, get_object_ids
from .config import (
    DEFAULT_ATTRIBUTION,
    LINK_WHERE,
    PROCESSING_VERSION,
    get_settings,
)
from .geo import nztm_to_lonlat, nztm_to_lonlat_many, polyline_length
from .speed import assign_speed
from .topology import SourceLink, assign_nodes, split_at_junctions

LINK_FIELDS = [
    "OBJECTID", "amdsIDNetworkModel", "status", "modelAssetType", "oneway",
    "surfaceType", "assetOwnerOrganisation", "dataManagingOrganisation",
    "amdsIDAuthority", "lifeLineRoute", "sharedInfrastructure", "detour",
    "modeVehicle", "modeVehicleHeavy", "modeEmergencyManagement", "modeFerry",
    "isLengthCounted", "Shape__Length",
]


@dataclass(frozen=True)
class Bbox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def as_dict(self) -> dict[str, float]:
        return {"xmin": self.xmin, "ymin": self.ymin,
                "xmax": self.xmax, "ymax": self.ymax}

    def wkt(self) -> str:
        return (
            f"POLYGON(({self.xmin} {self.ymin},{self.xmax} {self.ymin},"
            f"{self.xmax} {self.ymax},{self.xmin} {self.ymax},"
            f"{self.xmin} {self.ymin}))"
        )


@dataclass(frozen=True)
class Pilot:
    name: str
    description: str
    analysis: Bbox
    extract: Bbox


# Wellington was chosen over Auckland because its topology is dominated by
# genuine single points of failure - the Ngauranga Gorge, the Hutt corridor,
# Rimutaka Hill Road, the Mt Victoria and Terrace tunnels, the Wellington
# one-way pairs - which is precisely what a criticality tool has to get right.
# It also contains motorway, divided carriageway, complex interchanges, rural
# state highway and dense urban local roads within one modest extent.
#
# The extract is buffered 60 km beyond the analysis area so that long regional
# detours (for instance via SH2 and SH58) stay inside the downloaded network.
PILOTS: dict[str, Pilot] = {
    "wellington": Pilot(
        name="wellington",
        description="Wellington City, Porirua, Lower and Upper Hutt, "
                    "with a 60 km network buffer",
        analysis=Bbox(1735000, 5415000, 1775000, 5455000),
        extract=Bbox(1675000, 5355000, 1835000, 5515000),
    ),
    "auckland": Pilot(
        name="auckland",
        description="Auckland isthmus and North Shore, with a 60 km network buffer",
        analysis=Bbox(1740000, 5890000, 1780000, 5940000),
        extract=Bbox(1680000, 5830000, 1840000, 6000000),
    ),
}


def _fetch_table(layer_id: int, fields: list[str], concurrency: int,
                 label: str) -> list[dict[str, Any]]:
    s = get_settings()
    meta = get_layer_meta(s.amds_feature_service_url, layer_id)
    ids = get_object_ids(s.amds_feature_service_url, layer_id, "1=1")
    print(f"  {label}: {len(ids)} rows", end="", flush=True)
    if not ids:
        print()
        return []
    res = download_by_ids(
        s.amds_feature_service_url, layer_id, ids,
        out_fields=fields, return_geometry=False, out_sr=s.analysis_srid,
        batch_size=meta.max_record_count, object_id_field=meta.object_id_field,
        concurrency=concurrency,
    )
    extra = f"  MISSING {len(res.missing_ids)}" if res.missing_ids else ""
    print(f" -> {len(res.features)} downloaded{extra}")
    return [f.get("attributes", {}) for f in res.features]


def _connected_components(pairs: list[tuple[int, int]], node_count: int) -> list[int]:
    """Weak components by union-find. Gives a cheap definitive negative: two
    nodes in different components can never be connected by any route."""
    parent = list(range(node_count))

    def find(v: int) -> int:
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    labels: dict[int, int] = {}
    out = [0] * node_count
    for i in range(node_count):
        r = find(i)
        if r not in labels:
            labels[r] = len(labels)
        out[i] = labels[r]
    return out


def _ewkb(geom, srid: int) -> str:
    return shapely.to_wkb(shapely.set_srid(geom, srid), hex=True, include_srid=True)


def _to_4326_line(coords: list[tuple[float, float]]) -> LineString:
    """Reproject a coordinate run in one vectorised call.

    shapely.ops.transform invokes the transformer per coordinate, which is an
    order of magnitude slower over hundreds of thousands of links.
    """
    lon, lat = nztm_to_lonlat_many([c[0] for c in coords], [c[1] for c in coords])
    return LineString(zip(lon, lat))


def _to_4326_point(x: float, y: float) -> Point:
    lon, lat = nztm_to_lonlat(x, y)
    return Point(lon, lat)


# Above this many CANDIDATE movements the edge-expanded graph is refused.
#
# Ten million is far beyond any plausible road network and means node assignment
# has collapsed unrelated arcs onto one node. Measured candidate counts:
# Wellington 171,619, Auckland 360,759, national 1,851,262.
#
# DO NOT RAISE THIS TO MAKE AN INGEST PROCEED. Exceeding it is a finding about
# the topology, not a limit to be tuned around: measured worst nodes are 12 in /
# 12 out (Wellington), 6/6 (Auckland) and 22/22 (national). See
# docs/audits/2026-07-28-national-ingest-incident.md.
MAX_CANDIDATE_TRANSITIONS = 10_000_000


def _check_transition_cost(cur, snapshot_id: str) -> int:
    """Refuse to build the expanded graph when the node degrees say not to.

    Returns the candidate count, for logging alongside the actual row count.

    WHAT THIS NUMBER IS - AND IS NOT

    The join in `build_arc_transitions` visits, for every node, the product of
    its in-degree and out-degree. That product is an UPPER BOUND on the rows
    produced, not the row count: the builder then discards immediate U-turns
    (`b.link_id <> a.link_id`) and pairs banned by a two-link restriction.

    The accounting is exact, and was verified on all three snapshots:

        candidates = built + u-turns excluded + restricted pairs

        Wellington   171,619 = 104,355   + 67,264  + 0
        Auckland     360,759 = 221,934   + 138,824 + 1
        National   1,851,262 = 1,138,261 + 713,000 + 1

    The U-turn term is close to the arc count because most arcs arrive at a node
    where their own reverse arc also departs; the shortfall is one-way links and
    dead ends, which have no reverse to exclude.

    An earlier version of this docstring and the log line called the product the
    exact output size. It is not, and the difference is 39% nationally.

    WHY AN UPPER BOUND IS THE RIGHT THING TO CHECK

    It costs one aggregate scan, whereas the exact count would need the same
    join this guard exists to avoid running. Bounding above is sufficient: a
    figure that is safe cannot hide an explosive one.
    """
    cur.execute(
        """
        SELECT coalesce(sum(indeg::bigint * outdeg::bigint), 0) AS transitions,
               max(indeg) AS max_in, max(outdeg) AS max_out
          FROM (
            SELECT n.node_id,
                   count(*) FILTER (WHERE a.target = n.node_id) AS indeg,
                   count(*) FILTER (WHERE a.source = n.node_id) AS outdeg
              FROM nodes n
              JOIN arcs a ON a.snapshot_id = n.snapshot_id
                         AND (a.source = n.node_id OR a.target = n.node_id)
             WHERE n.snapshot_id = %s
             GROUP BY n.node_id
          ) d
        """,
        (snapshot_id,),
    )
    row = cur.fetchone()
    estimate = int(row["transitions"] if isinstance(row, dict) else row[0])
    max_in = int(row["max_in"] if isinstance(row, dict) else row[1])
    max_out = int(row["max_out"] if isinstance(row, dict) else row[2])

    print(f"  arc transitions: {estimate:,} candidates, upper bound "
          f"(worst node: {max_in} in, {max_out} out)")

    if estimate > MAX_CANDIDATE_TRANSITIONS:
        # Name the nodes responsible, so the next step is investigation rather
        # than guesswork.
        cur.execute(
            """
            SELECT n.node_id, count(*) AS degree,
                   ST_X(n.geom_2193)::int AS x, ST_Y(n.geom_2193)::int AS y
              FROM nodes n
              JOIN arcs a ON a.snapshot_id = n.snapshot_id
                         AND (a.source = n.node_id OR a.target = n.node_id)
             WHERE n.snapshot_id = %s
             GROUP BY n.node_id, n.geom_2193
             ORDER BY count(*) DESC
             LIMIT 5
            """,
            (snapshot_id,),
        )
        worst = cur.fetchall()
        detail = "; ".join(
            f"node {r['node_id'] if isinstance(r, dict) else r[0]} "
            f"degree {r['degree'] if isinstance(r, dict) else r[1]} "
            f"at ({r['x'] if isinstance(r, dict) else r[2]}, "
            f"{r['y'] if isinstance(r, dict) else r[3]})"
            for r in worst
        )
        raise SystemExit(
            f"refusing to build the edge-expanded graph: {estimate:,} candidate "
            f"arc transitions, limit {MAX_CANDIDATE_TRANSITIONS:,}.\n"
            f"  Highest-degree nodes: {detail}\n"
            f"  A road intersection joins three or four ways. A node with "
            f"hundreds means unrelated arcs have been snapped together during "
            f"node assignment - investigate before rebuilding.\n"
            f"  Do NOT raise the limit to get past this."
        )

    return estimate


# How long the derived transition build may run before it is abandoned.
#
# Measured builds: Wellington 1.2 s, Auckland 1.7 s, national under a minute.
# Twenty minutes is far beyond any of them and still terminates in an afternoon
# rather than never. The first national attempt ran 74 minutes with no rows
# written and no bound at all; whatever caused that was never isolated, so the
# protection is a timeout rather than a claim about the cause.
TRANSITION_BUILD_TIMEOUT_MS = 20 * 60 * 1000


def _build_transitions(cur, snapshot_id: str) -> None:
    """Build the edge-expanded graph, bounded and instrumented.

    The degree guard catches an explosive graph. It does NOT explain the first
    national attempt, which had ordinary degrees (22 in / 22 out) and still ran
    74 minutes at full CPU without writing a row. That cause was never isolated,
    so this stage is engineered so the same behaviour cannot recur silently:

      - a statement timeout, so it fails rather than hanging indefinitely;
      - the execution plan captured before running, so a pathological plan is
        recorded rather than inferred afterwards;
      - elapsed time and candidate-versus-actual row counts logged;
      - a distinct, named failure instead of an open transaction nobody can see
        into.
    """
    import time

    candidates = _check_transition_cost(cur, snapshot_id)

    # The plan, before committing to the work. If this stage ever misbehaves
    # again, the plan that produced it is in the log rather than lost with the
    # backend.
    try:
        cur.execute(
            """
            EXPLAIN (COSTS OFF, FORMAT TEXT)
            SELECT a.arc_id, b.arc_id
              FROM arcs a JOIN arcs b
                ON b.snapshot_id = a.snapshot_id AND b.source = a.target
             WHERE a.snapshot_id = %s AND b.link_id <> a.link_id
            """,
            (snapshot_id,),
        )
        rows = cur.fetchall()
        plan = " / ".join(
            (r["QUERY PLAN"] if isinstance(r, dict) else r[0]).strip()
            for r in rows
            if not (r["QUERY PLAN"] if isinstance(r, dict) else r[0]).strip()
            .startswith(("Recheck", "Index Cond", "Filter", "Hash Cond",
                         "Join Filter"))
        )
        print(f"  transition plan: {plan[:200]}")
    except Exception as exc:  # noqa: BLE001
        # Diagnostics must never be the reason an ingest fails.
        print(f"  transition plan: unavailable ({exc})")

    started = time.monotonic()
    # `SET` does not accept bind parameters - `SET LOCAL statement_timeout = $1`
    # is a syntax error. `set_config(..., is_local => true)` is the function form
    # and does. This project has made the same mistake once before in routing.py.
    cur.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (str(TRANSITION_BUILD_TIMEOUT_MS),),
    )
    try:
        cur.execute("SELECT build_arc_transitions(%s) AS n", (snapshot_id,))
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        raise SystemExit(
            f"the edge-expanded graph failed after {elapsed:.0f}s: {exc}\n"
            f"  {candidates:,} candidate transitions were expected.\n"
            f"  The core network is NOT committed - this transaction rolls "
            f"back in full. See "
            f"docs/audits/2026-07-28-national-ingest-incident.md for why that "
            f"is the wrong shape and what replaces it."
        ) from exc

    row = cur.fetchone()
    built = int(row["n"] if isinstance(row, dict) else row[0])
    elapsed = time.monotonic() - started
    # Both numbers, so the difference between them is visible rather than
    # something a reader has to discover later and misinterpret.
    print(f"  arc transitions: {built:,} built from {candidates:,} candidates "
          f"in {elapsed:.1f}s "
          f"({candidates - built:,} u-turns and restricted pairs excluded)")

    # Scoped to this transaction, but the remaining statements in it are cheap
    # and should not inherit a build-sized budget.
    cur.execute("SELECT set_config('statement_timeout', '0', true)")


def run(
    *,
    national: bool = False,
    pilot: str | None = None,
    bbox: Bbox | None = None,
    analysis_bbox: Bbox | None = None,
    name: str | None = None,
    concurrency: int = 6,
) -> str:
    s = get_settings()
    extract: Bbox | None = None
    analysis: Bbox | None = None

    # Coverage is decided here, where the extract is, and carried through to the
    # snapshot row. Nothing downstream has to infer it from whether an extent
    # happens to be null.
    if national:
        area = "national"
        coverage_kind, coverage_name = "national", "New Zealand"
    elif pilot:
        if pilot not in PILOTS:
            raise SystemExit(
                f"unknown pilot {pilot!r}. Available: {', '.join(PILOTS)}"
            )
        p = PILOTS[pilot]
        extract, analysis, area = p.extract, p.analysis, p.name
        coverage_kind = "regional"
        coverage_name = f"{p.name.capitalize()} pilot"
    elif bbox:
        extract = bbox
        analysis = analysis_bbox or bbox
        area = name or "custom"
        coverage_kind = "regional"
        coverage_name = f"{area.capitalize()} extract"
    else:
        raise SystemExit("specify --national, --pilot <name>, or --bbox")

    retrieved = datetime.now(timezone.utc)
    print(f"AMDS ingest: {area}")
    print(f"  service: {s.amds_feature_service_url}")
    print(f"  where:   {LINK_WHERE}")
    print(f"  extract: {extract.as_dict() if extract else 'national (no extent filter)'}")

    link_layer = get_layer_meta(s.amds_feature_service_url, s.amds_link_layer_id)
    ext = extract.as_dict() if extract else None

    # --- 1. pin the id set ------------------------------------------------
    source_count = get_count(s.amds_feature_service_url, s.amds_link_layer_id,
                             LINK_WHERE, ext)
    ids = get_object_ids(s.amds_feature_service_url, s.amds_link_layer_id,
                         LINK_WHERE, ext)
    print(f"\n  service reports {source_count} features; id list has {len(ids)}")
    if len(ids) != source_count:
        print(f"  WARNING: count/id-list mismatch. Service may be mid-edit. "
              f"Proceeding against the id list.", file=sys.stderr)

    # --- 2. download ------------------------------------------------------
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = int(done / max(1, total) * 100)
        if pct != last[0] and pct % 10 == 0:
            last[0] = pct
            print(f"\r  downloading links... {pct}% ({done}/{total})",
                  end="", flush=True)

    dl = download_by_ids(
        s.amds_feature_service_url, s.amds_link_layer_id, ids,
        out_fields=LINK_FIELDS, return_geometry=True, out_sr=s.analysis_srid,
        batch_size=link_layer.max_record_count,
        object_id_field=link_layer.object_id_field,
        concurrency=concurrency, on_progress=progress,
    )
    print(f"\r  downloading links... done ({len(dl.features)}/{len(ids)})      ")
    if dl.missing_ids:
        print(f"  WARNING: {len(dl.missing_ids)} ids did not come back", file=sys.stderr)

    # --- 3. attribute tables ----------------------------------------------
    print("\n  joining attribute tables")
    route_join = _fetch_table(s.amds_routename_table_id,
                              ["amdsIDNetworkModel", "amdsIDRouteName", "isPrimary"],
                              concurrency, "route-name join")
    route_names = _fetch_table(
        s.amds_routename_detail_table_id,
        ["amdsIDRouteName", "routeNameFullASCII", "routeNumber1", "routeAlpha1",
         "routeGroup", "status"],
        concurrency, "route names")
    urban_rural = _fetch_table(s.amds_urbanrural_table_id,
                               ["amdsIDNetworkModel", "urbanRural", "isFullLength",
                                "status"], concurrency, "urban/rural")
    turns = _fetch_table(
        s.amds_restricted_turn_table_id,
        ["amdsIDRestrictedTurn",
         *[f"amdsIDNetworkModel{i}" for i in range(1, 9)],
         "modeVehicleRestricted", "modeVehicleHeavyRestricted",
         "modeEmergencyRestricted", "status"],
        concurrency, "restricted turns")
    restrictions = _fetch_table(
        s.amds_restriction_table_id,
        ["amdsIDNetworkModel", "modeRestriction", "heightRestriction", "heightInfo",
         "weightRestriction", "weightInfo", "isFullLength", "status"],
        concurrency, "restrictions")
    authorities = _fetch_table(s.amds_authority_table_id,
                               ["amdsIDAuthority", "controllingNameASCII"],
                               concurrency, "authorities")

    # --- 4. build source links --------------------------------------------
    print("\n  building link records")
    name_by_route: dict[str, str] = {}
    number_by_route: dict[str, str] = {}
    for r in route_names:
        if r.get("routeNameFullASCII"):
            name_by_route[r["amdsIDRouteName"]] = r["routeNameFullASCII"]
        if r.get("routeNumber1") is not None:
            number_by_route[r["amdsIDRouteName"]] = (
                f"{r['routeNumber1']}{r.get('routeAlpha1') or ''}"
            )

    name_by_link: dict[str, str] = {}
    number_by_link: dict[str, str] = {}
    for j in route_join:
        lid, rid = j.get("amdsIDNetworkModel"), j.get("amdsIDRouteName")
        primary = j.get("isPrimary") == 1
        if rid in name_by_route and (primary or lid not in name_by_link):
            name_by_link[lid] = name_by_route[rid]
        if rid in number_by_route and (primary or lid not in number_by_link):
            number_by_link[lid] = number_by_route[rid]

    # urbanRural domain: 1 = Urban, 2 = Rural (verified against the layer
    # domain in the discovery report); anything else is unknown.
    ur_by_link: dict[str, str] = {}
    for u in urban_rural:
        if u.get("status") != 1:
            continue
        v = {1: "urban", 2: "rural"}.get(u.get("urbanRural"))
        if v and u["amdsIDNetworkModel"] not in ur_by_link:
            ur_by_link[u["amdsIDNetworkModel"]] = v

    authority_name = {
        a["amdsIDAuthority"]: a.get("controllingNameASCII")
        for a in authorities if a.get("amdsIDAuthority")
    }

    hw_by_link: dict[str, list[str]] = {}
    for r in restrictions:
        if r.get("status") != 1:
            continue
        flags = []
        if r.get("heightRestriction") == 1:
            flags.append(f"HEIGHT_LIMIT_{r.get('heightInfo') or '?'}m")
        if r.get("weightRestriction") == 1:
            flags.append(f"WEIGHT_LIMIT_{r.get('weightInfo') or '?'}t")
        if r.get("modeRestriction") == 1:
            flags.append("MODE_RESTRICTED")
        if flags:
            prev = hw_by_link.setdefault(r["amdsIDNetworkModel"], [])
            for f in flags:
                if f not in prev:
                    prev.append(f)

    sources: list[SourceLink] = []
    seen_amds: set[str] = set()
    multipart = degenerate = duplicate = 0

    for f in dl.features:
        a = f.get("attributes") or {}
        amds_id = a.get("amdsIDNetworkModel")
        paths = (f.get("geometry") or {}).get("paths") or []
        if not amds_id or not paths or len(paths[0]) < 2:
            degenerate += 1
            continue
        if amds_id in seen_amds:
            duplicate += 1
            continue

        flags: list[str] = []
        if len(paths) > 1:
            multipart += 1
            flags.append("MULTIPART_GEOMETRY_FIRST_PATH_USED")

        coords = [(float(p[0]), float(p[1])) for p in paths[0]]
        if polyline_length(coords) <= 0:
            degenerate += 1
            continue

        ur = ur_by_link.get(amds_id)
        if not ur:
            flags.append("NO_URBAN_RURAL_COVERAGE")
        flags.extend(hw_by_link.get(amds_id, []))
        oneway = a.get("oneway")
        if oneway not in (1, 2):
            flags.append("ONEWAY_UNSET_ASSUMED_TWO_WAY")

        sp = assign_speed(
            model_asset_type=a.get("modelAssetType"),
            surface_type=a.get("surfaceType"),
            asset_owner=a.get("assetOwnerOrganisation"),
            urban_rural=ur,
        )
        road_name = name_by_link.get(amds_id)
        number = number_by_link.get(amds_id)
        if not road_name and number:
            road_name = f"SH {number}"

        seen_amds.add(amds_id)
        sources.append(SourceLink(amds_id=amds_id, coords=coords, attrs={
            "source_object_id": a.get("OBJECTID"),
            "road_name": road_name,
            "road_number": number,
            "rca_code": a.get("assetOwnerOrganisation"),
            "rca_name": authority_name.get(a.get("amdsIDAuthority")),
            "model_asset_type": a.get("modelAssetType"),
            "surface_type": a.get("surfaceType"),
            "status": a.get("status"),
            "oneway": oneway,
            "source_length_m": a.get("Shape__Length"),
            "forward_allowed": True,
            "reverse_allowed": oneway != 1,
            "mode_vehicle": a.get("modeVehicle") == 1,
            "mode_vehicle_heavy": a.get("modeVehicleHeavy") == 1,
            "mode_emergency": a.get("modeEmergencyManagement") == 1,
            "mode_ferry": a.get("modeFerry") == 1,
            "lifeline_route": a.get("lifeLineRoute") == 1,
            "shared_infrastructure": a.get("sharedInfrastructure") == 1,
            "detour_available_flag": a.get("detour") == 1,
            "speed_kph": sp.kph,
            "speed_source": sp.source,
            "urban_rural": ur,
            "quality_flags": flags,
        }))

    # --- 5. split at junctions -------------------------------------------
    print("  splitting links at junctions")
    split = split_at_junctions(sources)
    print(f"    {split.parents_split} source links cut at {split.cuts_made} "
          f"junctions, {len(sources)} -> {len(split.links)} links")
    print(f"    {len(split.near_misses)} endpoints within review distance "
          f"but not connected")

    # --- 6. nodes, arcs, components --------------------------------------
    pairs, node_coords = assign_nodes(split.links)
    components = _connected_components(pairs, len(node_coords))
    print(f"    {len(node_coords)} nodes, {max(components) + 1 if components else 0} components")

    analysis_poly = None
    if analysis:
        analysis_poly = shapely.from_wkt(analysis.wkt())

    snapshot_id = "-".join([
        "amds", area, retrieved.date().isoformat(),
        hashlib.sha256(
            (dl.sha256 + LINK_WHERE + json.dumps(ext or {}, sort_keys=True)
             + PROCESSING_VERSION).encode()
        ).hexdigest()[:8],
    ])

    # --- 7. load ----------------------------------------------------------
    print(f"\n  loading into PostGIS as {snapshot_id}")
    notes: list[str] = []
    if dl.missing_ids:
        notes.append(f"{len(dl.missing_ids)} requested ids not returned")
    if dl.duplicate_ids:
        notes.append(f"{len(dl.duplicate_ids)} duplicate ids returned")
    if multipart:
        notes.append(f"{multipart} multipart geometries, first path used")
    if degenerate:
        notes.append(f"{degenerate} degenerate/zero-length features dropped")
    if duplicate:
        notes.append(f"{duplicate} duplicate amdsIDNetworkModel values dropped")
    notes.append(
        f"junction splitting: {split.parents_split} source links cut at "
        f"{split.cuts_made} junctions, {len(sources)} source links -> "
        f"{len(split.links)} graph links"
    )
    notes.append(
        f"{len(split.near_misses)} endpoints lie between the 0.05 m split "
        f"tolerance and 5 m; these were NOT connected and are recorded in near_misses"
    )
    notes.append(
        "AMDS publishes no speed attribute; speeds are estimated "
        "(see nzcl/speed.py)"
    )

    arc_count = _load(
        snapshot_id=snapshot_id,
        links=split.links,
        pairs=pairs,
        node_coords=node_coords,
        components=components,
        near_misses=split.near_misses,
        turns=turns,
        analysis_poly=analysis_poly,
        extract=extract,
        analysis=analysis,
        retrieved=retrieved,
        source_count=source_count,
        downloaded=len(dl.features),
        raw_sha256=dl.sha256,
        source_version=str(link_layer.raw.get("currentVersion", "")),
        status="complete" if not dl.missing_ids and len(dl.features) == len(ids)
               else "partial",
        notes=notes,
        coverage_kind=coverage_kind,
        coverage_name=coverage_name,
    )

    print(f"\nSnapshot written: {snapshot_id}")
    print(f"  links:      {len(split.links)} (from {len(sources)} source links)")
    print(f"  arcs:       {arc_count}")
    print(f"  nodes:      {len(node_coords)}")
    print(f"  components: {max(components) + 1 if components else 0}")
    for n in notes:
        print(f"  note: {n}")
    return snapshot_id


def _load(*, snapshot_id: str, links, pairs, node_coords, components, near_misses,
          turns, analysis_poly, extract, analysis, retrieved, source_count,
          downloaded, raw_sha256, source_version, status, notes,
          coverage_kind: str, coverage_name: str) -> int:
    s = get_settings()
    srid = s.analysis_srid

    with db.direct_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM network_snapshots WHERE snapshot_id = %s",
                        (snapshot_id,))
            cur.execute(
                """
                INSERT INTO network_snapshots (
                  snapshot_id, source_dataset, source_version, retrieved_at_utc,
                  source_url, layer_id, licence, attribution, raw_sha256,
                  processing_version, source_feature_count,
                  downloaded_feature_count, extent_2193, analysis_extent_2193,
                  where_clause, status, notes,
                  coverage_kind, coverage_name, display_extent_2193)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        ST_GeomFromText(%s,%s), ST_GeomFromText(%s,%s),
                        %s,%s,%s,
                        %s,%s,ST_GeomFromText(%s,%s))
                """,
                (snapshot_id,
                 "NZTA AMDS Network Model (AMDS_NetworkModel_PROD)",
                 source_version, retrieved,
                 f"{s.amds_feature_service_url}/{s.amds_link_layer_id}",
                 s.amds_link_layer_id,
                 "Published by NZTA Waka Kotahi for open access and consumption. "
                 "No explicit licence string is set on the ArcGIS item; "
                 "see docs/LICENSING.md.",
                 DEFAULT_ATTRIBUTION, raw_sha256, PROCESSING_VERSION,
                 source_count, downloaded,
                 extract.wkt() if extract else None, srid,
                 analysis.wkt() if analysis else None, srid,
                 LINK_WHERE, status, notes,
                 # Coverage is known exactly here, at the moment the extract is
                 # defined. Recording it removes the guesswork that previously
                 # labelled every clipped snapshot "Wellington pilot".
                 coverage_kind, coverage_name,
                 # The display extent is what the map fits with nothing
                 # selected. National has none: it fits the country.
                 (analysis.wkt() if analysis else None), srid),
            )

            # nodes
            with cur.copy(
                "COPY nodes (snapshot_id, node_id, geom_2193, geom_4326, "
                "component_id) FROM STDIN"
            ) as cp:
                for nid, (x, y) in enumerate(node_coords):
                    pt = Point(x, y)
                    cp.write_row((snapshot_id, nid, _ewkb(pt, srid),
                                  _ewkb(_to_4326_point(x, y), 4326),
                                  components[nid]))

            # links
            with cur.copy(
                "COPY links (snapshot_id, link_id, amds_id, closure_group_id, "
                "source_object_id, road_name, road_number, rca_code, rca_name, "
                "model_asset_type, surface_type, status, oneway, geom_2193, "
                "geom_4326, source_node, target_node, length_m, source_length_m, "
                "forward_allowed, reverse_allowed, mode_vehicle, "
                "mode_vehicle_heavy, mode_emergency, mode_ferry, lifeline_route, "
                "shared_infrastructure, detour_available_flag, speed_kph, "
                "speed_source, urban_rural, in_analysis_area, quality_flags) "
                "FROM STDIN"
            ) as cp:
                for lid, link in enumerate(links):
                    a = link.attrs
                    line = LineString(link.coords)
                    in_area = True
                    if analysis_poly is not None:
                        in_area = line.intersects(analysis_poly)
                    cp.write_row((
                        snapshot_id, lid, link.amds_id, link.closure_group_id,
                        a.get("source_object_id"), a.get("road_name"),
                        a.get("road_number"), a.get("rca_code"), a.get("rca_name"),
                        a.get("model_asset_type"), a.get("surface_type"),
                        a.get("status"), a.get("oneway"),
                        _ewkb(line, srid),
                        _ewkb(_to_4326_line(link.coords), 4326),
                        pairs[lid][0], pairs[lid][1], link.length_m,
                        a.get("source_length_m"),
                        a.get("forward_allowed", True), a.get("reverse_allowed", True),
                        a.get("mode_vehicle", True), a.get("mode_vehicle_heavy", True),
                        a.get("mode_emergency", True), a.get("mode_ferry", False),
                        a.get("lifeline_route", False),
                        a.get("shared_infrastructure", False),
                        a.get("detour_available_flag", False),
                        a.get("speed_kph"), a.get("speed_source", "none"),
                        a.get("urban_rural"), in_area, link.quality_flags,
                    ))

            # arcs: one row per permitted direction
            arc_id = 0
            with cur.copy(
                "COPY arcs (snapshot_id, arc_id, link_id, closure_group_id, "
                "source, target, direction, cost_distance_m, cost_time_s, "
                "time_cost_valid, mode_vehicle, mode_vehicle_heavy, "
                "mode_emergency) FROM STDIN"
            ) as cp:
                for lid, link in enumerate(links):
                    src_node, tgt_node = pairs[lid]
                    if src_node == tgt_node:
                        continue  # self-loop: unusable for routing
                    a = link.attrs
                    speed = a.get("speed_kph") or 0.0
                    time_s = link.length_m / (speed * 1000 / 3600) if speed > 0 else None
                    for direction, u, v, allowed in (
                        ("forward", src_node, tgt_node, a.get("forward_allowed", True)),
                        ("reverse", tgt_node, src_node, a.get("reverse_allowed", True)),
                    ):
                        if not allowed:
                            continue
                        cp.write_row((
                            snapshot_id, arc_id, lid, link.closure_group_id,
                            u, v, direction, link.length_m, time_s,
                            time_s is not None,
                            a.get("mode_vehicle", True),
                            a.get("mode_vehicle_heavy", True),
                            a.get("mode_emergency", True),
                        ))
                        arc_id += 1

            # near misses
            if near_misses:
                with cur.copy(
                    "COPY near_misses (snapshot_id, amds_id, other_amds_id, "
                    "distance_m, geom_2193) FROM STDIN"
                ) as cp:
                    for nm in near_misses[:50_000]:
                        cp.write_row((snapshot_id, nm.amds_id, nm.other_amds_id,
                                      nm.distance_m,
                                      _ewkb(Point(nm.x, nm.y), srid)))

            # turn restrictions, resolved to a connected chain of graph links
            _load_turns(cur, snapshot_id, turns, links, pairs)

            # Statistics for the planner. `arcs` was just COPYed inside this
            # open transaction, so autovacuum has not seen it and it carries
            # none. Measured as NOT the cause of the national hang below - the
            # planner chose a hash join either way - but the estimates are used
            # by everything downstream, so it is worth a second here.
            cur.execute("ANALYZE arcs")
            cur.execute("ANALYZE nodes")
            cur.execute("ANALYZE links")

            # Edge-expanded graph, used when a route would violate a turn
            # restriction. Built here so a snapshot is routable the moment it
            # lands, rather than depending on a separate step.
            #
            # GUARDED, because this join is quadratic in node degree and a
            # single degenerate node can make it effectively non-terminating.
            # A national run spun at 99% CPU for 74 minutes and wrote nothing;
            # Wellington, whose worst node has out-degree 12, completes the
            # same function in 1.2 seconds.
            _build_transitions(cur, snapshot_id)

            cur.execute(
                "UPDATE network_snapshots SET routable_link_count=%s, "
                "arc_count=%s, node_count=%s WHERE snapshot_id=%s",
                (len(links), arc_id, len(node_coords), snapshot_id),
            )
        conn.commit()

    with db.direct_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("ANALYZE links; ANALYZE arcs; ANALYZE nodes;")
    return arc_id


def _load_turns(cur, snapshot_id: str, turns, links, pairs) -> None:
    """Resolve AMDS turn restrictions to chains of GRAPH links.

    Splitting means one source link can be several graph links, so a restriction
    sequence has to be resolved to a CONNECTED chain of pieces. Where no such
    chain exists the restriction is dropped and counted rather than guessed at.
    """
    children: dict[str, list[int]] = {}
    for lid, link in enumerate(links):
        children.setdefault(link.closure_group_id, []).append(lid)

    def shared(a: int, b: int) -> bool:
        return bool({pairs[a][0], pairs[a][1]} & {pairs[b][0], pairs[b][1]})

    def resolve(parents: list[str]) -> list[int] | None:
        options = [children.get(p, []) for p in parents]
        if any(not o for o in options):
            return None
        chain: list[int] = []

        def walk(i: int) -> bool:
            if i == len(options):
                return True
            for cand in options[i]:
                if i > 0 and not shared(chain[i - 1], cand):
                    continue
                chain.append(cand)
                if walk(i + 1):
                    return True
                chain.pop()
            return False

        return list(chain) if walk(0) else None

    rid = 0
    for t in turns:
        if t.get("status") != 1:
            continue
        seq = [t.get(f"amdsIDNetworkModel{i}") for i in range(1, 9)]
        seq = [x for x in seq if x]
        if len(seq) < 2:
            continue
        chain = resolve(seq)
        if not chain:
            continue
        cur.execute(
            "INSERT INTO turn_restrictions (snapshot_id, restriction_id, "
            "amds_restricted_turn_id, link_seq, restricted_vehicle, "
            "restricted_heavy, restricted_emergency) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (snapshot_id, rid, t.get("amdsIDRestrictedTurn"), chain,
             t.get("modeVehicleRestricted") == 1,
             t.get("modeVehicleHeavyRestricted") == 1,
             t.get("modeEmergencyRestricted") == 1),
        )
        rid += 1


def _parse_bbox(text: str) -> Bbox:
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "bbox must be xmin,ymin,xmax,ymax in EPSG:2193")
    return Bbox(*parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest the AMDS Network Model")
    ap.add_argument("--national", action="store_true")
    ap.add_argument("--pilot", choices=sorted(PILOTS))
    ap.add_argument("--bbox", type=_parse_bbox)
    ap.add_argument("--analysis-bbox", type=_parse_bbox)
    ap.add_argument("--name")
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args(argv)

    db.migrate()
    run(national=args.national, pilot=args.pilot, bbox=args.bbox,
        analysis_bbox=args.analysis_bbox, name=args.name,
        concurrency=args.concurrency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
