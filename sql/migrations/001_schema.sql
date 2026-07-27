-- NZ Critical Links - canonical schema.
--
-- Everything is snapshot-scoped. A snapshot is immutable: re-ingesting the
-- source produces a new snapshot_id, so cached detour results become
-- unreachable rather than silently stale.
--
-- Geometry is stored in EPSG:2193 (NZTM2000) because every distance in this
-- system is measured in projected metres. A WGS84 column is carried alongside
-- purely for web delivery; nothing measures against it.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- ---------------------------------------------------------------- snapshots
CREATE TABLE IF NOT EXISTS network_snapshots (
    snapshot_id             text PRIMARY KEY,
    source_dataset          text        NOT NULL,
    source_version          text,
    retrieved_at_utc        timestamptz NOT NULL,
    source_updated_at       timestamptz,
    source_url              text        NOT NULL,
    layer_id                integer     NOT NULL,
    licence                 text        NOT NULL,
    attribution             text        NOT NULL,
    raw_sha256              text        NOT NULL,
    processing_version      text        NOT NULL,
    -- Count the service reported at extraction time, and what we actually got.
    source_feature_count    integer     NOT NULL,
    downloaded_feature_count integer    NOT NULL,
    routable_link_count     integer     NOT NULL DEFAULT 0,
    arc_count               integer     NOT NULL DEFAULT 0,
    node_count              integer     NOT NULL DEFAULT 0,
    -- Extraction extent, and the smaller area results are reported for. Links
    -- between the two are network buffer: they keep near-edge detours valid.
    extent_2193             geometry(Polygon, 2193),
    analysis_extent_2193    geometry(Polygon, 2193),
    where_clause            text        NOT NULL,
    status                  text        NOT NULL
                            CHECK (status IN ('complete','partial','failed')),
    notes                   text[]      NOT NULL DEFAULT '{}'
);

-- -------------------------------------------------------------------- nodes
CREATE TABLE IF NOT EXISTS nodes (
    snapshot_id     text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    node_id         bigint  NOT NULL,
    geom_2193       geometry(Point, 2193) NOT NULL,
    geom_4326       geometry(Point, 4326) NOT NULL,
    -- Weakly connected component. Lets an unreachable pair be rejected without
    -- running a search at all.
    component_id    integer NOT NULL DEFAULT -1,
    quality_flags   text[]  NOT NULL DEFAULT '{}',
    PRIMARY KEY (snapshot_id, node_id)
);

-- -------------------------------------------------------------------- links
-- One row per GRAPH link. A source AMDS feature that was split at junctions
-- yields several rows, all sharing closure_group_id.
CREATE TABLE IF NOT EXISTS links (
    snapshot_id             text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    link_id                 bigint  NOT NULL,
    -- Durable AMDS identifier. Suffixed "#n" where the source link was split.
    amds_id                 text    NOT NULL,
    -- The unsuffixed parent id. Closing a road closes every piece of it.
    closure_group_id        text    NOT NULL,
    -- ArcGIS OBJECTID: traceability only, NOT durable across republishes.
    source_object_id        bigint,
    road_name               text,
    road_number             text,
    rca_code                integer,
    rca_name                text,
    model_asset_type        smallint,
    surface_type            smallint,
    status                  smallint,
    oneway                  smallint,
    geom_2193               geometry(LineString, 2193) NOT NULL,
    geom_4326               geometry(LineString, 4326) NOT NULL,
    source_node             bigint  NOT NULL,
    target_node             bigint  NOT NULL,
    length_m                double precision NOT NULL CHECK (length_m > 0),
    -- Shape__Length as published, in the service's own SR. Cross-check only.
    source_length_m         double precision,
    forward_allowed         boolean NOT NULL,
    reverse_allowed         boolean NOT NULL,
    mode_vehicle            boolean NOT NULL DEFAULT true,
    mode_vehicle_heavy      boolean NOT NULL DEFAULT true,
    mode_emergency          boolean NOT NULL DEFAULT true,
    mode_ferry              boolean NOT NULL DEFAULT false,
    lifeline_route          boolean NOT NULL DEFAULT false,
    shared_infrastructure   boolean NOT NULL DEFAULT false,
    detour_available_flag   boolean NOT NULL DEFAULT false,
    -- AMDS publishes NO speed attribute. Every value here is derived; the
    -- source column records how, and nothing may present it as observed.
    speed_kph               double precision,
    speed_source            text    NOT NULL DEFAULT 'none',
    urban_rural             text,
    -- 1 inside the analysis area, 0 when the link exists only as buffer.
    in_analysis_area        boolean NOT NULL DEFAULT true,
    quality_flags           text[]  NOT NULL DEFAULT '{}',
    PRIMARY KEY (snapshot_id, link_id)
);

