# Validation plan and results

Every claim below is tied to a test, a measurement or a documented limitation.

---

## 1. Deterministic known-answer tests

`npm test` — 67 unit tests, all passing. Expected values are derivable on paper
from the fixture geometry, so the engine is checked against arithmetic rather
than against a previous run of itself.

### Routing and detour (`tests/unit/routing.test.ts`, 31 tests)

| # | Case | Expected | Status |
| --- | --- | --- | --- |
| 1 | Isolated edge, no detour | `DISCONNECTED`, null metrics | pass |
| 2 | Square loop, side 100 m | alternative 300 m, ratio 3.0, penalty 200 m | pass |
| 3 | Triangle 300/400/500 | close hypotenuse → 700 m, ratio 1.4 | pass |
| 4 | Directed one-way loop | forward `DISCONNECTED`, reverse 300 m | pass |
| 5 | Two-way link | exactly two opposed arcs | pass |
| 6 | Parallel carriageways 8 m apart | closing one leaves the other usable; detour via local road (600 m), never up the opposing carriageway | pass |
| 7 | Grade-separated crossing | 4 nodes not 5, 2 components, no route | pass |
| 8 | Prohibited turn | car forced onto 341.42 m path; emergency profile takes the 200 m banned turn | pass |
| 9 | Access-restricted road | car 300 m, emergency 100 m | pass |
| 10 | Heavy-vehicle restriction | car 100 m, heavy 300 m | pass |
| 11 | Cul-de-sac | terminal link `DISCONNECTED` | pass |
| 12 | Disconnected island | 2 components, `DISCONNECTED` citing components | pass |
| 13 | State budget exhausted | `UNRESOLVED_TIMEOUT`, explicitly **not** `DISCONNECTED` | pass |
| 14 | Cache invalidation | key changes on snapshot, algorithm version, scope, profile, metric, direction; stable under direction ordering | pass |
| 15 | Physical vs directed scope | physical removes 4 arcs and isolates; directed removes 1 and a 500 m route survives | pass |
| — | Network penalty ≠ added distance | link that is itself a detour: added −900 m, penalty 0 | pass |
| — | Time metric prefers longer, faster route | distance 1,000 m vs time route 1,600 m | pass |
| — | Clipped-extract honesty | `DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT` flag set | pass |

### Topology (`tests/unit/topology.test.ts`, 15 tests)

T-junction splitting, exact length preservation, closure-group cohesion,
grade-separated crossings left unnoded, an interchange where a ramp cuts the
motorway but an overbridge does not, near misses reported rather than joined,
multiple junctions on one road, and junctions landing on existing vertices.

### Corridor (`tests/unit/corridor.test.ts`, 7 tests)

One-way carriageway with an internal downstream node: endpoint measure
undefined, corridor resolves with penalty 600 m over hand-computed geometry;
genuine dead end still flagged `SOLE_ACCESS`; corridor skipped when the endpoint
measure succeeds.

### Projection (`tests/unit/geo.test.ts`, 7 tests)

Validated against **Esri-reprojected ground truth**: the same AMDS features
fetched twice, once `outSR=2193` and once `outSR=4326`. Our implementation
cannot pass by agreeing with itself.

Worst case over 24 points in 4 regions: inverse **0.11 mm**, forward **0.19 mm**,
round-trip **0.30 mm**.

### Map style (`tests/unit/map-style.test.ts`, 7 tests)

Validated against the official MapLibre style specification. This substitutes
for visual verification, which the hidden dev browser pane cannot provide
(`KNOWN_LIMITATIONS.md` §10).

---

## 2. Integration tests against real data

`tests/integration/api.test.ts` — 16 tests against the live Wellington snapshot,
skipped automatically if no snapshot is present.

- Snapshot completeness: downloaded count equals service count
- Provenance present: source URL, SHA-256, attribution, retrieval timestamp
- Every graph link has a unique identifier
- No zero-length links in the routable graph
- One-way links yield one arc, two-way yield two
- **Largest component > 80% of links** (guards the junction-splitting rule)
- Closure groups survive splitting
- Physical scope removes the whole group; directed removes one arc
- `OK` always has a positive distance and a route; `DISCONNECTED` always has
  neither
- **Route arc lengths sum to the reported distance** (±1 mm)
- **No route ever traverses an arc belonging to its own closure**
- Heavy profile never routes over a heavy-prohibited link
- `DISCONNECTED` always carries an isolation profile
- Cache hit/miss behaviour

---

## 3. Source-data QA

`npm run qa -- <snapshotId>`. Wellington pilot:

| measure | value |
| --- | --- |
| Graph links | 36,395 (from 29,053 source links) |
| Arcs / nodes | 69,942 / 33,025 |
| Connected components | 273 |
| Largest component | 87.31% |
| Network length | 8,473.3 km |
| Named links | 42.89% |
| One-way links | 1,268 |
| Turn restrictions applied | 13 |

