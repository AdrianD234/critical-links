# Known limitations

Every item here is measured or demonstrated, not assumed. Where a number
appears, the command that produced it is given.

---

## 1. This is not a traffic model

The tool computes the **shortest valid replacement path** when a road is closed.
It does **not** predict how much traffic uses each alternative route.

What would be needed before any statement about vehicles affected:

- an origin–destination demand matrix
- trip purposes and time-of-day profiles
- light/heavy vehicle class split
- link capacities and volume–delay functions
- intersection modelling and congestion
- behavioural route choice, then all-or-nothing or user-equilibrium assignment
- calibration against observed counts

None of that is present. The distinction is stated in the API `limitations`
array on every response, on the web UI, and in the Excel export.

---

## 2. Junction topology is inferred, because AMDS does not publish it

Layer 1 has no `fromNode`/`toNode` fields, so the graph is built from coincident
polyline endpoints. That alone is not enough: **AMDS does not split a through
road where a side road terminates on it.**

Measured on the Wellington pilot extract (`pipelines/validation/midlink-probe.ts`):

- 16,463 endpoints touched by only one link
- **7,119** of them sat within 10 mm of the **interior** of another link
- only 1,044 were near another link's endpoint
- 7,911 were genuine stubs with nothing within 5 m

Endpoint-only noding gave **5,719 connected components with the largest holding
21% of links** — not a road network. After junction splitting: **273 components,
largest 87.3%**, second 9.9% (Marlborough, correctly separated by Cook Strait).

### The rule, and why it is safe

- **Split** when one link's *endpoint* lies on another link's *interior*. A road
  that stops dead on another road's centreline is terminating at it: a
  T-junction, ramp merge or side road.
- **Never split** where two links' *interiors* cross. Neither road ends there —
  that is an overbridge, tunnel or grade-separated interchange. Since AMDS
  publishes no z-level, refusing to node interior-to-interior crossings is the
  only thing preserving grade separation.

Tested in `tests/unit/topology.test.ts` (15 cases), including an interchange
fixture where a ramp cuts the motorway but an overbridge crossing mid-span does
not.

### Residual: near misses

Split tolerance is 50 mm. **15,304** endpoints in the pilot lie between 50 mm and
5 m of another link and were **not** connected. They are listed in
`near-misses.json` beside the snapshot. Either the source has a genuine gap or
the tolerance is too tight; this is a data-steward question, not something the
pipeline should paper over. A tolerance sweep is available via
`pipelines/validation/topology-probe.ts`.

---

## 3. All travel times are estimates

AMDS publishes **no speed attribute**. Speeds are assigned as:

| condition | speed | source label |
| --- | --- | --- |
| Connector asset type | 20 km/h | `estimated_asset_type` |
| Unsurfaced / metalled | 40 km/h | `estimated_asset_type` |
| Urban (AMDS UrbanRural) | 50 km/h | `estimated_urban_rural` |
| Rural, NZTA-owned | 100 km/h | `estimated_urban_rural` |
| Rural, other | 80 km/h | `estimated_urban_rural` |
| No urban/rural coverage, NZTA | 90 km/h | `estimated_asset_type` |
| Otherwise | 50 km/h | `estimated_asset_type` |

Every time result carries `TIME_ESTIMATED`. **Distance is the defensible
metric.** Integrating the National Speed Limit Register would replace these and
set `speedSource: 'nslr'`; that is the highest-value next step.

---

## 4. Turn restrictions are implemented but the source is nearly empty

AMDS publishes **60 restricted turns nationally** for 272,441 vehicle links. The
engine enforces them correctly (`tests/unit/routing.test.ts` case 8), but
coverage is negligible.

**No route from this tool should be described as road-legal through a complex
intersection.**

Of the 60, 13 resolved within the pilot extract and **all 13 span more than two
links**. Two-link restrictions are enforced exactly by the arc-expanded search.
Longer sequences are checked against the predecessor chain of the best-known
path, which is a close approximation rather than an exact search — an exact
treatment needs the last L−1 arcs in the search state. Given 13 restrictions
over 36,395 links the practical exposure is negligible, but it is not zero.

A further 4 restrictions could not be resolved to a connected chain after
junction splitting and were dropped rather than guessed at.

---

## 5. The endpoint measure is undefined on one-way carriageways

The headline metric asks: after closing link *e* = (u, v), what is the shortest
path from *u* back to *v*? On a two-way road that is the right question. On a
one-way carriageway, *v* is often an internal node of the one-way system,
reachable **only** by driving that carriageway — so no *u*→*v* path exists, for
reasons unrelated to criticality.

Measured: **82% of pilot state-highway links returned DISCONNECTED** under the
endpoint measure alone. Inspecting Cobham Drive (818 m, one-way) showed its
downstream endpoint had exactly one other incident link, itself one-way outbound.

Two measures are therefore reported **alongside** the endpoint metric:

- **Corridor** — expands outward to the nearest upstream/downstream points at
  which a driver has a choice, then compares intact vs closed through-trips. For
  Cobham Drive: normal 823 m, with closure 1,273 m, **penalty 449 m**.