-- --------------------------------------------------------------------- arcs
-- One row per DIRECTED traversal. This is the table pgRouting reads.
--
-- pgRouting convention: a row with cost >= 0 and reverse_cost < 0 is a one-way
-- edge. We generate one arc row per permitted direction and set reverse_cost
-- to -1 throughout, so directionality is explicit in the data rather than
-- implied by a sign convention on a shared row.
CREATE TABLE IF NOT EXISTS arcs (
    snapshot_id         text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    arc_id              bigint  NOT NULL,
    link_id             bigint  NOT NULL,
    closure_group_id    text    NOT NULL,
    source              bigint  NOT NULL,
    target              bigint  NOT NULL,
    direction           text    NOT NULL CHECK (direction IN ('forward','reverse')),
    cost_distance_m     double precision NOT NULL,
    cost_time_s         double precision,
    time_cost_valid     boolean NOT NULL DEFAULT false,
    mode_vehicle        boolean NOT NULL DEFAULT true,
    mode_vehicle_heavy  boolean NOT NULL DEFAULT true,
    mode_emergency      boolean NOT NULL DEFAULT true,
    PRIMARY KEY (snapshot_id, arc_id)
);

-- ------------------------------------------------------- turn restrictions
-- A banned manoeuvre: traversing link_seq in order is prohibited.
CREATE TABLE IF NOT EXISTS turn_restrictions (
    snapshot_id             text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    restriction_id          bigint  NOT NULL,
    amds_restricted_turn_id text,
    link_seq                bigint[] NOT NULL,
    restricted_vehicle      boolean NOT NULL DEFAULT false,
    restricted_heavy        boolean NOT NULL DEFAULT false,
    restricted_emergency    boolean NOT NULL DEFAULT false,
    PRIMARY KEY (snapshot_id, restriction_id)
);

-- ---------------------------------------------------------- detour results
CREATE TABLE IF NOT EXISTS detour_results (
    snapshot_id             text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    link_id                 bigint  NOT NULL,
    closure_group_id        text    NOT NULL,
    vehicle_profile         text    NOT NULL,
    metric                  text    NOT NULL,
    closure_scope           text    NOT NULL,
    direction               text    NOT NULL,
    -- OK / DISCONNECTED / UNRESOLVED_TIMEOUT / INVALID_GRAPH /
    -- SOURCE_DATA_ERROR / UNSUPPORTED_PROFILE / API_ERROR. Never conflated.
    status                  text    NOT NULL,
    source_node             bigint,
    target_node             bigint,
    selected_link_length_m  double precision,
    normal_path_distance_m  double precision,
    alternative_distance_m  double precision,
    added_distance_vs_link_m double precision,
    network_penalty_m       double precision,
    detour_ratio_vs_link    double precision,
    normal_path_time_s      double precision,
    alternative_time_s      double precision,
    added_time_s            double precision,
    -- Corridor measure, used where the endpoint measure is undefined.
    corridor_status         text,
    corridor_normal_m       double precision,
    corridor_alternative_m  double precision,
    corridor_penalty_m      double precision,
    corridor_hops_upstream  integer,
    corridor_hops_downstream integer,
    -- What is stranded when nothing gets past.
    isolation_side          text,
    isolation_link_count    integer,
    isolation_length_m      double precision,
    route_arc_ids           bigint[],
    removed_arc_ids         bigint[],
    calculated_at_utc       timestamptz NOT NULL DEFAULT now(),
    algorithm               text    NOT NULL,
    algorithm_version       text    NOT NULL,
    runtime_ms              integer,
    quality_flags           text[]  NOT NULL DEFAULT '{}',
    error_detail            text,
    PRIMARY KEY (snapshot_id, link_id, vehicle_profile, metric,
                 closure_scope, direction, algorithm_version)
);

