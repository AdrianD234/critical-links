-- Attach LINZ Topo50 structure evidence to every crossing.
--
-- A mapped bridge or tunnel near a crossing is only evidence if it lies ALONG
-- one of the two roads - that is what "this road is on a structure here" looks
-- like. A bridge that crosses both at an angle is a river bridge that happens
-- to be nearby.
\timing on
\set snap 'amds-national-2026-07-28-5b359d84'

ALTER TABLE scratch_features
  ADD COLUMN IF NOT EXISTS struct_dist_m  double precision,
  ADD COLUMN IF NOT EXISTS struct_kind    text,
  ADD COLUMN IF NOT EXISTS struct_use     text,
  ADD COLUMN IF NOT EXISTS struct_align_deg double precision;

WITH nearest AS (
  SELECT f.link_a, f.link_b, f.pt_2193, s.geom_2193 AS sg, s.kind, s.use_1,
         ST_Distance(f.pt_2193, s.geom_2193) AS d
    FROM scratch_features f
    CROSS JOIN LATERAL (
      SELECT e.geom_2193, e.kind, e.use_1
        FROM ext_structures e
       ORDER BY e.geom_2193 <-> f.pt_2193
       LIMIT 1
    ) s
), aligned AS (
  SELECT n.link_a, n.link_b, n.d, n.kind, n.use_1,
         -- azimuth of the structure over a 20 m window at its closest point
         degrees(ST_Azimuth(
            ST_LineInterpolatePoint(n.sg, greatest(
               ST_LineLocatePoint(n.sg, ST_ClosestPoint(n.sg, n.pt_2193))
                 - 10.0/greatest(ST_Length(n.sg),1.0), 0.0)),
            ST_LineInterpolatePoint(n.sg, least(
               ST_LineLocatePoint(n.sg, ST_ClosestPoint(n.sg, n.pt_2193))
                 + 10.0/greatest(ST_Length(n.sg),1.0), 1.0)))) AS saz,
         degrees(ST_Azimuth(
            ST_LineInterpolatePoint(a.geom_2193,
               greatest(ST_LineLocatePoint(a.geom_2193, n.pt_2193)-0.02, 0.0)),
            ST_LineInterpolatePoint(a.geom_2193,
               least(ST_LineLocatePoint(a.geom_2193, n.pt_2193)+0.02, 1.0)))) AS az_a,
         degrees(ST_Azimuth(
            ST_LineInterpolatePoint(b.geom_2193,
               greatest(ST_LineLocatePoint(b.geom_2193, n.pt_2193)-0.02, 0.0)),
            ST_LineInterpolatePoint(b.geom_2193,
               least(ST_LineLocatePoint(b.geom_2193, n.pt_2193)+0.02, 1.0)))) AS az_b
    FROM nearest n
    JOIN links a ON a.snapshot_id = :'snap' AND a.link_id = n.link_a
    JOIN links b ON b.snapshot_id = :'snap' AND b.link_id = n.link_b
), folded AS (
  SELECT link_a, link_b, d, kind, use_1,
         least(
           CASE WHEN mod((saz-az_a)::numeric,180.0) > 90
                THEN 180.0 - mod((saz-az_a)::numeric,180.0)
                ELSE mod((saz-az_a)::numeric,180.0) END,
           CASE WHEN mod((saz-az_b)::numeric,180.0) > 90
                THEN 180.0 - mod((saz-az_b)::numeric,180.0)
                ELSE mod((saz-az_b)::numeric,180.0) END
         ) AS align
    FROM aligned
)
UPDATE scratch_features f
   SET struct_dist_m = g.d, struct_kind = g.kind, struct_use = g.use_1,
       struct_align_deg = g.align
  FROM folded g
 WHERE f.link_a = g.link_a AND f.link_b = g.link_b;

ANALYZE scratch_features;

\echo '=== distance from each crossing to the nearest Topo50 structure ==='
SELECT CASE WHEN struct_dist_m <= 5   THEN 'a. <= 5 m'
            WHEN struct_dist_m <= 10  THEN 'b. <= 10 m'
            WHEN struct_dist_m <= 25  THEN 'c. <= 25 m'
            WHEN struct_dist_m <= 100 THEN 'd. <= 100 m'
            ELSE 'e. > 100 m' END AS band,
       count(*), round(100.0*count(*)/sum(count(*)) OVER (),1) AS pct
  FROM scratch_features GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== ...and of those within 15 m, how well aligned with one of the roads ==='
SELECT CASE WHEN struct_align_deg <= 20 THEN 'aligned (<=20 deg): the road is ON the structure'
            WHEN struct_align_deg <= 45 THEN 'partly aligned (20-45 deg)'
            ELSE 'crosses both (>45 deg): a nearby river bridge' END AS a,
       count(*)
  FROM scratch_features WHERE struct_dist_m <= 15 GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=== the proposed rule: structure within 15 m AND aligned within 20 deg ==='
SELECT struct_kind, struct_use, count(*)
  FROM scratch_features
 WHERE struct_dist_m <= 15 AND struct_align_deg <= 20
 GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '=== how the Darfield crossings score ==='
SELECT link_a, link_b, round(struct_dist_m::numeric,1) AS struct_m,
       struct_kind, round(struct_align_deg::numeric,1) AS align
  FROM scratch_features WHERE (link_a,link_b) IN ((232709,234053),(232708,234875));
