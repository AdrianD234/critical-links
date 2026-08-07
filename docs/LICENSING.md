# Licensing and attribution

## AMDS Network Model

**Finding: no explicit licence string is published on the ArcGIS item.**

The discovery pipeline reads `licenseInfo` and `accessInformation` from the
ArcGIS item and `copyrightText` from the service. For
`f955c118272b462e9ce757405890b87f` all three are empty. This is recorded
verbatim in `data/source-metadata/amds/<date>/discovery-report.json` rather than
being glossed over.

What **is** established:

- The service declares capabilities `Query,Extract` — bulk extraction is
  technically permitted by the publisher's own configuration.
- NZTA's item description states the model *"is available for all users
  (everyone) to access"* and *"is available with open access to use and
  consume"*.
- The service is unauthenticated and publicly reachable.

### What this means in practice

Open access to consume is clearly intended. But "no licence string" is not the
same as "public domain", and a formal licence should be confirmed with NZTA
before any public redistribution of AMDS-derived data or before commercial use.
This is a genuine open question, not a settled one.

Attribution used throughout the application, exports and API responses:

> Contains data sourced from the NZTA Waka Kotahi AMDS Network Model, maintained
> by New Zealand Road Controlling Authorities, the Department of Conservation
> and NZTA.

It is stored in each snapshot's `meta.json`, returned on every API response, and
written into the Excel export's Source Lineage sheet.

---

## LINZ Basemaps

LINZ Basemaps is licensed **CC BY 4.0** and requires attribution. The map
displays:

> LINZ Basemaps — CC BY 4.0

Register your own free key at `https://basemaps.linz.govt.nz/`. **Do not use the
example key published in LINZ documentation.** The key is read from
`VITE_LINZ_API_KEY` and is never committed — `.env` is gitignored and
`.env.example` carries only a placeholder.

The basemap is a display layer only. It contributes nothing to the analytical
network.

---

## OpenStreetMap — not used, and why that matters

**No OSM data is present in this database.**

Had AMDS extraction failed, an OSM extract was the documented fallback. It was
not needed.

This separation is deliberate and worth preserving. OSM is licensed **ODbL**,
which carries attribution requirements and share-alike obligations for
*derivative databases*. Merging OSM geometry or attributes into AMDS-derived
tables would arguably bring the entire resulting database into ODbL scope,
which would in turn constrain how NZTA-sourced analysis could be licensed
onwards.

If OSM is introduced later:

1. Keep it in separate tables, tagged by source.
2. Never merge OSM attributes into AMDS records in place.
3. Revisit this document **before** the merge, not after.
4. Treat disagreement between OSM and AMDS as an investigation trigger, not as
   proof that AMDS is wrong.

---

## Secrets

- `.env` is gitignored; `.env.example` holds placeholders only.
- No API key appears in source, in committed URLs, or in test fixtures.
- The discovery and ingest pipelines require no credentials — everything used is
  a public, unauthenticated endpoint.
- Two referenced ArcGIS items (`dfa1e050f3cd42828f87d67fcff5a4fb`,
  `c8344c3898064bcda655b572187bf86b`) return HTTP 403. No attempt is made to
  access them.

---

## Derived outputs

CSV and XLSX exports carry the AMDS attribution on the Network Metadata and
Source Lineage sheets. Anyone redistributing those outputs inherits the same
open question about formal licence terms noted above.

---

## Road-name sources

Road names can come from outside AMDS. Each source is cleared, or not, in the
`name_source_licences` table, and the display view joins it — so an uncleared
source cannot reach the interface regardless of what any document says. Full
findings in [`ROAD_NAME_SOURCES.md`](ROAD_NAME_SOURCES.md).

### LINZ NZ Addresses: Road Sections — cleared

**CC BY 4.0.** The service itself is silent (`ows:Fees` and
`ows:AccessConstraints` are both empty for the whole LINZ Data Service), so the
evidence is the government's own catalogue:

```
catalogue.data.govt.nz  package_search
  title      NZ Addresses: Road Sections
  org        Land Information New Zealand
  license_id CC-BY-4.0
  url        https://data.linz.govt.nz/layer/123109-nz-addresses-road-sections/
```

The URL carries the same layer id this project reads, which is what ties the
licence to the data rather than to a similarly named product.

Attribution shown whenever a LINZ-sourced name is displayed:

> Contains road-name data sourced from the LINZ Data Service and licensed by
> Land Information New Zealand for reuse under CC BY 4.0.

### NZTA Street Names — not cleared

**No licence is published anywhere this layer appears**, and unlike AMDS there
is no statement of open access to fall back on. `copyrightText`, `licenseInfo`
and `accessInformation` are all empty. Its portal item describes it as *"Street
names for use with aerial photo base maps"* — a cartographic labelling service
on NZTA's enterprise portal — and it is absent from data.govt.nz, which
catalogues 78 other NZTA datasets.

Reading it offline to corroborate a match is one thing. Redistributing it as
the road names in an application is another, and nothing published grants that.

**Consequence:** 25,997 links have a name only from this source and are shown
as unnamed, and the `officially_unnamed` classification — which no other source
can supply — is computed but never displayed.

**To clear it:** NZTA confirms terms of use for
`spatial.nzta.govt.nz/portal/rest/services/Hosted/Street_names/FeatureServer/0`,
or publishes the equivalent through their open data portal.

### NZTA RAMM carriageway — not cleared, no display impact

`copyrightText` empty, not catalogued. Used only for state-highway route,
corridor and ramp context; no name from it is ever stored as a display name.
Clearing it would change nothing that is shown.
