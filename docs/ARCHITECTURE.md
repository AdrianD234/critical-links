# Architecture

```
ArcGIS FeatureServer (NZTA AMDS)
        │  discovery → ingestion (id-pinned, reconciled, hashed)
        ▼
   immutable snapshot on disk           data/processed/<snapshotId>/
   links.ndjson · geometry.bin · restrictions.json · meta.json
        │  loaded once into typed arrays
        ▼
   RoadGraph (CSR adjacency)  →  Router (arc-expanded A*)
        │                              │
        │                              ├─ DetourEngine   endpoint measure
        │                              ├─ Corridor       through-trip measure
        │                              └─ Isolation      what is stranded
        ▼
   Fastify API  ──── vector tiles (geojson-vt + vt-pbf)
        │       └─── JSON: search, detour, QA, OpenAPI
        ▼
   React + MapLibre GL JS          batch pipeline → CSV / XLSX
```

---

## ADR-001: Node + TypeScript instead of Python, PostGIS and pgRouting

**Status:** accepted
**Context:** the reference design specified Python 3.12, FastAPI, PostgreSQL,
PostGIS and pgRouting in Docker.

**Correction (2026-07-27).** This ADR originally claimed the machine had *no
Python and no Docker*. **The Python half of that was wrong.** `python --version`
in git bash returns the Windows Store alias stub, which shadows a real install;
taking that as conclusive was a mistake. What is actually present:

| Component | Status |
| --- | --- |
| Python | **Anaconda 3.13.5** at `C:\Conda\anaconda3`, conda 25.7.0 |
| WSL | **Ubuntu 24.04.2 LTS**, Python 3.12.3, 953 GB free |
| PostgreSQL 16 / PostGIS 3.4.2 / pgRouting 3.6.1 / GDAL 3.8.4 | Installable via `apt` in WSL |
| Docker / Podman | Genuinely absent |

So the reference stack **is** achievable here, via WSL rather than Docker. The
original decision rested on a false premise and is restated below on accurate
grounds.

**The actual constraint.** Docker is absent. Standing up PostgreSQL + PostGIS +
pgRouting therefore means a WSL install requiring interactive `sudo`, which
could not be performed unattended. That is a real but *surmountable* obstacle,
not an impossibility.

**Why the decision still stands on its merits.** Independently of availability,
for this specific problem:

- pgRouting's `pgr_dijkstra` is **node-based**. Turn restrictions are a property
  of the (arriving arc, departing arc) pair, so they need `pgr_trsp` or a
  hand-built edge expansion — the arc-expanded search here handles them
  natively.
- A closure query is *"remove this arc set and re-run"*. In-process that is
  17 ms with no query planning or IPC; the same loop through SQL pays a round
  trip per call, which matters at 375,485 links.
- The engine is provable by known-answer tests against hand-computed geometry,
  which is harder to do against a query plan.

**What this stack genuinely gives up**, and which PostGIS would provide: SQL for
ad-hoc spatial analysis, direct QGIS connectivity, and an institutional standard
familiar to GIS teams. See ADR-004.

**Decision.** Build on Node 24 + TypeScript, with the routing engine implemented
directly over typed arrays and snapshots held as immutable files.

**Consequences — what is lost:**

- No SQL. Ad-hoc spatial queries against the network need code, not psql.
- No PostGIS spatial indexing for arbitrary geometry operations.
- No pgRouting; the shortest-path implementation is ours to maintain and prove.

**Consequences — what is gained:**

- It runs. 67 unit tests and 16 integration tests execute against real data.
- Turn restrictions are expressible. pgRouting's `pgr_dijkstra` is node-based;
  restrictions need an edge-expanded graph, which would have meant either
  `pgr_trsp` (with its own constraints) or building the expansion by hand
  anyway.
- Speed. Measured p95 of 65 ms per closure over 36,395 links, including two
  shortest-path searches plus corridor and isolation analysis, with no network
  or IPC round trip per query.
- Transparency. "Exclude these arcs and re-run A*" is 200 lines of readable
  code with known-answer tests, rather than a query plan.

**Mitigations if the constraint lifts.** The snapshot format is a plain
file layout and the graph builder is pure; a PostGIS loader would be an
additional writer, not a rewrite. The metric definitions in
`METRIC_DEFINITIONS.md` are storage-independent.

---

## ADR-002: node at endpoint-on-interior, never at interior-on-interior

**Status:** accepted

AMDS publishes no from/to node ids and no z-level, and does not split a through
road where a side road ends on it. Endpoint-only noding produced 5,719
components with the largest holding 21% of links.

