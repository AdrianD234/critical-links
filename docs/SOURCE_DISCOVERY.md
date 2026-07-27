# Source discovery — NZTA AMDS Network Model

Reproduce with:

```bash
npm run discover
```

Output lands in `data/source-metadata/amds/<YYYY-MM-DD>/` as `discovery-report.json`
(machine readable), `feature-service.raw.json` (unmodified service metadata) and
`summary.md`.

## How the data was found

The published entry point is an ArcGIS Experience Builder application, which is
**not** the data. The chain is:

| Step | Item | Type | Notes |
| --- | --- | --- | --- |
| 1 | `c720e30739154520bc7d7c0fbfb2b6e5` | Web Experience | "AMDS Network Model Application", owner `Publisher_NZTA` |
| 2 | `e6daee49bcff49f9901f45a8ff25fcf6` | Web Map | "AMDS Network Model Webmap" |
| 3 | `f955c118272b462e9ce757405890b87f` | **Feature Service** | "AMDS Network Model PROD" — the data |

A second service, `dfa1e050f3cd42828f87d67fcff5a4fb` ("AMDS Network Model
Secured PROD"), is referenced but sits behind `utility.arcgis.com/usrsvcs/` and
returns HTTP 403 without credentials. One further referenced item
(`c8344c3898064bcda655b572187bf86b`) also returns 403. **Neither is required**:
the public PROD service carries the full national network.

## The service actually used

```
https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/AMDS_NetworkModel_PROD/FeatureServer
```

- Owner: `Publisher_NZTA`
- Capabilities: `Query,Extract` — **bulk extraction is permitted**
- `maxRecordCount`: 2000
- Pagination supported; `returnIdsOnly=true` supported
- Native `outSR=2193` supported, so no reprojection is needed for analysis
- No `copyrightText` and no `licenseInfo` set on the item (see `LICENSING.md`)

### Layers and tables

| id | name | geometry | features |
| --- | --- | --- | --- |
| 0 | LinearRefSysCalibration | point | 300,504 |
| **1** | **NetworkModel** | **polyline** | **677,024** |
| 2 | Authority | table | 72 |
| 3 | Cycleway | table | 4,748 |
| 4 | Geometry | table | 666,484 |
| 5 | Lane | table | 227,591 |
| 6 | LinearRefSysNetwork | table | 70 |
| 7 | LinearRefSysRoute | table | 91,588 |
| 8 | LinearRefSysSequence | table | 203,262 |
| 9 | RestrictedTurn | table | **60** |
| 10 | Restriction | table | 1,372 |
| 11 | RouteName | table | 92,065 |
| 12 | UrbanRural | table | 212,416 |
| 13 | NetworkModel↔RouteName join | table | 98,266 |

## Layer 1 field inventory

31 fields. The ones that matter, and what is **missing**:

**Stable identifier** — `amdsIDNetworkModel` (String, GUID in braces). This is
the canonical durable id. `OBJECTID` is recorded for traceability only and is
never treated as durable.

**Direction** — `oneway` (1 = Oneway, 2 = Both Directions). Never null on the
vehicle-routable subset (verified: `status=1 AND modeVehicle=1 AND oneway IS
NULL` returns 0).

**Mode / access** — `modeVehicle`, `modeVehicleHeavy`, `modeEmergencyManagement`,
`modePedestrian`, `modeCycling`, `modeMicroMode`, `modePublicTransport`,
`modeBus`, `modeRail`, `modeFerry`.

**Classification** — `modelAssetType`, `surfaceType`, `status`,
`assetOwnerOrganisation` (RCA), `dataManagingOrganisation`, `lifeLineRoute`,
`sharedInfrastructure`, `detour`.

### What layer 1 does NOT have — and the consequences

| Missing | Consequence |
| --- | --- |
| **from/to node ids** | There is no `fromNode`/`toNode`. Topology must be derived from coincident polyline endpoints. See `KNOWN_LIMITATIONS.md`. |
| **z-level / grade separation** | Nothing marks an overbridge as passing *over* rather than joining. This is why the ingest never nodes at interior-to-interior crossings. |
| **speed limit** | No speed attribute of any kind. Every travel time in this application is an estimate. |
| **carriageway / physical asset grouping** | No relationship groups two carriageways of a divided road, so closure groups are per source link. |

## Routable subset profile

Measured by `returnCountOnly` at discovery time:

| filter | count |
| --- | --- |
| `1=1` | 677,024 |
| `status=1` | 676,880 |
| `status=1 AND modeVehicle=1` | **272,441** |
| `status=1 AND modeVehicle=1 AND modelAssetType=1` | 262,057 |
| `status=1 AND modeVehicle=1 AND oneway=1` | 9,054 |
| `status=1 AND modeVehicle=1 AND oneway IS NULL` | 0 |
| `status=1 AND modeVehicle=1 AND modeFerry=1` | 0 |

The ingest filter is `status=1 AND modeVehicle=1` — current, vehicle-accessible
links only.

## Turn restrictions: a significant finding

Table 9 holds **60 restricted turns for the entire country**, against 272,441
vehicle links. The schema is capable (sequences of up to 8 links, per-mode
flags), but it is effectively unpopulated.

The engine implements turn restrictions properly, and the tests prove banned
manoeuvres are enforced. But coverage of real-world banned turns is negligible,
so **no route produced by this tool should be presented as road-legal through a
complex intersection**. This is surfaced in the API `limitations` array, in the
UI, and in the Excel export.

## Extraction method

`returnIdsOnly=true` pins the exact OBJECTID set, then features are downloaded
in `maxRecordCount` batches **by explicit id list** rather than by
`resultOffset` paging. Offset paging over a service being edited underneath can
silently skip or duplicate rows; an id list makes a shortfall detectable. Every
batch is retried with exponential backoff, every response is hashed, and the
returned ids are reconciled against the requested ids. A shortfall marks the
snapshot `partial` — it never passes silently.

Requests are **POSTed**. A batch of 2000 OBJECTIDs in a query string exceeds the
URL limit and the service returns HTTP 414.
