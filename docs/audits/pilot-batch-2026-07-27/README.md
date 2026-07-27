# Pilot batch — Wellington, 2026-07-27

Snapshot `amds-wellington-2026-07-27-6ef785ad` (post grid-boundary fix:
36,397 links, 69,944 arcs, 33,015 nodes, 268 components).

Run: `car` / `distance` / source-feature closure scope,
`pgr-dijkstra-arc v2.0.0`.

## Independent reconciliation

The batch prints its own completeness flag, and that flag is **not** trustworthy
in general: it counts `DISTINCT link_id`, so a two-way link with only one
direction stored still reads as done, and a link whose computation raised
writes no row at all while still being counted as attempted. That defect is
real and is scheduled for replacement by a `batch_runs` ledger.

This run was therefore checked against the keys it *should* have produced —
one per eligible link per **permitted direction** — rather than against its own
flag. See `reconciliation.txt`.

| check | result |
| --- | --- |
| expected keys | 37,059 |
| recorded keys | 37,059 |
| missing keys | 0 |
| unexpected keys | 0 |
| links short of their permitted directions | 0 |
| duplicate keys | 0 |
| `OK` rows without a distance | 0 |
| `DISCONNECTED` rows carrying a distance | 0 |
| `OK` rows without a route | 0 |
| `DISCONNECTED` rows without an isolation profile | 0 |

The run reconciles. It was a single uninterrupted pass with no exceptions,
which is why the unreliable flag happened to agree with reality — that is luck,
not evidence, and the accounting still needs fixing before a resumed or
interrupted run can be trusted.

## Results

| status | rows | share |
| --- | --- | --- |
| OK | 20,155 | 54.39% |
| DISCONNECTED | 16,845 | 45.45% |
| SOURCE_DATA_ERROR | 59 | 0.16% |

`SOURCE_DATA_ERROR` is self-loop links (source node == target node), which
cannot carry an arc. They are reported rather than silently dropped.

## Measured performance

| measure | value |
| --- | --- |
| elapsed | 3,522 s (58.7 min) |
| throughput | 5.6 links/s |
| mean | 179.4 ms |
| p95 | 275.8 ms |
| max | 444.8 ms |

Note on an earlier claim: mid-run I said the full batch was averaging closer to
300 ms and that the 190 ms sampled figure was optimistic. **That correction was
wrong.** It was inferred from wall-clock progress while the API and other
queries were competing for the same database. The measured per-link mean over
all 19,572 links is 179.4 ms — slightly *better* than the sample.

## Status of this dataset

Reconciled and internally consistent for the parameters above. It is **not** a
national dataset, and it predates the outstanding semantic corrections
(closure-scope redefinition, exact turn-restriction handling, corridor
geometry). Treat it as a validated pilot baseline, not a governed national
result.