Splitting where one link's **endpoint** lies on another's **interior** gives 273
components, largest 87.3%. Refusing to split where two **interiors** cross is
what preserves every overbridge, tunnel and grade-separated interchange — with
no z-level attribute, that refusal is the only thing standing between the model
and a country full of invented junctions.

Full reasoning and measurements: `KNOWN_LIMITATIONS.md` §2.

---

## ADR-003: report three measures, not one

**Status:** accepted

The specified metric (shortest path between the closed link's own endpoints) is
undefined on one-way carriageways — 82% of pilot state-highway links returned
DISCONNECTED for reasons unrelated to criticality.

Rather than redefine the headline metric, the engine reports it exactly as
specified and adds two companions: a **corridor** through-trip comparison and an
**isolation** profile of what is stranded. The UI, API and exports label all
three distinctly. See `METRIC_DEFINITIONS.md`.

---

## Components

### `packages/core`

Pure, dependency-free domain logic.

| module | responsibility |
| --- | --- |
| `types.ts` | Domain types, AMDS coded-value domains, status enum |
| `geo.ts` | NZTM2000 ↔ WGS84 (Redfearn), polyline length |
| `graph.ts` | `RoadGraph`: typed arrays, CSR in/out adjacency, components, closure groups |
| `topology.ts` | Junction splitting (ADR-002) |
| `routing.ts` | Arc-expanded A* with exclusions, mode masks, turn restrictions |
| `detour.ts` | Endpoint metrics, orchestration per direction |
| `corridor.ts` | Through-trip replacement path |
| `isolation.ts` | Bounded stranded-pocket measurement |
| `speed.ts` | Speed assignment, explicitly labelled as estimated |
| `snapshot.ts` | Immutable snapshot read/write |
| `cache.ts` | Cache key including snapshot + algorithm version; bounded LRU |

**Why typed arrays.** The national vehicle network is ~272k links / ~536k arcs.
As typed arrays that is tens of megabytes and the A* inner loop stays cache
friendly. Object graphs at that scale cost an order of magnitude more memory and
GC pressure.

**Why CSR.** Contiguous neighbour lists mean the hot loop walks memory linearly.
Reverse adjacency (`inStart`/`inArcs`) exists specifically so the corridor walk
can go upstream.

**Why stamp arrays.** `dist`, `pred`, `closed` and `excl` are allocated once and
invalidated by an incrementing epoch, so a query never pays an O(arcCount) clear.
That matters when running hundreds of thousands of closures.

### `apps/api`

Fastify. Loads one snapshot at start-up and holds it in memory. Every response
carries provenance and the limitations that apply to the numbers in it —
deliberately repetitive, so a figure cannot be lifted out without its caveats.

Vector tiles are built once at start-up with `geojson-vt` and encoded per
request with `vt-pbf`. The national network is never sent as bulk GeoJSON; only
the selected closure and its detour are returned as explicit geometry.

### `apps/web`

React + Vite + MapLibre GL JS. All view state lives in the URL, so any result is
shareable. The result panel is written so that no number appears without its
qualifier.

### `pipelines`

| pipeline | command |
| --- | --- |
| discovery | `npm run discover` |
| ingestion | `npm run ingest -- --pilot wellington` |
| QA | `npm run qa -- <snapshotId>` |
| batch detours | `npm run detours -- --snapshot <id>` |
| export | `npm run export -- --snapshot <id>` |
| topology probes | `pipelines/validation/{topology,midlink}-probe.ts` |

---

## Performance

Measured on the Wellington pilot (36,395 links, 69,942 arcs, 33,025 nodes):

| | |
| --- | --- |
| Snapshot load | 381 ms |
| Detour mean | 16.9 ms |
| Detour p50 / p95 / max | 0 / 65 / 80 ms |
| Interactive target | p95 < 2,000 ms — met with margin |

Reproduce: `npm run detours -- --snapshot <id> --benchmark 600`

---

## Concurrency and scale

The batch pipeline is single-threaded, restartable and idempotent. Results
stream to NDJSON with an explicit yield so the write stream actually flushes —
without it the whole run buffers in memory and a kill loses everything.

For a national run, `worker_threads` sharding by link-id range is the obvious
next step: the graph is read-only after construction and `SharedArrayBuffer`
would let workers share it without copying.

---

## ADR-004: full port to Python, FastAPI, PostGIS and pgRouting

**Status:** accepted, implemented
**Supersedes the operative part of ADR-001.**

**Context.** ADR-001 rested on a false premise (see its correction note). Once
Python and a WSL route to PostgreSQL were confirmed, the reference stack became
achievable, and the decision was taken to build it.

**What was provisioned.** No Docker was needed: WSL Ubuntu 24.04 carries all of
it in `apt`.

| Component | Version |
| --- | --- |
| PostgreSQL | 16.14 |
| PostGIS | 3.4.2 (GEOS 3.12.1, PROJ 9.4.0) |
| pgRouting | 3.6.1 |
| Python | 3.12.3 |
| FastAPI / psycopg | 0.140 / 3.3.4 |

Provisioning is scripted and idempotent: `scripts/wsl-setup-db.sh`.

**What changed.**

| Concern | Before | After |
| --- | --- | --- |
| Storage | Immutable files, typed arrays | PostGIS tables, `sql/migrations/` |
| Routing | Hand-written arc-expanded A* | `pgr_dijkstra` over the `arcs` table |
| Projection | Hand-written Redfearn series | PROJ via pyproj |
| API | Fastify | FastAPI |
| Tiles | geojson-vt + vt-pbf | `ST_AsMVT` in the database |
| Spatial ops | Hand-written | PostGIS / Shapely STRtree |

The React + MapLibre client was **not** ported: it was already the specified
stack. The response contract was kept identical, so the same client runs against
either service unchanged — which is what made the cross-validation below
possible.

**Turn restrictions.** The Ubuntu pgRouting build does not ship `pgr_trsp`
(verified: `scripts/probe-pgrouting.sh`). Banned manoeuvres are handled with an
edge-expanded graph, `arc_transitions`, where a node is an arc and a prohibited
turn is expressed by the absence of an edge. Because AMDS publishes only 60
restricted turns nationally, routes take the cheap plain-graph path first and
only fall back to the expanded graph when a candidate route actually violates a
restriction — always correct, rarely expensive.

**Measured cost.** This is the honest trade, now quantified on the Wellington
pilot rather than predicted:

| | TypeScript (in-memory) | Python + pgRouting |
| --- | --- | --- |
| Single shortest path | ~2 ms | **39.9 ms** |
| Full closure analysis (mean) | 16.9 ms | **190 ms** |
| p95 | 58 ms | 265 ms |
| Pilot batch, 19.5k links | 5.3 min | ~62 min |

Each `pgr_dijkstra` call reloads the entire edge set — 69,944 arcs for the
pilot — which is the whole of the difference. One optimisation was applied
where it mattered: the corridor search now resolves all its probe distances in a
single multi-target call instead of one per probe, cutting the mean from 469 ms
to 190 ms.

Interactive performance is still comfortably inside target (p95 265 ms against a
2 s goal). Batch throughput is the real cost, and it is a defensible one to pay
for SQL access, QGIS connectivity and a standard stack.

**What was gained.** SQL for ad-hoc analysis; QGIS can connect directly to the
database; the tile pipeline lives in PostGIS so tiles cannot drift from the data
being routed on; and the stack is the one a GIS team would expect to maintain.

---

## Cross-validation: two engines, one specification

The TypeScript engine was kept rather than deleted, which turned out to be worth
far more than the code itself. Two independent implementations — different
languages, graph representations and shortest-path libraries — computing the
same quantity over the same source data is a stronger check than either one's
own test suite.

Run it with `python -m nzcl.crossvalidate --ts-url http://<host>:8787`.

**It immediately found a real bug present in BOTH engines.**

The first run agreed on 99.74% of 378 direction-results, with one distance
disagreement of 3,611 m on Raiha Street. Investigation (`nzcl.diagnose`) showed
every link of the TypeScript route existed in the pgRouting graph, but two of
them were not adjacent there. Measuring the geometry directly settled it: the
two links met end-to-end **0.4 mm apart**, yet had been assigned different
nodes.

The cause was grid quantisation. Both engines snapped a coordinate to a single
grid cell — `round(x / tolerance)` — so two points either side of a cell
boundary get different keys no matter how close they are. The junction was
severed and a detour was sent 3.6 km the wrong way.

Both now probe the 3×3 cell neighbourhood and merge with the nearest node inside
the tolerance. Regression tests in both suites cover a pair deliberately placed
astride a cell edge.

After the fix, both engines independently produce **33,015 nodes** for the
Wellington pilot, and cross-validation reports:

```
250 links sampled, 478 direction-results compared
  status agreements:      478      status disagreements: 0
  distance agreements:    478      distance disagreements: 0
  agreement: 100.0%   median delta 0.026 m   max delta 0.118 m
```

The residual 12 cm is floating-point and geometry-rounding difference between
the two stacks.
