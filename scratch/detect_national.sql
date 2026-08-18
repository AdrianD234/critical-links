-- National interior-interior crossing detection.
-- Writes scratch_crossings: one row per PAIR of vehicle-eligible graph links whose
-- geometries intersect but which share no graph node.
\timing on
\set snap 'amds-national-2026-07-28-5b359d84'

DROP TABLE IF EXISTS scratch_crossings;

CREATE TABLE scratch_crossings AS
SELECT a.link_id            AS link_a,
       b.link_id            AS link_b,
       a.closure_group_id   AS group_a,
       b.closure_group_id   AS group_b,
       a.amds_id            AS amds_a,
       b.amds_id            AS amds_b,
       a.model_asset_type   AS mat_a,
       b.model_asset_type   AS mat_b,
       a.road_number        AS num_a,
       b.road_number        AS num_b,
       a.oneway             AS oneway_a,
       b.oneway             AS oneway_b,
       ST_GeometryType(ST_Intersection(a.geom_2193, b.geom_2193)) AS itype,
       ST_NumGeometries(ST_Multi(ST_Intersection(a.geom_2193, b.geom_2193))) AS ipart_count,
       ST_Intersection(a.geom_2193, b.geom_2193) AS igeom
  FROM links a
  JOIN links b
    ON b.snapshot_id = a.snapshot_id
   AND b.link_id > a.link_id
   AND b.mode_vehicle
   AND ST_Intersects(a.geom_2193, b.geom_2193)
 WHERE a.snapshot_id = :'snap'
   AND a.mode_vehicle
   AND NOT (ARRAY[a.source_node, a.target_node] && ARRAY[b.source_node, b.target_node]);

CREATE INDEX scratch_crossings_a ON scratch_crossings (link_a);
CREATE INDEX scratch_crossings_b ON scratch_crossings (link_b);
CREATE INDEX scratch_crossings_geom ON scratch_crossings USING gist (igeom);
ANALYZE scratch_crossings;

SELECT count(*) AS pair_rows FROM scratch_crossings;
SELECT itype, count(*) FROM scratch_crossings GROUP BY 1 ORDER BY 2 DESC;
