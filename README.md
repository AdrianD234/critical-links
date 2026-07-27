# NZ Critical Links

Road-network criticality and detour analysis for New Zealand, built on the
**NZTA Waka Kotahi AMDS Network Model**.

Close any road link and see the shortest legal replacement path: how far traffic
must go around, how much distance that adds, and — when there is no way around —
exactly what gets cut off.

> **This is structural resilience analysis, not a traffic model.** It computes
> replacement paths. It does **not** predict how much traffic uses each
> alternative route. See [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) §1.

---

## Stack

| Layer | Technology |
| --- | --- |
| Database | PostgreSQL 16.14 · PostGIS 3.4.2 · pgRouting 3.6.1 |
| Backend | Python 3.12 · FastAPI · psycopg 3 · Shapely · pyproj |
| Frontend | React · TypeScript · Vite · MapLibre GL JS |
| Tiles | `ST_AsMVT`, served straight from PostGIS |
| Basemap | LINZ Basemaps |

The database runs in **WSL** — no Docker required, Ubuntu carries the whole
stack in `apt`. Provisioning is scripted and idempotent.

A second, independent implementation of the same engine in TypeScript lives in
`packages/core` and `apps/api`. It is kept deliberately: cross-validating the
two caught a real bug that neither test suite found on its own (see below).

---

## Quick start

```bash
.\scripts\setup-python.ps1
```

```bash
.\scripts\ingest-pilot.ps1
```

```bash
.\scripts\run-dev.ps1
```

Then open <http://localhost:5173>.

Setup installs PostgreSQL/PostGIS/pgRouting in WSL, creates the database, builds
a Python venv and applies migrations. Ingest discovers the AMDS source and loads
the Wellington region (~30 s). A free [LINZ](https://basemaps.linz.govt.nz/) key
in `VITE_LINZ_API_KEY` adds the background map; the app runs without one.

---

## What it does

Select a link and the engine removes it — by default the whole physical road
asset, both directions — then re-runs a shortest-path search on the remaining
network and reports:

| Measure | Question it answers |
| --- | --- |
| **Endpoint detour** | How far must traffic travel from this link's start back to its end? |
| **Network penalty** | How much longer is that than the normal shortest path? |
| **Corridor detour** | How much longer is a *through trip*? (used where the endpoint measure is undefined, which is routine on one-way carriageways) |
| **Isolation profile** | If nothing gets past, how much road and how many links are stranded? |

Toggles for distance/time, car/heavy/emergency, whole-road/single-direction
closure, and forward/reverse. Every view is a permalink.

---

## Verified results

Wellington pilot — 29,053 source links → **36,397 graph links**, 69,944 arcs,
8,473 km of road, 87.3% in one connected component.

The highest-criticality links the model found, with no local knowledge encoded:

| Road | Added distance if closed | Ratio |
| --- | --- | --- |
| Moonshine Road (forward) | +20.9 km | 11.6× |
| Moonshine Road (reverse) | +53.5 km | 28.1× |
| Paekākāriki Hill Road | +35 km | 71× |

Both are hill roads linking the Hutt Valley to the Kāpiti Coast — exactly the
links a Wellington resilience practitioner would nominate. Moonshine Road also
shows a real directional asymmetry, which the model reports rather than averages
away.

The model independently rediscovered **Cook Strait**: nationally the largest
connected component is the North Island (63.8%) and the second the South Island
(31.4%). Waiheke Island appears as its own ferry-only component.

National snapshot: **272,441 / 272,441 links downloaded**, 375,485 graph links,
147,848 km.

---

## Two findings worth reading

### AMDS does not split roads at junctions

Building the graph from coincident endpoints alone gave **5,719 components with
the largest holding 21% of links** — not a road network. Measurement showed
**7,119 of 16,463** dangling endpoints sat within 10 mm of another link's
*interior*: AMDS does not cut a through road where a side road ends on it.

Splitting there — and **never** where two interiors merely cross, which is what
preserves every overbridge and tunnel — gives **268 components, largest 87.3%**.

### Cross-validation caught a bug in both engines

Running the pgRouting and TypeScript engines against each other found one
disagreement of 3,611 m. Two links met end-to-end **0.4 mm apart** yet had
different nodes: both engines snapped coordinates to a single grid cell, so
points either side of a cell boundary never merged however close they were. The
junction was severed and a detour went 3.6 km the wrong way.

Both now probe the cell neighbourhood. After the fix both engines independently
produce **33,015 nodes** and agree on **100% of 478 direction-results**, median
delta 2.6 cm.

```bash
python -m nzcl.crossvalidate --ts-url http://<windows-host>:8787
```

---

## Commands

| Task | Command |
| --- | --- |
| Provision database + Python | `.\scripts\setup-python.ps1` |
| Discover source | `python -m nzcl.discover` |
| Ingest pilot | `python -m nzcl.ingest --pilot wellington` |
| Ingest nationally | `python -m nzcl.ingest --national` |
| Quality report | `python -m nzcl.qa <snapshotId>` |
| Batch all links | `python -m nzcl.batch --snapshot <id> [--resume]` |
| Benchmark | `python -m nzcl.bench <snapshotId> 60` |
| Export CSV + XLSX | `python -m nzcl.export --snapshot <id>` |
| Cross-validate engines | `python -m nzcl.crossvalidate` |
| API | `uvicorn nzcl.api:app --port 8000` |
| Both test suites | `.\scripts\test.ps1` |

---

## Performance

Measured on the Wellington pilot (36,397 links, 69,944 arcs):

| | Python + pgRouting | TypeScript (in-memory) |
| --- | --- | --- |
| Single shortest path | 39.9 ms | ~2 ms |
| Full closure analysis (mean) | 190 ms | 16.9 ms |
| p95 | 265 ms | 58 ms |

Each `pgr_dijkstra` call reloads the whole edge set, which is the entire
difference. Interactive performance is comfortably inside the 2 s target; batch
throughput is the real cost of the stack, and it is stated rather than hidden.
See [ADR-004](docs/ARCHITECTURE.md).

---

## Layout

```
python/src/nzcl   ingest, topology, routing, detour, QA, export, API
sql/migrations    PostGIS schema, arc_transitions expanded graph
apps/web          React + MapLibre GL JS
packages/core     independent TypeScript engine (cross-validation)
apps/api          TypeScript API (cross-validation)
scripts/          PowerShell and WSL entry points
docs/             architecture, sources, licensing, limitations, validation
python/tests      48 tests against a real PostGIS database
tests/            70 TypeScript tests
```

---

## Documentation

| Document | Contents |
| --- | --- |
| [SOURCE_DISCOVERY.md](docs/SOURCE_DISCOVERY.md) | How the AMDS service was found, its schema, what it lacks |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Every input, and what is deliberately not used |
| [LICENSING.md](docs/LICENSING.md) | Attribution, and the open question about AMDS licence terms |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design, four ADRs, and the cross-validation story |
| [METRIC_DEFINITIONS.md](docs/METRIC_DEFINITIONS.md) | What every number means |
| [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | Each one measured |
| [VALIDATION_PLAN.md](docs/VALIDATION_PLAN.md) | Every test and its result |

---

## Attribution

Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, maintained
by New Zealand Road Controlling Authorities, the Department of Conservation and
NZTA. Basemap © LINZ, CC BY 4.0.
