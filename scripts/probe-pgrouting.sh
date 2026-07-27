#!/usr/bin/env bash
# What routing functions does the installed pgRouting actually provide?
set -euo pipefail
export PGPASSWORD="${NZCL_DB_PASSWORD:-nzcl_local_dev}"
psql -h 127.0.0.1 -U nzcl -d nzcl -tA <<'SQL'
SELECT p.proname || '(' || pg_get_function_arguments(p.oid) || ')'
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN ('pgr_trsp','pgr_trsp_withpoints','pgr_dijkstra','pgr_astar',
                    'pgr_bdastar','pgr_bddijkstra')
ORDER BY p.proname, 1;
SQL
