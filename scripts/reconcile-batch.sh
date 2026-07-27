#!/usr/bin/env bash
# Independently reconcile a batch run against the keys it SHOULD have produced.
#
#   reconcile-batch.sh <snapshot_id> [profile] [metric] [scope]
#
# The batch's own completeness flag counts DISTINCT link_id, so a two-way link
# with only one direction stored still reads as done. This checks the real unit
# of work: one row per eligible link per PERMITTED direction.
set -euo pipefail
export PGPASSWORD="${NZCL_DB_PASSWORD:-nzcl_local_dev}"

SNAP="$1"
PROFILE="${2:-car}"
METRIC="${3:-distance}"
SCOPE="${4:-physical}"

psql -h 127.0.0.1 -U nzcl -d nzcl -v ON_ERROR_STOP=1 \
     -v snap="$SNAP" -v profile="$PROFILE" -v metric="$METRIC" -v scope="$SCOPE" <<'SQL'
\pset border 2
\echo '== expected vs recorded (one key per eligible link per permitted direction) =='

WITH expected AS (
    SELECT l.link_id, d.direction
    FROM links l
    CROSS JOIN LATERAL (VALUES ('forward', l.forward_allowed),
                               ('reverse', l.reverse_allowed)) AS d(direction, allowed)
    WHERE l.snapshot_id = :'snap'
      AND l.in_analysis_area
      AND l.length_m > 0
      AND d.allowed
),
recorded AS (
    SELECT link_id, direction
    FROM detour_results
    WHERE snapshot_id = :'snap'
      AND vehicle_profile = :'profile'
      AND metric = :'metric'
      AND closure_scope = :'scope'
)
SELECT (SELECT count(*) FROM expected)                       AS expected_keys,
       (SELECT count(*) FROM recorded)                       AS recorded_keys,
       (SELECT count(*) FROM expected e
          WHERE NOT EXISTS (SELECT 1 FROM recorded r
                            WHERE r.link_id = e.link_id
                              AND r.direction = e.direction)) AS missing_keys,
       (SELECT count(*) FROM recorded r
          WHERE NOT EXISTS (SELECT 1 FROM expected e
                            WHERE e.link_id = r.link_id
                              AND e.direction = r.direction)) AS unexpected_keys;

\echo ''
\echo '== links with FEWER rows than permitted directions =='
WITH permitted AS (
    SELECT link_id,
           (forward_allowed::int + reverse_allowed::int) AS n_expected
    FROM links
    WHERE snapshot_id = :'snap' AND in_analysis_area AND length_m > 0
),
got AS (
    SELECT link_id, count(*) AS n_got
    FROM detour_results
    WHERE snapshot_id = :'snap' AND vehicle_profile = :'profile'
      AND metric = :'metric' AND closure_scope = :'scope'
    GROUP BY link_id
)
SELECT count(*) AS links_short_of_expected_directions
FROM permitted p LEFT JOIN got g USING (link_id)
WHERE coalesce(g.n_got, 0) < p.n_expected;

\echo ''
\echo '== duplicate keys =='
SELECT count(*) AS duplicate_keys FROM (
  SELECT link_id, direction
  FROM detour_results
  WHERE snapshot_id = :'snap' AND vehicle_profile = :'profile'
    AND metric = :'metric' AND closure_scope = :'scope'
  GROUP BY link_id, direction HAVING count(*) > 1
) q;

\echo ''
\echo '== status distribution =='
SELECT status, count(*) AS rows,
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
FROM detour_results
WHERE snapshot_id = :'snap' AND vehicle_profile = :'profile'
  AND metric = :'metric' AND closure_scope = :'scope'
GROUP BY status ORDER BY rows DESC;

\echo ''
\echo '== rows whose derived fields disagree with their status =='
SELECT
  count(*) FILTER (WHERE status = 'OK' AND alternative_distance_m IS NULL)
    AS ok_without_distance,
  count(*) FILTER (WHERE status = 'DISCONNECTED' AND alternative_distance_m IS NOT NULL)
    AS disconnected_with_distance,
  count(*) FILTER (WHERE status = 'OK' AND (route_arc_ids IS NULL OR cardinality(route_arc_ids) = 0))
    AS ok_without_route,
  count(*) FILTER (WHERE status = 'DISCONNECTED' AND isolation_side IS NULL)
    AS disconnected_without_isolation
FROM detour_results
WHERE snapshot_id = :'snap' AND vehicle_profile = :'profile'
  AND metric = :'metric' AND closure_scope = :'scope';
SQL
