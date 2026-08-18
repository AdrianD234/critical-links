-- mod() in Postgres keeps the sign of the dividend, so mod(-66,180) = -66 and
-- a "<= 20 degrees" test silently accepted every negative angle. Fold to a
-- true 0..90 before comparing.
UPDATE scratch_features
   SET struct_align_deg = CASE
         WHEN mod(mod(struct_align_deg::numeric, 180.0) + 180.0, 180.0) > 90
         THEN 180.0 - mod(mod(struct_align_deg::numeric, 180.0) + 180.0, 180.0)
         ELSE mod(mod(struct_align_deg::numeric, 180.0) + 180.0, 180.0) END
 WHERE struct_align_deg IS NOT NULL;
ANALYZE scratch_features;

\echo '=== alignment of the nearest structure, for crossings within 15 m ==='
SELECT CASE WHEN struct_align_deg <= 20 THEN 'a. aligned <=20 deg (the road is ON it)'
            WHEN struct_align_deg <= 45 THEN 'b. 20-45 deg'
            ELSE 'c. >45 deg (crosses both: a nearby river bridge)' END AS a,
       count(*)
  FROM scratch_features WHERE struct_dist_m <= 15 GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== the rule as corrected ==='
SELECT count(*) FILTER (WHERE struct_dist_m <= 15 AND struct_align_deg <= 20) AS fires,
       count(*) FILTER (WHERE struct_dist_m <= 15) AS within_15m,
       count(*) AS total
  FROM scratch_features;

SELECT struct_kind, struct_use, count(*)
  FROM scratch_features
 WHERE struct_dist_m <= 15 AND struct_align_deg <= 20
 GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '=== Darfield ==='
SELECT link_a, link_b, round(struct_dist_m::numeric,1) AS struct_m,
       round(struct_align_deg::numeric,1) AS align
  FROM scratch_features WHERE (link_a,link_b) IN ((232709,234053),(232708,234875));