-- ------------------------------------------------------------- QA findings
CREATE TABLE IF NOT EXISTS qa_issues (
    snapshot_id     text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    issue_id        bigserial,
    severity        text    NOT NULL CHECK (severity IN ('error','warning','info')),
    issue_type      text    NOT NULL,
    entity_type     text    NOT NULL,
    entity_id       text,
    geom_2193       geometry(Geometry, 2193),
    count           integer NOT NULL DEFAULT 1,
    detail          text    NOT NULL,
    sample_ids      text[],
    detected_at_utc timestamptz NOT NULL DEFAULT now(),
    resolution_status text  NOT NULL DEFAULT 'open',
    PRIMARY KEY (snapshot_id, issue_id)
);

-- Endpoints close to another link but deliberately NOT connected. A genuine
-- gap in the source, or a tolerance that is too tight - a data-steward call.
CREATE TABLE IF NOT EXISTS near_misses (
    snapshot_id     text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    id              bigserial,
    amds_id         text    NOT NULL,
    other_amds_id   text    NOT NULL,
    distance_m      double precision NOT NULL,
    geom_2193       geometry(Point, 2193) NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

-- ------------------------------------------------------------------ indexes
CREATE INDEX IF NOT EXISTS links_geom_2193_idx  ON links USING GIST (geom_2193);
CREATE INDEX IF NOT EXISTS links_geom_4326_idx  ON links USING GIST (geom_4326);
CREATE INDEX IF NOT EXISTS links_snapshot_idx   ON links (snapshot_id);
CREATE INDEX IF NOT EXISTS links_amds_idx       ON links (snapshot_id, amds_id);
CREATE INDEX IF NOT EXISTS links_group_idx      ON links (snapshot_id, closure_group_id);
CREATE INDEX IF NOT EXISTS links_name_idx       ON links USING GIN (to_tsvector('simple', coalesce(road_name,'')));
CREATE INDEX IF NOT EXISTS links_analysis_idx   ON links (snapshot_id) WHERE in_analysis_area;

CREATE INDEX IF NOT EXISTS nodes_geom_idx       ON nodes USING GIST (geom_2193);
CREATE INDEX IF NOT EXISTS nodes_component_idx  ON nodes (snapshot_id, component_id);

-- The routing hot path: pgr_dijkstra reads (source, target, cost) filtered by
-- snapshot and mode.
CREATE INDEX IF NOT EXISTS arcs_snapshot_idx    ON arcs (snapshot_id);
CREATE INDEX IF NOT EXISTS arcs_source_idx      ON arcs (snapshot_id, source);
CREATE INDEX IF NOT EXISTS arcs_target_idx      ON arcs (snapshot_id, target);
CREATE INDEX IF NOT EXISTS arcs_link_idx        ON arcs (snapshot_id, link_id);
CREATE INDEX IF NOT EXISTS arcs_group_idx       ON arcs (snapshot_id, closure_group_id);

CREATE INDEX IF NOT EXISTS detour_link_idx      ON detour_results (snapshot_id, link_id);
CREATE INDEX IF NOT EXISTS detour_status_idx    ON detour_results (snapshot_id, status);
CREATE INDEX IF NOT EXISTS near_miss_snap_idx   ON near_misses (snapshot_id);
