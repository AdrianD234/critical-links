# NZ Critical Links

Road-network criticality and detour analysis for New Zealand, built on the
**NZTA Waka Kotahi AMDS Network Model**.

Apply a **modelled closure** to any road link and see the shortest replacement
path available in the represented network: how much further that path is, and —
when the network offers none — which links lose connectivity.

Nothing here observes a road being closed. You posit a closure; the engine
answers a question about the graph.

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

Select a road and the engine removes the segment you selected, works out which
trips genuinely crossed it, and reports what each has to do instead:

| Measure | Question it answers |
| --- | --- |
| **Through movement** | Which crossings of this closure existed, and which one the figures describe |
| **Replacement route** | The shortest represented-network route for that crossing with the segment removed |
| **Network penalty** | How much longer that is than the same crossing with the segment in place |
| **Physical isolation** | Which links lose access, computed on the undirected graph, independent of any route search |

The measurement is across the closure BOUNDARY, not between the closed
segment's own two endpoints. The difference is not a refinement: a replacement
route in practice leaves and rejoins the network well beyond the closure, so an
endpoint pair is measured from the wrong two places — and on a one-way
carriageway there is no path from a link's end back to its own start at all, so
the endpoint question has no answer while traffic gets past perfectly well.

None of these is a statement about traffic. The engine computes shortest paths
between nodes; it does not know which vehicles used the road, how trips
redistribute, or whether anyone would take the replacement route. Where a
closure has many crossings the panel names the one being measured and lists the
rest, because a figure with no subject is not checkable.

Toggles for distance/time and car/heavy/emergency, and closure scope. **The
default closure scope is the road segment you selected.** Closing every graph
link derived from one AMDS source feature remains available as an advanced
choice, and states its own cost — the kilometres and the segment count — before
any figure, because an AMDS source feature is a data-maintenance unit that may
cover more road than was selected. Every view is a permalink.

What the engine reports and does not claim:

- routes are through the **represented network**, assembled from AMDS geometry,
  not the road network as surveyed;
- the topology is **inferred**, junctions included, and its confidence is
  reported per result rather than filtered out;
- times are **estimated**: AMDS publishes no speed attribute;
- turn restrictions are **post-validated**, not enforced during routing. A route
  using a published applicable restriction is withheld rather than offered, but
  that is weaker than a search constrained by the restrictions from the start —
  and AMDS publishes 60 restricted turns for the whole country, of which one
  restricts any modelled vehicle class;
- a **bounded** search that did not evaluate every crossing says so, and is not
  allowed a headline implying it looked at everything.

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

## Three findings worth reading

### AMDS does not split roads at junctions

Building the graph from coincident endpoints alone gave **5,719 components with
the largest holding 21% of links** — not a road network. Measurement showed
**7,119 of 16,463** dangling endpoints sat within 10 mm of another link's
*interior*: AMDS does not cut a through road where a side road ends on it.

Splitting there — and **never** where two interiors merely cross, which is what
preserves every overbridge and tunnel — gives **268 components, largest 87.3%**.

### Two thirds of links had no name, and a third still do not

The reported symptom was a tooltip reading `(unnamed link)` on a section of
State Highway 3. Two separate causes: AMDS was being read badly, and AMDS does
not carry a name for most links in the first place.

Reading it properly changed **13,633** labels and added **56** — a correctness
change, not a coverage one. `routeNameFullASCII` turned out not to be a
transliteration of `routeNameFull` but a separately maintained column that
disagrees with it, sometimes completely: one record reads "Kotare Lane" in one
and "SH 1N/458 RAMP (SH) #4 OFF" in the other, and the structured name
components agree with the former.

Coverage came from matching against LINZ NZ Addresses: Road Sections, adopted
at high confidence only:

| | Graph links | |
| --- | ---: | ---: |
| Named before | 139,980 | 37.3% |
| **Named now** | **249,424** | **66.4%** |
| Name known, licence unconfirmed | 25,997 | 6.9% |

Measured two-source agreement on the adopted class is **99.24%**, using a check
independent of the matcher's own decision. Full working, including what that
figure does not cover, is in
[docs/audits/road-name-enrichment.md](docs/audits/road-name-enrichment.md).

Naming is a layer over the graph, not a column inside it: `links`, `nodes`,
`arcs` and `arc_transitions` are never written by it, and 41 real detours are
re-run before and after to prove the numbers did not move.

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
| Road names: backfill from AMDS | `python -m nzcl.names backfill` |
| Road names: load external sources | `python -m nzcl.names sources` |
| Road names: match nationally | `python -m nzcl.names enrich` |
| Road names: coverage report | `python -m nzcl.names report` |
| Prove naming changed no routing | `python -m nzcl.names verify --probe 40 --baseline <file>` |
| API | `uvicorn nzcl.api:app --port 8000` |
| Both test suites | `.\scripts\test.ps1` |

---

## Performance

Measured on the Wellington pilot (36,397 links, 69,944 arcs):

| | Python + pgRouting | TypeScript (in-memory) |
| --- | --- | --- |
| Single shortest path | 39.9 ms | ~2 ms |
| Full closure analysis (mean) | 179 ms | 16.9 ms |
| p95 | 276 ms | 58 ms |

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
| [ROAD_NAME_SOURCES.md](docs/ROAD_NAME_SOURCES.md) | Where road names come from, and which may be shown |
| [VALIDATION_PLAN.md](docs/VALIDATION_PLAN.md) | Every test and its result |

---

## Attribution

Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, maintained
by New Zealand Road Controlling Authorities, the Department of Conservation and
NZTA. Basemap © LINZ, CC BY 4.0.

Road names where AMDS has none are sourced from the LINZ Data Service and
licensed by Land Information New Zealand for reuse under CC BY 4.0.
