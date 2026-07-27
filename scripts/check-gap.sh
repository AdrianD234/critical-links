#!/usr/bin/env bash
# How far apart are two links, and do their endpoints touch?
#
#   check-gap.sh <snapshot> <amds_id_a> <amds_id_b>
set -euo pipefail
export PGPASSWORD="${NZCL_DB_PASSWORD:-nzcl_local_dev}"

psql -h 127.0.0.1 -U nzcl -d nzcl -v ON_ERROR_STOP=1 \
     -v snap="$1" -v a="$2" -v b="$3" <<'SQL'
\pset border 2
SELECT
  round(ST_Distance(a.geom_2193, b.geom_2193)::numeric, 4)  AS gap_m,
  round(ST_Distance(ST_StartPoint(a.geom_2193), b.geom_2193)::numeric, 4) AS a_start_to_b,
  round(ST_Distance(ST_EndPoint(a.geom_2193),   b.geom_2193)::numeric, 4) AS a_end_to_b,
  round(ST_Distance(ST_StartPoint(b.geom_2193), a.geom_2193)::numeric, 4) AS b_start_to_a,
  round(ST_Distance(ST_EndPoint(b.geom_2193),   a.geom_2193)::numeric, 4) AS b_end_to_a,
  a.source_node AS a_src, a.target_node AS a_tgt,
  b.source_node AS b_src, b.target_node AS b_tgt
FROM links a, links b
WHERE a.snapshot_id = :'snap' AND a.amds_id = :'a'
  AND b.snapshot_id = :'snap' AND b.amds_id = :'b';
SQL
