\echo '=== vertex coincidence: is the crossing a digitised vertex on both lines? ==='
SELECT CASE WHEN vdist_a <= 0.001 AND vdist_b <= 0.001 THEN 'both have a vertex there'
            WHEN vdist_a <= 0.001 OR  vdist_b <= 0.001 THEN 'one has a vertex there'
            ELSE 'neither: crossing falls mid-segment on both' END AS vertex_case,
       count(*), round(100.0*count(*)/sum(count(*)) OVER (), 1) AS pct
  FROM scratch_features GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=== ...and the same, split by whether there is motorway context nearby ==='
SELECT (motorway_links_300m > 0 OR ramp_links_300m > 0) AS motorway_or_ramp_within_300m,
       CASE WHEN vdist_a <= 0.001 AND vdist_b <= 0.001 THEN 'vertex on both'
            WHEN vdist_a <= 0.001 OR  vdist_b <= 0.001 THEN 'vertex on one'
            ELSE 'vertex on neither' END AS vertex_case,
       count(*)
  FROM scratch_features GROUP BY 1,2 ORDER BY 1,3 DESC;

\echo ''
\echo '=== crossing angle ==='
SELECT width_bucket(angle_deg, 0, 90, 9) AS bucket,
       (width_bucket(angle_deg, 0, 90, 9)-1)*10 || '-' ||
       width_bucket(angle_deg, 0, 90, 9)*10 || ' deg' AS band,
       count(*)
  FROM scratch_features GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== state-highway involvement (PRIORITISATION ONLY, never a classifier) ==='
SELECT CASE WHEN rca_a = 1 AND rca_b = 1 THEN 'both state highway'
            WHEN rca_a = 1 OR  rca_b = 1 THEN 'one state highway'
            ELSE 'neither state highway' END AS sh,
       count(*), round(100.0*count(*)/sum(count(*)) OVER (), 1) AS pct
  FROM scratch_features GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=== ramps, connectors, one-way carriageways ==='
SELECT count(*) FILTER (WHERE ramp_a OR ramp_b)                     AS involves_a_ramp,
       count(*) FILTER (WHERE mat_a = 6 OR mat_b = 6)               AS involves_a_connector,
       count(*) FILTER (WHERE oneway_a = 1 OR oneway_b = 1)         AS involves_a_oneway,
       count(*) FILTER (WHERE oneway_a = 1 AND oneway_b = 1)        AS both_oneway,
       count(*) FILTER (WHERE motorway_links_300m > 0)              AS motorway_within_300m,
       count(*) FILTER (WHERE ramp_links_300m > 0)                  AS ramp_within_300m,
       count(*) FILTER (WHERE name_a ILIKE '%interchange%' OR name_b ILIKE '%interchange%')
                                                                    AS named_interchange,
       count(*) FILTER (WHERE 'MODE_RESTRICTED' = ANY(flags_a) OR 'MODE_RESTRICTED' = ANY(flags_b))
                                                                    AS mode_restricted,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM unnest(flags_a) f WHERE f LIKE 'HEIGHT_LIMIT%')
                           OR EXISTS (SELECT 1 FROM unnest(flags_b) f WHERE f LIKE 'HEIGHT_LIMIT%'))
                                                                    AS height_limited
  FROM scratch_features;

\echo ''
\echo '=== urban / rural ==='
SELECT coalesce(ur_a,'?') || ' x ' || coalesce(ur_b,'?') AS ur, count(*)
  FROM scratch_features GROUP BY 1 ORDER BY 2 DESC LIMIT 10;

\echo ''
\echo '=== named vs unnamed ==='
SELECT (name_a IS NOT NULL) AS named_a, (name_b IS NOT NULL) AS named_b, count(*)
  FROM scratch_features GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '=== an existing NODE sits within 1 m of the crossing (noder half-saw it) ==='
SELECT nodes_within_1m, count(*) FROM scratch_features GROUP BY 1 ORDER BY 1 LIMIT 8;

\echo ''
\echo '=== the two Greendale crossings, in full ==='
SELECT link_a, link_b, name_a, name_b, round(vdist_a::numeric,4) vdist_a,
       round(vdist_b::numeric,4) vdist_b, round(angle_deg::numeric,1) angle,
       rca_a, rca_b, oneway_a, oneway_b, mat_a, mat_b, ur_a, ur_b,
       motorway_links_300m, ramp_links_300m, nodes_within_1m, near_misses_25m
  FROM scratch_features WHERE (link_a, link_b) IN ((232709, 234053), (232708, 234875));
