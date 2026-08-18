-- Build one feature row per crossing PAIR, carrying every attribute a
-- classifier could use. No classification happens here; this is the evidence.
\timing on
\set snap 'amds-national-2026-07-28-5b359d84'

DROP TABLE IF EXISTS scratch_features;

CREATE TABLE scratch_features AS
WITH pt AS (
  SELECT c.link_a, c.link_b, c.group_a, c.group_b, c.itype,
         ST_PointOnSurface(c.igeom) AS p,
         ST_NumGeometries(ST_Multi(c.igeom)) AS n_intersections
    FROM scratch_crossings c
   WHERE ST_Dimension(c.igeom) = 0
)
SELECT
  pt.link_a, pt.link_b, pt.group_a, pt.group_b, pt.n_intersections,
  pt.p AS pt_2193,
  ST_X(pt.p) AS px, ST_Y(pt.p) AS py,

  -- distance from the crossing to the nearest DIGITISED VERTEX of each line.
  -- Zero on both means a cartographer put a vertex exactly there.
  (SELECT min(ST_Distance(pt.p, d.geom)) FROM ST_DumpPoints(a.geom_2193) d) AS vdist_a,
  (SELECT min(ST_Distance(pt.p, d.geom)) FROM ST_DumpPoints(b.geom_2193) d) AS vdist_b,

  a.rca_code AS rca_a, b.rca_code AS rca_b,
  a.oneway AS oneway_a, b.oneway AS oneway_b,
  a.model_asset_type AS mat_a, b.model_asset_type AS mat_b,
  a.surface_type AS surf_a, b.surface_type AS surf_b,
  a.urban_rural AS ur_a, b.urban_rural AS ur_b,
  a.road_number AS num_a, b.road_number AS num_b,
  a.length_m AS len_a, b.length_m AS len_b,
  a.quality_flags AS flags_a, b.quality_flags AS flags_b,
  a.speed_kph AS speed_a, b.speed_kph AS speed_b,

  na.display_name AS name_a, nb.display_name AS name_b,
  na.is_ramp AS ramp_a, nb.is_ramp AS ramp_b,
  na.route_designation AS desig_a, nb.route_designation AS desig_b,

  -- how far along each line the crossing sits, as a fraction
  ST_LineLocatePoint(a.geom_2193, pt.p) AS frac_a,
  ST_LineLocatePoint(b.geom_2193, pt.p) AS frac_b,

  -- angle between the two lines at the crossing, degrees 0..90.
  -- A near-tangential crossing is usually digitising noise between parallel
  -- carriageways, not a junction.
  degrees(abs(ST_Azimuth(ST_LineInterpolatePoint(a.geom_2193,
              greatest(ST_LineLocatePoint(a.geom_2193, pt.p) - 0.02, 0.0)),
            ST_LineInterpolatePoint(a.geom_2193,
              least(ST_LineLocatePoint(a.geom_2193, pt.p) + 0.02, 1.0)))
       - ST_Azimuth(ST_LineInterpolatePoint(b.geom_2193,
              greatest(ST_LineLocatePoint(b.geom_2193, pt.p) - 0.02, 0.0)),
            ST_LineInterpolatePoint(b.geom_2193,
              least(ST_LineLocatePoint(b.geom_2193, pt.p) + 0.02, 1.0))))) AS raw_angle,

  -- is there any OTHER link ending at this exact spot? A crossing that
  -- coincides with a real node is a junction the noder already half-saw.
  (SELECT count(*) FROM nodes n
    WHERE n.snapshot_id = :'snap' AND ST_DWithin(n.geom_2193, pt.p, 1.0)) AS nodes_within_1m,
  (SELECT count(*) FROM nodes n
    WHERE n.snapshot_id = :'snap' AND ST_DWithin(n.geom_2193, pt.p, 25.0)) AS nodes_within_25m,

  -- near misses recorded by the ingest at this spot
  (SELECT count(*) FROM near_misses nm
    WHERE nm.snapshot_id = :'snap' AND ST_DWithin(nm.geom_2193, pt.p, 25.0)) AS near_misses_25m,

  -- local motorway context: any one-way state-highway link within 300 m
  (SELECT count(*) FROM links m
    WHERE m.snapshot_id = :'snap' AND m.rca_code = 1 AND m.oneway = 1
      AND ST_DWithin(m.geom_2193, pt.p, 300.0)) AS motorway_links_300m,
  -- local ramp context
  (SELECT count(*) FROM links m
     JOIN link_names ln ON ln.snapshot_id = m.snapshot_id
                       AND ln.closure_group_id = m.closure_group_id
    WHERE m.snapshot_id = :'snap' AND ln.is_ramp
      AND ST_DWithin(m.geom_2193, pt.p, 300.0)) AS ramp_links_300m

  FROM pt
  JOIN links a ON a.snapshot_id = :'snap' AND a.link_id = pt.link_a
  JOIN links b ON b.snapshot_id = :'snap' AND b.link_id = pt.link_b
  LEFT JOIN link_display_names na ON na.snapshot_id = :'snap' AND na.link_id = pt.link_a
  LEFT JOIN link_display_names nb ON nb.snapshot_id = :'snap' AND nb.link_id = pt.link_b;

ALTER TABLE scratch_features
  ADD COLUMN angle_deg double precision
  GENERATED ALWAYS AS (least(raw_angle, 180 - raw_angle)) STORED;

CREATE INDEX scratch_features_pt ON scratch_features USING gist (pt_2193);
CREATE INDEX scratch_features_ab ON scratch_features (link_a, link_b);
ANALYZE scratch_features;

SELECT count(*) AS feature_rows FROM scratch_features;