Issues raised: `SELF_LOOP` (84), `UNCONNECTED_NEAR_MISS` (15,304),
`TURN_RESTRICTION_COVERAGE` (13), `SPEED_PROVENANCE` (36,395), plus an
informational `COMPONENT_STRUCTURE`. No errors.

---

## 4. Topology investigation

Two purpose-built probes, retained because the finding they produced is the most
consequential in the project.

`pipelines/validation/topology-probe.ts` — component distribution and a
tolerance sweep. The sweep showed connectivity **does not improve** with
tolerance (largest component stayed ~21% from 1 mm to 2 m), ruling out snapping
as the cause.

`pipelines/validation/midlink-probe.ts` — measured that **7,119 of 16,463**
dangling endpoints sit within 10 mm of another link's *interior*, identifying
unsplit through roads as the actual cause.

---

## 5. Real-world spot checks

### Geographic sanity: connected components

Component centroids and dominant road names were inspected. Component 0 (31,778
links) is the North Island, dominated by `SH 1N` links. Component 3 (3,618
links) is Marlborough — `State Highway #1S`, `Redwood Street (Blenheim)` —
correctly separated by Cook Strait, which no road crosses. Components 6 and 9
are Marlborough Sounds peninsulas (`Kenepuru Road`, `Crail Bay Road`), genuinely
road-isolated.

**This is a strong signal: the model independently rediscovered Cook Strait.**

### Criticality ranking

Highest network penalty among named links over 500 m, from the completed batch:

| Road | Added distance | Ratio | Location |
| --- | --- | --- | --- |
| Moonshine Road | +56.0 km | 81.3× | −41.107, 174.965 |
| Paekākāriki Hill Road | +35.3 km | 71.4× | −41.077, 174.929 |

Both are hill roads linking the Hutt Valley / Pauatahanui to the Kāpiti Coast.
Closing either forces traffic the long way round via SH1 or SH2/SH58. These are
exactly the links a Wellington resilience practitioner would nominate, and they
were surfaced by the model with no local knowledge encoded.

### Individual corridors

| Link | Result |
| --- | --- |
| Hutt Road South (1,264 m) | `OK`, alternative 1,450 m, ratio 1.15, penalty 185 m — dense urban grid, short detour |
| The Terrace (341 m) | `OK`, alternative 1,129 m, ratio 3.31 — CBD one-way system forces a loop |
| Adelaide Road (374 m) | `OK`, alternative 1,327 m, ratio 3.55 |
| Cobham Drive (818 m, one-way) | endpoint `DISCONNECTED`; corridor `OK` with penalty 449 m |

### Stratification achieved

Motorway, divided carriageway, urban one-way pairs, rural state highway, hill
roads, local streets, cul-de-sacs, and both main islands (via the Marlborough
portion of the extract) are all represented in the pilot.

---

## 6. Performance

Wellington pilot, single-threaded:

| measure | value |
| --- | --- |
| Full batch | 19,573 / 19,573 links, **complete** |
| Elapsed | 316.7 s (5.3 min), 61.8 links/s |
| p50 / p95 / p99 / max | 0 / 58 / 61 / 82 ms |
| **Timeouts** | **0** |
| Status counts | OK 20,121 · DISCONNECTED 16,881 · SOURCE_DATA_ERROR 59 |

The aspirational target was p95 < 2 s interactive. Achieved with ~34× margin.

---

## 7. Outstanding validation work

Not done, and not claimed:

1. **Visual map verification.** Blocked by the hidden browser pane. Needs one
   run in a visible browser.
2. **Comparison against the AMDS Experience Builder app.** Manual, per-link.
3. **Independent OSM cross-check** of a stratified sample. Would catch missing
   roads that AMDS itself omits — something no internal check can detect.
4. **Field or imagery verification** of a sample of the 15,304 near misses, to
   establish whether they are genuine network gaps or a tolerance artefact.
5. **NSLR integration** before any travel-time figure is used in a decision.
6. **Reviewer sign-off records.** The structure exists (per-case pass/fail with
   notes) but no human review has been recorded.

---

# Part 2 — Python + PostGIS + pgRouting

After the stack was ported (ADR-004), the validation was rebuilt against the new
implementation and extended with cross-engine comparison.

## 8. Python test suite — 48 tests, all passing

```bash
cd python && ~/.venvs/nzcl/bin/python -m pytest tests/ -q
```

`tests/test_routing.py` runs against a **real PostGIS database with real
pgr_dijkstra calls**, not a stand-in. Synthetic fixtures are loaded through the
same junction-splitting and node-assignment code that production data uses, so a
test cannot pass against a graph assembled by a different route than users get.

