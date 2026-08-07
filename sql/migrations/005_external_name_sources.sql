-- Reference geometry from the external sources that can supply a road name.
--
-- These tables are NOT part of the network. Nothing routes over them, nothing
-- joins them to `arcs`, and no snapshot depends on them: they exist so that an
-- offline matching pass can ask "what does another authority call the road at
-- these coordinates". They carry no snapshot_id for the same reason - a street
-- name is a fact about the country, not about one extract of it.
--
-- One table for all three sources rather than three shaped alike. The matcher
-- then has a single query to write and a single place where a source's
-- provenance can go missing, and `source` is never optional.
--
-- Idempotent, like every migration here.

CREATE TABLE IF NOT EXISTS ext_road_names (
    -- nzta_street_names | linz_road_sections | nzta_ramm_carriageway
    source                text NOT NULL,
    -- The source's own stable identifier, as text. LINZ road_section_id, the
    -- NZTA object id, the RAMM carriageway key.
    feature_id            text NOT NULL,
    -- Multipart geometry is exploded rather than stored as a multi, so every
    -- row is a simple line and start/end/heading are always well defined.
    part                  smallint NOT NULL DEFAULT 0,

    -- The best human-readable name this source offers for the road. NULL where
    -- the source holds no name, which for NZTA street names is a positive
    -- statement rather than a gap - see is_unnamed.
    display_name          text,
    -- Aggressively normalised key for cross-source agreement tests. Never
    -- displayed.
    name_key              text,

    -- NZTA street names only: the source's own classification. This is the
    -- only field in any available source that distinguishes a road that has no
    -- name from a road whose name we failed to find.
    is_unnamed            boolean,
    is_state_highway      boolean,
    is_private            boolean,
    is_dual_carriageway   boolean,
    oneway                text,
    status                text,

    locality              text,
    locality_alt          text,
    territorial_authority text,
    territorial_authority_alt text,

    -- NZTA street names publishes LINZ road-section identifiers as a
    -- comma-separated LIST, so this is many-to-one and is parsed, never
    -- compared as a string.
    linz_road_section_ids bigint[],

    -- RAMM only. A corridor spans tens or hundreds of kilometres
    -- ("Hamilton to New Plymouth") and is never a road display name.
    corridor              text,
    -- RAMM only. "003-0076" is a route-section code, not a name either.
    route_code            text,

    extra                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    length_m              double precision NOT NULL,
    geom_2193             geometry(LineString, 2193) NOT NULL,

    PRIMARY KEY (source, feature_id, part)
);

CREATE INDEX IF NOT EXISTS ext_road_names_geom_idx
    ON ext_road_names USING GIST (geom_2193);
CREATE INDEX IF NOT EXISTS ext_road_names_source_idx
    ON ext_road_names (source);
CREATE INDEX IF NOT EXISTS ext_road_names_linz_ids_idx
    ON ext_road_names USING GIN (linz_road_section_ids);
CREATE INDEX IF NOT EXISTS ext_road_names_key_idx
    ON ext_road_names (source, name_key);

-- --------------------------------------------------------------- provenance
-- When each source was read, from where, and what came back. A coverage claim
-- that cannot name its inputs is not evidence.
CREATE TABLE IF NOT EXISTS ext_source_runs (
    source          text NOT NULL,
    acquired_at_utc timestamptz NOT NULL,
    service_url     text NOT NULL,
    feature_count   integer NOT NULL,
    row_count       integer NOT NULL,
    srid            integer NOT NULL,
    -- Empty for both NZTA layers as published; recorded as such rather than
    -- assumed. No external name reaches the interface until this is resolved.
    licence         text,
    attribution     text,
    notes           text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (source, acquired_at_utc)
);
