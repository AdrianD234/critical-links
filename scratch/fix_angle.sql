-- The first angle column was wrong: an azimuth difference runs 0..360, so
-- `least(x, 180-x)` went negative for 3,423 rows. Fold to 0..90 properly.
ALTER TABLE scratch_features DROP COLUMN IF EXISTS angle_deg;
ALTER TABLE scratch_features
  ADD COLUMN angle_deg double precision
  GENERATED ALWAYS AS (
    CASE WHEN mod(raw_angle::numeric, 180.0) > 90
         THEN 180.0 - mod(raw_angle::numeric, 180.0)
         ELSE mod(raw_angle::numeric, 180.0) END
  ) STORED;
ANALYZE scratch_features;

SELECT width_bucket(angle_deg, 0, 90, 9) AS b,
       (width_bucket(angle_deg, 0, 90, 9)-1)*10 || '-' ||
       width_bucket(angle_deg, 0, 90, 9)*10 || ' deg' AS band,
       count(*)
  FROM scratch_features GROUP BY 1 ORDER BY 1;

SELECT link_a, link_b, round(angle_deg::numeric,1) AS angle
  FROM scratch_features WHERE (link_a,link_b) IN ((232709,234053),(232708,234875));
