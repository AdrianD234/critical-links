-- Road names as a layer over the graph, not a column inside it.
--
-- 62.7% of graph links render as "(unnamed link)". Fixing that means reading
-- AMDS properly and then bringing in external sources, and both of those are
-- naming work - they say nothing about where the road goes. Putting the result
-- in `links` would mean rewriting rows that the routing engine reads, on a
-- table whose contents are the evidence that a detour result is reproducible.
--
-- So naming lives here instead:
--
--   * `links` is never updated by any naming process. The topology tables are
--     provably untouched, which is what lets the before/after routing proof be
--     an equality check rather than an argument.
--   * a name is attached to the AMDS SOURCE FEATURE (closure_group_id), not to
--     a graph link. Junction splitting turns one road into several links;
--     naming each piece independently is how one road ends up with three
--     different names. The view fans the name back out to the children.
--   * every name carries where it came from, which field, and how confident
--     the match was - including the ones that are only "officially unnamed".
--
-- Idempotent, like every migration here.

-- --------------------------------------------------------------- link_names
CREATE TABLE IF NOT EXISTS link_names (
    snapshot_id           text NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    -- The unsuffixed AMDS id: one row per source feature.
    closure_group_id      text NOT NULL,

    -- What the interface shows in the road-name position. NULL is legitimate
    -- and means "no name to show", which the UI must phrase honestly.
    display_name          text,
    -- amds_named | route_designation_only | externally_enriched |
    -- officially_unnamed | ambiguous_conflict | unresolved
    name_status           text NOT NULL,
    -- amds_routename | nzta_street_names | linz_road_sections |
    -- nzta_ramm_carriageway
    name_source           text,
    -- The exact field the classification came from, e.g. 'routeNameFull' or
    -- 'isunnamed'. Recorded because "confirmed by LINZ" and "confirmed by an
    -- NZTA-hosted layer whose identifiers align with LINZ" are not the same
    -- claim.
    source_field          text,

    -- The native AMDS name, canonical Unicode. Never overwritten by an
    -- external source: where they disagree the record becomes a conflict.
    native_name           text,
    -- Macron-folded search key, derived from native_name here rather than read
    -- from AMDS's own ASCII column, which is separately maintained and can
    -- disagree with the name it claims to transliterate.
    native_name_key       text,

    -- "State Highway 3". A designation, shown alongside a street name and used
    -- as the display name only when there is no street name.
    route_designation     text,
    -- The raw AMDS string the designation was normalised from, e.g.
    -- "SH 1S/774", kept so the normalisation is auditable.
    designation_raw       text,

    alternates            text[] NOT NULL DEFAULT '{}',
    route_name_ids        text[] NOT NULL DEFAULT '{}',
    primary_route_name_id text,
    effective_from        timestamptz,
    effective_to          timestamptz,

    -- True when sources disagree. A conflict is displayed as a conflict; it is
    -- never resolved silently in favour of whichever source was read last.
    conflict              boolean NOT NULL DEFAULT false,
    is_ramp               boolean NOT NULL DEFAULT false,
    locality_code         smallint,

    -- External enrichment. Populated only when AMDS had nothing.
    external_name         text,
    external_source       text,
    external_ref          text,
    match_confidence      text,
    match_score           double precision,
    match_evidence        jsonb,

    notes                 text[] NOT NULL DEFAULT '{}',
    updated_at            timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (snapshot_id, closure_group_id),
    CONSTRAINT link_names_status_check CHECK (name_status IN (
        'amds_named', 'route_designation_only', 'externally_enriched',
        'officially_unnamed', 'ambiguous_conflict', 'unresolved')),
    CONSTRAINT link_names_confidence_check CHECK (match_confidence IS NULL
        OR match_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'NONE'))
);

CREATE INDEX IF NOT EXISTS link_names_status_idx
    ON link_names (snapshot_id, name_status);

-- Search reads the folded key and the display name together, so both are in
-- the document. Mirrors links_name_idx, which stays where it is.
CREATE INDEX IF NOT EXISTS link_names_search_idx
    ON link_names USING GIN (to_tsvector('simple',
        coalesce(display_name, '') || ' ' || coalesce(native_name_key, '')));

-- ------------------------------------------------------- match candidates
-- Every candidate an external source offered, not only the winner. Without
-- the rejected ones there is no way to ask why a match was chosen, or to
-- re-score after a rule changes without re-running the spatial work.
CREATE TABLE IF NOT EXISTS road_name_candidates (
    snapshot_id       text NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    closure_group_id  text NOT NULL,
    source            text NOT NULL,
    -- 1 = best scoring. Rank is assigned by the matcher, not by the database.
    candidate_rank    integer NOT NULL,
    candidate_name    text,
    -- The source's own stable identifier for the feature that matched.
    candidate_ref     text,
    -- NZTA street names only: the source's explicit unnamed classification.
    is_unnamed        boolean,
    score             double precision,
    evidence          jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, closure_group_id, source, candidate_rank)
);

CREATE INDEX IF NOT EXISTS road_name_candidates_group_idx
    ON road_name_candidates (snapshot_id, closure_group_id);

-- ---------------------------------------------------------------- the view
-- Fans a source-feature name out to every graph link split from it, and falls
-- back to the value ingested into `links` for any snapshot that has not been
-- through the naming pass. The fallback is what makes this safe to deploy
-- before the backfill runs.
-- Dropped rather than replaced: a later migration widens this view, and
-- CREATE OR REPLACE cannot narrow one back on a re-run. Migrations here are
-- re-run routinely, so "idempotent" has to mean idempotent in sequence, not
-- just individually.
DROP VIEW IF EXISTS link_display_names;

CREATE VIEW link_display_names AS
SELECT
    l.snapshot_id,
    l.link_id,
    l.closure_group_id,
    coalesce(n.display_name, l.road_name)              AS display_name,
    coalesce(n.name_status,
             CASE WHEN l.road_name IS NULL THEN 'unresolved'
                  ELSE 'amds_named' END)               AS name_status,
    coalesce(n.name_source,
             CASE WHEN l.road_name IS NULL THEN NULL
                  ELSE 'amds_routename' END)           AS name_source,
    n.source_field,
    n.native_name,
    n.native_name_key,
    n.route_designation,
    n.alternates,
    coalesce(n.conflict, false)                        AS conflict,
    coalesce(n.is_ramp, false)                         AS is_ramp,
    n.external_source,
    n.match_confidence,
    l.road_number
FROM links l
LEFT JOIN link_names n
       ON n.snapshot_id = l.snapshot_id
      AND n.closure_group_id = l.closure_group_id;

-- ------------------------------------------------------- naming provenance
-- One row per naming pass, so a coverage figure can always be traced to the
-- rules and source versions that produced it.
CREATE TABLE IF NOT EXISTS naming_runs (
    snapshot_id     text NOT NULL REFERENCES network_snapshots ON DELETE CASCADE,
    run_id          text NOT NULL,
    stage           text NOT NULL,
    started_at_utc  timestamptz NOT NULL DEFAULT now(),
    finished_at_utc timestamptz,
    naming_version  text NOT NULL,
    sources         jsonb NOT NULL DEFAULT '{}'::jsonb,
    counts          jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes           text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (snapshot_id, run_id)
);
