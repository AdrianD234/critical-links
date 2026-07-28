-- Coverage, recorded rather than inferred.
--
-- The application used to work out what a snapshot covered from
-- `analysis_extent_2193 IS NOT NULL`, and then labelled anything clipped
-- "Wellington pilot". That is wrong twice over: an Auckland or custom extract
-- would have been announced as Wellington, and a national snapshot is not
-- distinguishable from a very large regional one by extent alone.
--
-- Coverage is a property of the ingest, known exactly at the moment the extract
-- is defined. It belongs in the row.
--
-- Idempotent, like every migration here: there is no applied-migrations ledger
-- to drift out of step with reality, so re-running must be safe.

ALTER TABLE network_snapshots
    -- 'national'  the whole country, no extent filter
    -- 'regional'  a clipped pilot or custom extract
    -- 'synthetic' a hand-built fixture for tests and CI
    ADD COLUMN IF NOT EXISTS coverage_kind text,
    -- Human-facing: 'New Zealand', 'Wellington pilot', 'CI fixture'.
    ADD COLUMN IF NOT EXISTS coverage_name text,
    -- Where the map should sit when nothing is selected. For a national
    -- snapshot this is New Zealand; for a pilot, the area it covers. Distinct
    -- from analysis_extent_2193, which is the smaller area results are
    -- reported for and excludes the surrounding network buffer.
    ADD COLUMN IF NOT EXISTS display_extent_2193 geometry(Polygon, 2193);

-- Backfill anything ingested before this column existed, from the only
-- evidence available for those rows. New ingests set it explicitly.
--
-- The id prefix is used only for the backfill and never at read time: it is a
-- naming convention, and conventions are exactly what stops being true.
UPDATE network_snapshots
   SET coverage_kind = CASE
           WHEN source_dataset = 'synthetic'            THEN 'synthetic'
           WHEN analysis_extent_2193 IS NULL            THEN 'national'
           ELSE 'regional'
       END
 WHERE coverage_kind IS NULL;

UPDATE network_snapshots
   SET coverage_name = CASE
           WHEN coverage_kind = 'national'  THEN 'New Zealand'
           WHEN coverage_kind = 'synthetic' THEN 'Synthetic fixture'
           WHEN snapshot_id LIKE '%wellington%' THEN 'Wellington pilot'
           WHEN snapshot_id LIKE '%auckland%'   THEN 'Auckland pilot'
           ELSE 'Regional extract'
       END
 WHERE coverage_name IS NULL;

-- Absent a recorded display extent, the analysis extent is the honest
-- fallback for a regional snapshot; national needs none, because the map
-- fits the country.
UPDATE network_snapshots
   SET display_extent_2193 = analysis_extent_2193
 WHERE display_extent_2193 IS NULL
   AND analysis_extent_2193 IS NOT NULL;

CREATE INDEX IF NOT EXISTS network_snapshots_coverage_idx
    ON network_snapshots (coverage_kind, status, retrieved_at_utc DESC);
