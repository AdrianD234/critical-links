# Data sources

Every input, where it came from, and what it is used for. Provenance for the
active snapshot is also embedded in `meta.json` beside the data and returned on
every API response.

---

## Primary — NZTA AMDS Network Model

| | |
| --- | --- |
| Service | `https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/AMDS_NetworkModel_PROD/FeatureServer` |
| ArcGIS item | `f955c118272b462e9ce757405890b87f` |
| Owner | `Publisher_NZTA` |
| Discovered via | Experience Builder app `c720e30739154520bc7d7c0fbfb2b6e5` → web map `e6daee49bcff49f9901f45a8ff25fcf6` → this service |
| Capabilities | `Query,Extract` |
| Retrieved | see `retrievedAtUtc` in the snapshot `meta.json` |
| Integrity | SHA-256 over all response pages, recorded as `rawSha256` |

### Layers consumed

| id | table | fields used | purpose |
| --- | --- | --- | --- |
| 1 | NetworkModel | `amdsIDNetworkModel`, `oneway`, `modeVehicle`, `modeVehicleHeavy`, `modeEmergencyManagement`, `modeFerry`, `modelAssetType`, `surfaceType`, `status`, `assetOwnerOrganisation`, `dataManagingOrganisation`, `amdsIDAuthority`, `lifeLineRoute`, `sharedInfrastructure`, `detour`, `Shape__Length`, geometry | The routable network |
| 2 | Authority | `amdsIDAuthority`, `controllingNameASCII` | RCA names |
| 9 | RestrictedTurn | `amdsIDNetworkModel1..8`, per-mode restricted flags, `status` | Banned manoeuvres |
| 10 | Restriction | `amdsIDNetworkModel`, height/weight/mode restriction fields | Physical limits, recorded as flags only |
| 11 | RouteName | `amdsIDRouteName`, `routeNameFullASCII`, `routeNumber1`, `routeAlpha1` | Road names, state-highway numbers |
| 12 | UrbanRural | `amdsIDNetworkModel`, `urbanRural`, `status` | Urban/rural class driving speed estimates |
| 13 | NetworkModel↔RouteName | `amdsIDNetworkModel`, `amdsIDRouteName`, `isPrimary` | Name join |

### Filter

`status=1 AND modeVehicle=1` — current, vehicle-accessible links.
272,441 nationally.

### Extraction

Coordinates requested natively in `outSR=2193`, so no reprojection occurs before
analysis. Features are pulled by explicit OBJECTID batches (POST, because a
2000-id list overflows a URL), retried with exponential backoff, and reconciled
against the requested id list. A shortfall marks the snapshot `partial`.

---

## Secondary — LINZ Basemaps

| | |
| --- | --- |
| URL | `https://basemaps.linz.govt.nz/v1/tiles/topographic/WebMercatorQuad/{z}/{x}/{y}.webp` |
| Used for | Web map background **only** |
| Key | `VITE_LINZ_API_KEY`, registered free at basemaps.linz.govt.nz |

The basemap is **not** the routing network. A road that looks right on the
basemap is no evidence that the analytical topology is correct. Do not use the
example key published in LINZ documentation; the app runs without a key and says
so on screen.

---

## Deliberately NOT used

### OpenStreetMap

**No OSM data is present in this database.** It was considered as a QA
cross-check and as a pilot fallback, but AMDS extraction succeeded outright so
neither was needed.

If OSM is added later it must stay **separable**. OSM is licensed ODbL, which
carries attribution and potential share-alike obligations for a derivative
database. Merging OSM geometry or attributes into AMDS-derived tables would put
the whole database in scope. Any future enrichment should live in its own
tables, tagged by source, and `LICENSING.md` must be revisited first.

For QA purposes, a national extract is available from
`https://download.geofabrik.de/australia-oceania/new-zealand.html`. Do **not**
use the public Overpass API to download the country.

### National Speed Limit Register

`https://www.nzta.govt.nz/partners/speed-management/national-speed-limit-register`

Not yet integrated. AMDS publishes no speed attribute, so every travel time here
is estimated. Integrating NSLR would replace those estimates and set
`speedSource: 'nslr'`. This is the single highest-value data addition
outstanding.

### Traffic counts / AADT

`https://opendata-nzta.opendata.arcgis.com/`

Not yet integrated. Required before any statement about vehicles affected,
vehicle-kilometres added, or economic disruption. Matching a state-highway count
station to a local road would need an explicit, documented methodology — it is
not a spatial join.

---

## Snapshot immutability

A snapshot id is derived from the content hash, filter, extent and processing
version:

```
amds-<area>-<date>-<sha256[0:8]>
```

Detour caches key on snapshot id **and** algorithm version, so re-ingesting the
source or changing routing semantics makes every cached result unreachable
rather than silently stale.

Raw and processed data are gitignored. Only small provenance artefacts
(`data/source-metadata/`) and test fixtures are committed.
