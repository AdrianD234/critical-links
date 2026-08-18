SELECT (SELECT count(*) FROM scratch_crossings) AS pairs_all,
       (SELECT count(*) FROM scratch_crossings WHERE ST_Dimension(igeom)=0) AS pairs_pointlike,
       (SELECT count(*) FROM scratch_crossings WHERE ST_Dimension(igeom)>0) AS pairs_overlap,
       (SELECT count(*) FROM scratch_xpoints) AS intersection_points,
       (SELECT count(*) FROM scratch_crossings WHERE group_a=group_b) AS self_crossing_pairs;

SELECT itype, count(*) FROM scratch_crossings WHERE ST_Dimension(igeom)>0 GROUP BY 1 ORDER BY 2 DESC;
