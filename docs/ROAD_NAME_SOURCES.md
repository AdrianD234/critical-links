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

## B. LINZ NZ Addresses: Road Sections — authoritative names

**Verified working, 6 August 2026.**

| | |
| --- | --- |
| Layer | `layer-123109` — **"NZ Addresses: Road Sections"** |
| Access | LDS WFS, `LINZ_LDS_API_KEY` (server-side, no `VITE_` prefix) |
| Features | **250,409** |
| CRS | EPSG:2193 available; WFS will reproject |

> **The layer is not called "Roads Subsections (Addressing)" any more.** It was
> found by reading `GetCapabilities` from the live service, which lists 1,838
> feature types. Had the documented name or a historical layer id been assumed,
> this would have failed — the reason for the rule against assuming ids.

Fields:

`road_section_id` (stable id) · `road_id` · **`full_road_name`** (canonical
Unicode) · `full_road_name_ascii` · `road_name_label` / `_body` / `_type` /
`_suffix` · `secondary_road_name` · `tertiary_road_name` · `suburb_locality` ·
`town_city` · `territorial_authority` · `is_land` · ASCII variants of each

**No `isunnamed` field.** This is an *addressing* layer: it carries roads that
have names. Officially-unnamed classification must come from the NZTA Street
names layer instead — the two sources are complementary, not alternatives.

### Key scope

A key created with **"Data access only"** works for WFS and is the correct
choice. It returns `401 Invalid API key scope` on the `/services/api/v1.x/`
catalog API, which needs a different scope. Use WFS; do not request a broader
scope than the work needs.

⚠️ WFS `DescribeFeatureType` responses embed the key in `schemaLocation` URLs.
Any saved response must be redacted before it is committed — the copy under
`data/source-metadata/linz-road-sections/` has been.

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

## D. NZTA Street Names — the officially-unnamed source

**Verified working, 6 August 2026.** No key required.

```
https://spatial.nzta.govt.nz/portal/rest/services/Hosted/Street_names/FeatureServer/0
```

> Initially reported as "not found". That was wrong: it is on NZTA's **Enterprise
> Portal** (`spatial.nzta.govt.nz`), not the ArcGIS Online organisation
> (`services.arcgis.com/CXBb7LAjgIIdcsPt`) that hosts AMDS and RAMM. Enumerating
> one host and concluding the layer did not exist was a bad inference from an
> incomplete search.

| | |
| --- | --- |
| Features | **388,416** |
| CRS | **EPSG:2193** — the analysis CRS, no reprojection needed |
| Capabilities | `Query` only (no extract endpoint) |
| Fields | 70 |
| `copyrightText` | **empty** |
| `description` | **empty** |

Fields that matter here:

`fullprimaryroadname` · `primaryname` · `alternateroadname` ·
`pseudonymroadname` · **`isunnamed`** · `isstatehighway` · `isprivate` ·
**`linzrdsegid`** · `leftlocalityname` / `rightlocalityname` · `lefttaname` /
`righttaname` · `referencestation` · `bridgename` · `tunnelname` · `oneway` ·
`isdualcarriageway` · `status` · `retireddate` · `changedate` · `classification`
· `hierarchy` · `surfacetype`

### `isunnamed` — the field this workstream needs most

| `isunnamed` | Features |
| --- | --- |
| False | 309,639 |
| **True** | **78,777** |

This is the authoritative basis for classifying a road as *officially unnamed*
rather than *unresolved*. Nothing else available supplies it.

### `status`

| Status | Features |
| --- | --- |
| In use | 368,480 |
| Unformed surveyed | 10,969 |
| Unsurveyed proposed | 6,337 |
| Under construction | 2,101 |
| Disused | 529 |

`Unformed surveyed` is the paper-road category. Combined with `isunnamed`, this
is what distinguishes an unformed legal road from a naming failure.

`retireddate` uses a far-future sentinel (year 2500) for live records rather
than null — filter on it carefully.

### LINZ lineage — now evidenced, not inferred

For the screenshot link, NZTA reported `linzrdsegid: "143560, 305857, 305858,
305859"` and LINZ independently returned `road_section_id: 143560`. **The
identifier matches**, which confirms the layer is LINZ-addressing-derived rather
than merely resembling it.

Note `linzrdsegid` can hold a **comma-separated list**, so it is a
many-to-one join key and must be parsed, not compared as a string.

Still unresolved: refresh cadence, and licence — both `copyrightText` and
`description` are empty. Fit for proof of concept and corroboration now;
governed-input status needs those answered.

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

Resolved 7 August 2026. The state below is not documentation — it is the
`name_source_licences` table, which the display view joins. A source that is
not cleared there cannot reach the interface, whatever this page says.

| Source | Licence | Cleared for display? |
| --- | --- | --- |
| AMDS NetworkModel + RouteName | As existing AMDS terms | ✅ |
| **LINZ NZ Addresses: Road Sections** | **CC BY 4.0** | ✅ |
| NZTA Street Names | None published anywhere it appears | ❌ |
| NZTA RAMM carriageway | None published | ❌ (context only) |
| OpenStreetMap | ODbL | 🚫 QA only, share-alike unassessed |
| LINZ Topo50 | CC BY 4.0 | 🚫 QA only, cartographic product |

### LINZ — confirmed

The WFS itself is silent: `ows:Fees` and `ows:AccessConstraints` are both empty
for the whole service, and the layer page is a JavaScript application that
returns nothing useful to a fetch. The evidence is the government's own
catalogue instead:

```
catalogue.data.govt.nz  package_search
  title      NZ Addresses: Road Sections
  org        Land Information New Zealand
  license_id CC-BY-4.0
  url        https://data.linz.govt.nz/layer/123109-nz-addresses-road-sections/
```

The URL carries **the same layer id this project reads**, which is what ties
the licence to the data rather than to a similarly named product.

Attribution is published in the interface whenever a LINZ-sourced name is
displayed, read from the database rather than hard-coded, so a source cannot
start appearing without its attribution appearing with it.

### NZTA Street Names — not cleared, and unlikely to be

This one changed on inspection, and it matters more than the others because it
is the only source of the `isunnamed` classification.

- `copyrightText` on the layer: **empty**
- `licenseInfo` on the portal item `eb19b15540a844ada92dcaf5b054174e`
  (owner `GeospatialSystems`): **empty**
- `accessInformation`: **empty**
- The item's own description: **"Street names for use with aerial photo base
  maps. Major road names appear at a higher level and other road names appear
  the more you zoom in."**
- Not on NZTA's open data portal, and **absent from data.govt.nz**, which
  catalogues 78 other NZTA datasets.

That description is the finding. This is a **cartographic labelling service on
an enterprise portal** — the layer that draws road labels under NZTA's own
aerial imagery — not a published dataset with terms of reuse. It is fit for
offline corroboration, which is how it is used; it is not fit to be
redistributed as the road names in someone else's application.

**Consequence:** 25,997 graph links have a name matched only from this source
and are displayed as unnamed. The interface says so explicitly rather than
implying those roads have no name. `officially_unnamed` is likewise computed
and stored but never displayed.

**To clear it:** NZTA must confirm terms of use for
`spatial.nzta.govt.nz/portal/.../Street_names/FeatureServer/0`, or publish the
equivalent through their open data portal. One email, and a table update.

### NZTA RAMM — not cleared, no display impact

`copyrightText` empty, not catalogued. It is used only for state-highway route,
corridor and ramp context and **no name from it is ever stored as a display
name** — its `roadName` is a route-section code and its `roadCorridor` spans
hundreds of kilometres. Clearance would change nothing that is shown.