- **Isolation** — quantifies which side is stranded and how much of it.

In a 2,000-link sample: of 1,762 DISCONNECTED directions, 1,416 strand a pocket
of ≤3 links (cul-de-sacs and driveways) and only 19 strand ≥100 links.

The corridor search probes hop distances geometrically rather than one at a
time, so reported corridor endpoints may be slightly further out than the
minimum. Both the intact and closed paths are measured over the same corridor,
so the **penalty** stays valid.

---

## 6. The pilot is a clipped extract

The Wellington snapshot covers a 160 × 160 km extract with a 60 km network
buffer around a 40 × 40 km analysis area. Consequences:

- Results whose replacement route uses buffer links are flagged
  `ROUTE_USES_BUFFER` — still valid, that is what the buffer is for.
- **Every** DISCONNECTED result in a clipped snapshot is flagged
  `DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT`. A detour leaving the extract
  entirely cannot be ruled out.
- Only the national snapshot removes this caveat.

---

## 7. Closure groups are per source link

AMDS layer 1 publishes no carriageway or physical-asset relationship, so the
closure group is the source link. After junction splitting, all pieces of one
source link share the group, so closing a road closes the whole of it.

What this does **not** do: group the two carriageways of a divided highway, or
the several geometries of a bridge or interchange. Grouping them by proximity is
explicitly avoided — `tests/unit/routing.test.ts` case 6 asserts that closing one
carriageway leaves its neighbour 8 m away fully usable. Richer grouping needs a
source relationship that is not published.

---

## 8. Physical restrictions are recorded but not enforced

Height and weight limits from AMDS table 10 (1,372 nationally) are attached to
links as quality flags such as `HEIGHT_LIMIT_4.3m`. They do **not** constrain
routing. A heavy-vehicle route may pass under a low bridge.

---

## 9. Grid distance versus ground distance

Distances are Euclidean sums in EPSG:2193. Transverse Mercator grid distance
differs from ground distance by the point scale factor — 0.9996 on the central
meridian, rising to about 1.0006 at New Zealand's east/west extremes, so a
worst case near 0.06%. Detour **ratios** are essentially unaffected because
numerator and denominator carry the same distortion.

Projection accuracy is verified against Esri-reprojected ground truth from the
NZTA service itself (`tests/unit/geo.test.ts`): worst case 0.11 mm inverse,
0.19 mm forward, 0.30 mm round-trip across four regions.

---

## 10. Map rendering is not visually verified in this environment

The development browser pane runs with `document.visibilityState === 'hidden'`,
so `requestAnimationFrame` never fires (measured: 0 frames in 1,500 ms) and
MapLibre cannot complete style load. The map could not be screenshotted or
rendered here.

What **is** verified:

- vector tiles serve correctly (HTTP 200, 42–478 kB, valid MVT, correct CORS)
- the API returns closure and route GeoJSON with correct bounds
- the full UI renders and drives the API, confirmed by reading the live DOM
- style and layer definitions validate against the MapLibre style specification
  (`tests/unit/map-style.test.ts`)

What is **not** verified: that the map looks correct on screen. That needs one
run in a visible browser.

---

## 11. Self-loops

84 pilot links start and end at the same node. They are excluded from arc
generation and report `SOURCE_DATA_ERROR` with a `SELF_LOOP` flag rather than
being silently dropped.

---

## 12. Multipart geometry

Where a source feature has more than one path, the first is used and the link is
flagged `MULTIPART_GEOMETRY_FIRST_PATH_USED`. None occurred in the pilot.

---

## Ingest is one all-or-nothing transaction

The national ingest loads the snapshot row, nodes, links, arcs and restrictions,
**and builds the full edge-expanded transition graph**, inside a single
transaction. A failure in that last derived structure discards the entire
national download and topology processing.

This is not hypothetical: it happened twice on 28 July 2026, destroying 272,441
downloaded features each time because an auxiliary table serving 43 turn
restrictions did not build. See
`docs/audits/2026-07-28-national-ingest-incident.md`.

Planned: commit the core network first (`core_complete`), then build derived
routing structures in a separate restartable phase.

## Rebuilding re-downloads the country

The Python ingest has no import path for a previously downloaded extract, so any
processing-version rebuild re-contacts the ArcGIS service and downloads all
272,441 features again — even when a complete, sha256-pinned copy is already on
disk.

Planned: separate raw acquisition from processing-version rebuild and database
load, so a processing change rebuilds from the pinned source.

## National detour latency

Measured on `amds-national-2026-07-28-5b359d84` (375,696 links, 731,286 arcs):

| Operation | National | Wellington pilot |
| --- | --- | --- |
| Detour (both directions) | 1.1–3.0 s | ~180 ms |
| Search | 59–125 ms | — |
| Metadata | 34–77 ms | — |
| z5 tile | 1.16 MB, 228 ms | — |

Interactively usable. It needs attention before a national batch, which
multiplies it across every link.
