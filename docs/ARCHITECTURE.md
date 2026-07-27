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

**The constraint.** The target machine has **no Python and no Docker**. Verified:

```
$ python --version   → not found
$ docker --version   → not found
$ node --version     → v24.15.0
```

Writing the specified stack would have produced code that could not be run,
tested or demonstrated here — and the brief was explicit that a working pilot
was required, not an implementation plan.

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
