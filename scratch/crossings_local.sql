\set snap 'amds-national-2026-07-28-5b359d84'
WITH focus AS (
  SELECT geom_2193 AS g FROM links
   WHERE snapshot_id = :'snap' AND link_id = 234872
), area AS (
  SELECT ST_Buffer(g, 5000) AS g FROM focus
), cand AS (
  SELECT l.link_id, l.amds_id, l.closure_group_id, l.geom_2193, l.source_node, l.target_node
    FROM links l, area a
   WHERE l.snapshot_id = :'snap' AND l.mode_vehicle AND ST_Intersects(l.geom_2193, a.g)
)
SELECT a.link_id AS la, b.link_id AS lb,
       na.display_name AS name_a, nb.display_name AS name_b,
       ST_AsText(ST_PointOnSurface(ST_Intersection(a.geom_2193,b.geom_2193))) AS ipt,
       ST_GeometryType(ST_Intersection(a.geom_2193,b.geom_2193)) AS itype,
       a.source_node, a.target_node, b.source_node, b.target_node,
       a.closure_group_id = b.closure_group_id AS same_group
  FROM cand a JOIN cand b ON a.link_id < b.link_id
  LEFT JOIN link_display_names na ON na.snapshot_id=:'snap' AND na.link_id=a.link_id
  LEFT JOIN link_display_names nb ON nb.snapshot_id=:'snap' AND nb.link_id=b.link_id
 WHERE ST_Intersects(a.geom_2193, b.geom_2193)
   AND NOT (ARRAY[a.source_node,a.target_node] && ARRAY[b.source_node,b.target_node])
 ORDER BY 1,2;
