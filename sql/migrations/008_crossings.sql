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

    -- May a shared graph node be created here, under ANY policy? A separate
    -- question from the disposition: the disposition says what the evidence
    -- supports, this says whether acting on it is REPRESENTABLE.
    --
    -- False for tangential grazes, for a road crossing itself, and - the
    -- important one - for MIXED_PLACE. A graph node grants every incident arc
    -- every movement, so at a place where one pair meets at grade and another
    -- passes over, noding the at-grade pair would hand the flyover the same
    -- turns. Nothing at such a place is noded, under any policy.
    safe_to_node    boolean NOT NULL DEFAULT true,

    -- HIGH or MEDIUM. ORDINARY_CROSSROADS is MEDIUM on purpose: its rule is
    -- the absence of contrary evidence, which is not evidence for the
    -- conclusion. JUNCTION_WITNESS is HIGH - a third road ends there.
    confidence      text    NOT NULL DEFAULT 'MEDIUM'
        CHECK (confidence IN ('HIGH', 'MEDIUM')),

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


-- --------------------------------------------------------------------------
-- CREATE TABLE IF NOT EXISTS does not reshape a table that already exists.
--
-- `crossings` was created earlier in this branch's development with a column
-- called `plausible_junction`, which was then replaced by the pair below:
-- `safe_to_node` - may a node be created here under ANY policy - and
-- `confidence`. Editing the CREATE TABLE above upgraded a fresh database and
-- silently left every existing one behind, and the ingest COPY names the new
-- columns, so any machine that had run the earlier version would fail on the
-- next ingest with "column safe_to_node does not exist". Nothing had run it
-- yet only because no snapshot with crossings has been built.
--
-- Written as ALTER ... IF NOT EXISTS so it is idempotent in both directions:
-- a database that already matches the CREATE TABLE is untouched.
ALTER TABLE crossings
    ADD COLUMN IF NOT EXISTS safe_to_node boolean NOT NULL DEFAULT true;
ALTER TABLE crossings
    ADD COLUMN IF NOT EXISTS confidence text NOT NULL DEFAULT 'MEDIUM';
ALTER TABLE crossings DROP COLUMN IF EXISTS plausible_junction;

DO $$
BEGIN
    ALTER TABLE crossings
        ADD CONSTRAINT crossings_confidence_check
        CHECK (confidence IN ('HIGH', 'MEDIUM'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
