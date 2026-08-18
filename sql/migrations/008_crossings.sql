-- Interior-to-interior crossings, and what was decided about each one.
--
-- Two links crossing in plan view with no shared node used to be an unrecorded
-- non-event: `split_at_junctions` declined to node it and said nothing. That
-- silence is what let the Darfield case sit in a shipped national snapshot -
-- a zero-gap crossroads treated as a flyover, adding 3.0 km to a replacement
-- path, with nothing anywhere in the database to notice it by.
--
-- Every crossing is now recorded, INCLUDING the ones left disconnected. The
-- GRADE_SEPARATED rows are a claim the system is making and can be checked
-- against; the UNRESOLVED rows are doubt that has to reach the answer.

CREATE TABLE IF NOT EXISTS crossings (
    snapshot_id     text    NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    crossing_id     bigint  NOT NULL,

    -- The two SOURCE features. Durable across re-ingest; link ids are not,
    -- because link_id is a positional index assigned at load time.
    source_a        text    NOT NULL,
    source_b        text    NOT NULL,

    disposition     text    NOT NULL
        CHECK (disposition IN ('AT_GRADE', 'GRADE_SEPARATED', 'UNRESOLVED')),
    -- The single rule that decided it, e.g. STRUCTURE_MAPPED, TANGENTIAL,
    -- ORDINARY_CROSSROADS. Stable and machine-readable.
    reason          text    NOT NULL,
    detail          text    NOT NULL DEFAULT '',
    evidence        text[]  NOT NULL DEFAULT '{}',

    -- Was this crossing actually cut, under the policy this snapshot was built
    -- with? Not implied by `disposition`: a snapshot built with
    -- crossing_policy='none' records the classification and honours none of it.
    noded           boolean NOT NULL DEFAULT false,

    -- Could this be a junction if the doubt broke the other way? False for
    -- tangential grazes and for a road crossing itself. Only crossings where
    -- this is true belong in the POSSIBLE graph.
    plausible_junction boolean NOT NULL DEFAULT true,

    angle_deg       double precision,
    -- Distinct PLACE this crossing belongs to. Several pairs share one place
    -- where divided carriageways meet, which is why a pair count is not a
    -- count of intersections.
    place_id        integer,

    geom_2193       geometry(Point, 2193) NOT NULL,
    PRIMARY KEY (snapshot_id, crossing_id)
);

CREATE INDEX IF NOT EXISTS crossings_snapshot_idx
    ON crossings (snapshot_id, disposition);
CREATE INDEX IF NOT EXISTS crossings_geom_idx
    ON crossings USING GIST (geom_2193);
CREATE INDEX IF NOT EXISTS crossings_place_idx
    ON crossings (snapshot_id, place_id);
CREATE INDEX IF NOT EXISTS crossings_source_a_idx
    ON crossings (snapshot_id, source_a);
CREATE INDEX IF NOT EXISTS crossings_source_b_idx
    ON crossings (snapshot_id, source_b);


-- Authoritative structures from LINZ Topo50: bridge centrelines (layer-50244)
-- and tunnel centrelines (layer-50366).
--
-- This is the ONLY authoritative evidence of a road-over-road structure
-- available for New Zealand. AMDS has none: no structure attribute in any of
-- its fourteen layers, and its z-values are a LiDAR terrain drape - layer 4's
-- own metadata says zAccuracyMethodUsed is LiDAR on 611,884 of 621,679 rows
-- and Surveyed on none.
--
-- Not snapshot-scoped: it is an external reference dataset, not part of any
-- AMDS snapshot, and re-ingesting AMDS must not discard it.
CREATE TABLE IF NOT EXISTS ext_structures (
    source        text    NOT NULL,
    feature_id    bigint  NOT NULL,
    kind          text    NOT NULL,
    use_1         text,
    name          text,
    geom_2193     geometry(LineString, 2193) NOT NULL,
    PRIMARY KEY (source, feature_id)
);

CREATE INDEX IF NOT EXISTS ext_structures_geom_idx
    ON ext_structures USING GIST (geom_2193);