| Case | Expected | Status |
| --- | --- | --- |
| Isolated edge | `DISCONNECTED`, null metrics | pass |
| Square loop, side 100 m | alternative 300 m, ratio 3.0, penalty 200 m | pass |
| Triangle 300/400/500 | close hypotenuse → 700 m, ratio 1.4 | pass |
| Directed one-way loop | forward `DISCONNECTED`, reverse 300 m | pass |
| One-way / two-way arc counts | 1 and 2 respectively | pass |
| Grade-separated crossing | 4 nodes, no route | pass |
| Prohibited turn | car forced onto 341.42 m path via the expanded graph; emergency takes the 200 m banned turn | pass |
| Mode restriction | car 300 m vs emergency 100 m; car 100 m vs heavy 300 m | pass |
| Cul-de-sac / island | `DISCONNECTED` with isolation profile | pass |
| **Timeout** | `UNRESOLVED_TIMEOUT`, explicitly not `DISCONNECTED` | pass |
| Closure scope | physical removes 2 arcs and isolates; directed removes 1, 500 m route survives | pass |
| Network penalty ≠ added distance | added −900 m, penalty 0 | pass |
| Time metric | prefers 1,600 m fast route over 1,000 m slow one | pass |
| Corridor on a one-way pair | endpoint undefined, corridor penalty exactly 600 m | pass |
| Route integrity | never uses a closure arc; arc lengths sum to reported distance | pass |

`tests/test_topology.py` — 20 tests covering T-junction splitting, exact length
preservation, closure-group cohesion, grade separation, interchanges, near
misses, and the grid-boundary regression below.

## 9. Cross-engine validation — the strongest check available

Two independent implementations of the same specification: different languages,
graph representations and shortest-path libraries, over the same source data.

```bash
python -m nzcl.crossvalidate --ts-url http://<windows-host>:8787
```

### It found a bug neither test suite caught

First run: **99.74% agreement** on 378 direction-results, with one distance
disagreement of **3,611 m** on Raiha Street.

Diagnosis, step by step:

1. `nzcl.diagnose` confirmed all 91 links of the TypeScript route existed in the
   pgRouting graph, but two consecutive ones were **not adjacent** there — so the
   engines had different graphs, not different answers.
2. `scripts/check-gap.sh` measured the geometry directly: the two links met
   end-to-end **0.4 mm apart**, well inside the 10 mm node tolerance, yet had
   been assigned different nodes.
3. Cause: both engines quantised coordinates to a single grid cell
   (`round(x / tolerance)`). Two points either side of a cell boundary get
   different keys however close they are.

Consequence: a severed junction, and a detour routed 3.6 km the wrong way.

Fix: probe the 3×3 cell neighbourhood and merge with the nearest node inside the
tolerance. Applied to **both** engines, with regression tests in both suites
using a pair deliberately placed astride a cell edge.

### After the fix

Both engines independently produce **33,015 nodes** for the Wellington pilot —
they had differed before.

```
250 links sampled, 478 direction-results compared
  links not comparable (different split): 0
  status agreements:      478      status disagreements:   0
  distance agreements:    478      distance disagreements: 0
  agreement: 100.0%
  median delta 0.026 m    max delta 0.118 m
```

The residual 12 cm is floating-point and geometry-rounding difference between
the two stacks.

**This is the single most valuable validation artefact in the project.** Neither
engine's own tests found the bug, because both suites were written against the
same mental model. Only an independent implementation exposed it.

## 10. API smoke test

`python scripts/smoke-api.py` exercises the endpoints the web client depends on
and prints enough of each response to judge whether the numbers are sane, not
merely present: health, metadata, search, detour with route geometry, both
closure scopes, all three vehicle profiles, and a 404 on an unknown link.

Notable: the `heavy` profile returns `DISCONNECTED` on a link where `car`
returns `OK`, because the car detour uses a link heavy vehicles may not — the
profile filter doing real work on real data.

## 11. Measured performance (Python + pgRouting)

| measure | value |
| --- | --- |
| Ingest, Wellington pilot | 28 s (download, split, node, load) |
| Single shortest path | 39.9 ms |
| Full closure analysis, mean | 190 ms |
| p50 / p95 / max | ~150 / 265 / 326 ms |
| Interactive target | p95 < 2 s — met with ~7.5× margin |

The 39.9 ms floor is `pgr_dijkstra` reloading all 69,944 arcs per call. See
ADR-004 for the comparison against the in-memory engine and what was optimised.

## 12. Still outstanding

Unchanged from Part 1, and still not claimed:

1. **Visual map verification** — blocked by the hidden browser pane.
2. **National batch** — not run; measured throughput implies a long run.
3. **Independent OSM cross-check** of a stratified sample.
4. **Field review** of the near-miss endpoints.
5. **NSLR integration** before any travel-time figure informs a decision.
6. **Reviewer sign-off records** — the structure exists, no human review recorded.
