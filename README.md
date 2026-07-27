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

## Quick start

```bash
.\scripts\bootstrap.ps1
```

```bash
.\scripts\download-pilot.ps1
```

```bash
.\scripts\run-dev.ps1
```

Then open <http://localhost:5173>.

Bootstrap installs dependencies and creates `.env`. The pilot download runs
source discovery and ingests the Wellington region (~5 minutes). `run-dev`
starts the API and the map.

A free [LINZ Basemaps](https://basemaps.linz.govt.nz/) key in
`VITE_LINZ_API_KEY` adds the background map. The app runs without one.

**Requires Node 20.11+ only** — no database or container runtime needed to run it. That is a design choice, not a limitation of the machine; see
[ADR-001](docs/ARCHITECTURE.md#adr-001-node--typescript-instead-of-python-postgis-and-pgrouting).

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

Wellington pilot — 29,053 source links → **36,395 graph links**, 69,942 arcs,
8,473 km of road.

Full batch: **19,573 / 19,573 links complete**, 316.7 s, p95 **58 ms**,
**zero timeouts**.

The highest-criticality links the model found, with no local knowledge encoded:

| Road | Added distance if closed | Ratio |
| --- | --- | --- |
| Moonshine Road | +56.0 km | 81× |
| Paekākāriki Hill Road | +35.3 km | 71× |

Both are hill roads linking the Hutt Valley to the Kāpiti Coast — exactly the
links a Wellington resilience practitioner would nominate.

The model also independently rediscovered **Cook Strait**: the second-largest
connected component is Marlborough, and Waiheke Island appears as its own
ferry-only component.

National snapshot: **272,441 / 272,441 links downloaded**, 375,485 graph links,
147,848 km, p95 561 ms per closure.

---

## The finding that mattered most

AMDS does not split a through road where a side road terminates on it, and
publishes no from/to node ids. Building the graph from coincident endpoints
alone gave **5,719 components with the largest holding 21% of links** — not a
road network.

Measurement showed **7,119 of 16,463** dangling endpoints sat within 10 mm of
another link's *interior*. Splitting there — and **never** where two interiors
merely cross, which is what preserves every overbridge and tunnel — gives
**273 components, largest 87.3%**.

Full detail: [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) §2.

---

## Commands

| Task | Command |
| --- | --- |
| Setup | `.\scripts\bootstrap.ps1` |
| Discover source | `npm run discover` |
| Ingest pilot | `npm run ingest -- --pilot wellington` |
| Ingest nationally | `.\scripts\download-national.ps1` |
| Quality report | `npm run qa -- <snapshotId>` |
| Batch all links | `npm run detours -- --snapshot <id>` |
| Benchmark | `npm run detours -- --snapshot <id> --benchmark 600` |
| Export CSV + XLSX | `npm run export -- --snapshot <id>` |
| API only | `npm run api` |
| Web only | `npm run web` |
| Tests | `npm test` |

Batch runs are restartable: add `--resume`.

---

## Excel output

`npm run export` writes CSV and a five-sheet workbook to `data/exports/`:
**Link Detours** (one row per link and direction, with a permalink back into the
app), **Network Metadata**, **Quality Summary**, **Metric Definitions** and
**Source Lineage**. Values are computed by the validated backend — there are no
spreadsheet formulas that could drift from the engine.

---

## Layout

```
packages/core     graph, routing, topology, metrics — no dependencies
apps/api          Fastify API + vector tiles
apps/web          React + MapLibre GL JS
pipelines/        discovery, ingestion, validation, detours, export
scripts/          PowerShell entry points
docs/             architecture, sources, licensing, limitations, validation
tests/            67 unit + 16 integration
data/             snapshots and exports (gitignored)
```

---

## Documentation

| Document | Contents |
| --- | --- |
| [SOURCE_DISCOVERY.md](docs/SOURCE_DISCOVERY.md) | How the AMDS service was found, its schema, what it lacks |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Every input, and what is deliberately not used |
| [LICENSING.md](docs/LICENSING.md) | Attribution, and the open question about AMDS licence terms |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design and three ADRs |
| [METRIC_DEFINITIONS.md](docs/METRIC_DEFINITIONS.md) | What every number means |
| [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | Twelve limitations, each measured |
| [VALIDATION_PLAN.md](docs/VALIDATION_PLAN.md) | Every test and its result |

---

## Attribution

Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, maintained
by New Zealand Road Controlling Authorities, the Department of Conservation and
NZTA. Basemap © LINZ, CC BY 4.0.
