# Road-name sources

What can supply a road name, what each is authoritative for, and what may not be
used. Findings are dated because service schemas and availability change.

Baseline measurements are in
[`audits/road-name-coverage-before.md`](audits/road-name-coverage-before.md).

---

## The problem in one line

AMDS carries route names for **96,862 of 272,441** vehicle links — a 35.6%
ceiling. The rest need an external source or an explicit "officially unnamed"
classification.

---

## A. AMDS RouteName — native, authoritative, incomplete

**Tables 11 (`RouteName`) and 13 (`NetworkModel` ↔ `RouteName`)** on the same
FeatureServer as the network geometry. Already ingested.

| | |
| --- | --- |
| Coverage | 96,862 distinct links (35.6%) |
| Licence | As AMDS — already attributed |
| Status | **In use.** Under-used: see the four defects in the baseline audit |

Fields the ingest should start reading:

- `routeNameFull` — canonical Unicode name **with macrons**. Present on 92,012
  of 92,065 records. Currently discarded in favour of `routeNameFullASCII`.
- `localityName` — domain-coded locality, useful for disambiguation.
- `effectiveFrom` / `effectiveTo` — temporal validity.
- `rampType`, `rampNumber`, `interchangeNumber` — identifies ramps, which are
  legitimately unnamed and should be classified rather than matched.
- `routeNumber1/2`, `routeAlpha1/2`, `direction` — route designation.

**Native AMDS names must never be overwritten by an external source.** Where
they disagree, keep both and mark the record as a conflict.

---

## B. LINZ Roads Subsections (Addressing) — preferred external source

New Zealand's official record of road names and addressing. Weekly updates and
changeset support, so enrichment can later be incremental.

| | |
| --- | --- |
| Access | LINZ Data Service — **requires `LINZ_LDS_API_KEY`** |
| Distinct from | The Basemaps key already in `.env`. A different key. |
| Status | **BLOCKED** — no LDS key available yet |

Wanted from it: official name, official-unnamed indicator, road-section stable
id, locality and territorial authority, effective dates.

> **Action required:** an LDS account key from
> <https://data.linz.govt.nz/>. It must go in `.env` as `LINZ_LDS_API_KEY`
> and must not be committed.

Schema discovery, layer id and licence text are deferred until the key exists —
recording a layer id now from documentation rather than from the live service
would be the kind of assumption this project has been bitten by before.

---

## C. NZTA RAMM carriageway — state-highway specialist

**Verified reachable and useful, 6 August 2026.** No key required.

```
https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/
  GEO_MASTER_GIS_Carriageway/FeatureServer/0
```

| | |
| --- | --- |
| Records | 10,866 polylines |
| Fields | `roadName`, `roadID`, `roadCorridor`, `carrWayNo`, `carrwayStartM`/`EndM`, `startName`, `endName`, `roadClass`, `roadClassification`, `shRampType`, `roadGroup` |
| `copyrightText` | **empty** |
| Description | "State Highway Carriageway … maintained by HNO RAMM" |

Proven against the screenshot link: one candidate within 250 m, giving
`003-0076` / "Hamilton to New Plymouth" — State Highway 3, section 0076.

**`roadName` here is a route-section code, not a human road name.** It supplies
route designation and corridor context. It must not be presented as a street
name.

NZTA notes known attribute-quality issues in RAMM. Use it for state-highway
route and corridor context only — never as replacement geometry or topology.

⚠️ Empty `copyrightText` means the licence must be confirmed before this becomes
a governed production input, even though the service is public.

---

## D. NZTA Street Names — not found where expected

The consultant described a national `Street names` layer with
`fullprimaryroadname`, `linzrdsegid`, `isunnamed` and locality/TA fields — which
would be close to ideal for both matching and the officially-unnamed
classification.

**It is not on the NZTA ArcGIS organisation that hosts AMDS.** All 145 services
there were enumerated on 6 August 2026; the name-related ones are
`GEO_MASTER_GIS_Carriageway`, `National_Road_Centreline_Road_Controlling_Authority_data`
and `PlaceNames`. No street-names service.

It may live on a different NZTA organisation or portal. **Not yet located — do
not assume it exists at a guessed URL.** If found, its `linzrdsegid` would make
it valuable for corroboration, but its lineage and refresh cadence must be
established before it is trusted over direct LINZ.

---

## E. QA only — never adopted automatically

**OpenStreetMap.** Useful where official sources disagree or are silent. Kept
logically separate: ODbL share-alike has implications for a derived database
that have not been assessed.

**LINZ Topo50 roads.** A cartographic product. LINZ documents that unnamed rural
roads under 300 m may be absent, complex motorway ramps may be omitted, and
roads may be offset by up to 30 m. Unfit for line-by-line conflation; fine for
visual QA.

**Council GIS layers.** May settle stubborn local cases; too fragmented to be a
first national solution.

**The rendered LINZ basemap.** Names must never be scraped from map tiles.

---

## Matching rules

Applies to every external source.

1. **Match at the AMDS source feature, before junction splitting**, then
   propagate to graph children. Matching split children independently gives
   different names to pieces of one road.
2. **Nearest line is not sufficient.** It produces confident, wrong answers at
   dual carriageways, interchanges, frontage roads, overbridges and dense urban
   streets. Score on overlap length and ratio, mean and maximum separation,
   heading difference, endpoint proximity, length ratio, TA/RCA agreement,
   state-highway compatibility, locality agreement, agreement across adjacent
   subsections, and the margin over the best differently-named candidate.
3. **All geometry in EPSG:2193.**
4. **Retain every candidate and its score**, not only the winner.
5. **Adopt automatically only high-confidence, clearly dominant candidates.**
   Reviewed precision must reach 99% with no systematic failure at ramps or
   divided carriageways before national adoption.
6. **Never overwrite a non-blank AMDS name.** Conflicts stay conflicts.
7. **Officially unnamed is a resolved outcome, not a failure.**

---

## Source priority

1. Active native AMDS `RouteName`
2. High-confidence LINZ addressing, where AMDS is blank
3. RAMM route/corridor designation, for state highways
4. NZTA Street Names, if located and its provenance validated
5. Manual review
6. Unresolved

---

## Licensing

| Source | Licence | Attribution | Cleared? |
| --- | --- | --- | --- |
| AMDS NetworkModel + RouteName | As existing AMDS terms | Already in the app footer | ✅ |
| LINZ Roads Subsections | Expected CC BY 4.0 — **confirm from the live service** | Required, wording TBC | ⏳ no key |
| NZTA RAMM carriageway | **`copyrightText` empty — must be confirmed** | Required, wording TBC | ⚠️ |
| NZTA Street Names | Unknown | Unknown | ❌ not located |
| OpenStreetMap | ODbL | Required; share-alike unassessed | 🚫 QA only |
| LINZ Topo50 | CC BY 4.0 | Required | 🚫 QA only |

No external name may be written to `display_road_name` until its row here is
cleared, with the attribution wording recorded and shown in the interface.
