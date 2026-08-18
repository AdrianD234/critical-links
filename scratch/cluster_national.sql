-- Explode crossing pairs to individual intersection POINTS, then cluster them
-- spatially into unique physical crossings. Reports the pair count, the point
-- count and the cluster count at several radii, because they are three
-- different numbers and only the last one is "how many places".
\timing on
\set snap 'amds-national-2026-07-28-5b359d84'

DROP TABLE IF EXISTS scratch_xpoints;

CREATE TABLE scratch_xpoints AS
SELECT c.link_a, c.link_b, c.group_a, c.group_b, c.itype,
       (d.dump).geom AS pt
  FROM scratch_crossings c
  CROSS JOIN LATERAL (SELECT ST_DumpPoints(c.igeom) AS dump) d
 WHERE ST_Dimension(c.igeom) = 0;   -- points only; collinear overlaps excluded

CREATE INDEX scratch_xpoints_geom ON scratch_xpoints USING gist (pt);
ANALYZE scratch_xpoints;

\echo '--- pairs whose intersection is NOT zero-dimensional (collinear overlap: duplicate geometry, not a crossing) ---'
SELECT itype, count(*) FROM scratch_crossings WHERE ST_Dimension(igeom) > 0 GROUP BY 1 ORDER BY 2 DESC;

\echo '--- pairs, points ---'
SELECT (SELECT count(*) FROM scratch_crossings)                          AS pairs_all,
       (SELECT count(*) FROM scratch_crossings WHERE ST_Dimension(igeom)=0) AS pairs_pointlike,
       (SELECT count(*) FROM scratch_xpoints)                            AS intersection_points;

\echo '--- clusters at several radii ---'
DROP TABLE IF EXISTS scratch_clusters;
CREATE TABLE scratch_clusters AS
SELECT link_a, link_b, group_a, group_b, pt,
       ST_ClusterDBSCAN(pt, eps := 10,  minpoints := 1) OVER () AS cl10,
       ST_ClusterDBSCAN(pt, eps := 25,  minpoints := 1) OVER () AS cl25,
       ST_ClusterDBSCAN(pt, eps := 50,  minpoints := 1) OVER () AS cl50,
       ST_ClusterDBSCAN(pt, eps := 100, minpoints := 1) OVER () AS cl100
  FROM scratch_xpoints;

CREATE INDEX scratch_clusters_geom ON scratch_clusters USING gist (pt);
CREATE INDEX scratch_clusters_cl25 ON scratch_clusters (cl25);
ANALYZE scratch_clusters;

SELECT count(DISTINCT cl10)  AS clusters_10m,
       count(DISTINCT cl25)  AS clusters_25m,
       count(DISTINCT cl50)  AS clusters_50m,
       count(DISTINCT cl100) AS clusters_100m
  FROM scratch_clusters;

\echo '--- how many pairs / points per 25 m cluster ---'
SELECT n_points, count(*) AS clusters FROM (
  SELECT cl25, count(*) AS n_points FROM scratch_clusters GROUP BY cl25
) t GROUP BY 1 ORDER BY 1 LIMIT 20;

\echo '--- distinct SOURCE FEATURES per 25 m cluster (2 = one road crossing one road) ---'
SELECT n_groups, count(*) AS clusters FROM (
  SELECT cl25, count(DISTINCT g) AS n_groups FROM (
    SELECT cl25, group_a AS g FROM scratch_clusters
    UNION ALL
    SELECT cl25, group_b AS g FROM scratch_clusters
  ) u GROUP BY cl25
) t GROUP BY 1 ORDER BY 1;
