# Road-name coverage: baseline before enrichment

**Snapshot:** `amds-national-2026-07-28-5b359d84` (national, complete, processing 2.0.0)
**Branch:** `feature/road-name-enrichment`, from `main` at `5edb54e`
**Date:** 6 August 2026

Measured before any change. Every figure below is reproducible from the queries
in this document against the live snapshot.

---

## Headline

| Level | Total | Named | % named | % of km named |
| --- | --- | --- | --- | --- |
| AMDS source features | 272,426 | 91,673 | **33.7%** | 36.6% |
| Graph links (what the map renders) | 375,696 | 139,980 | **37.3%** | — |

**235,716 of 375,696 rendered links (62.7%) currently display "(unnamed link)".**

The two levels differ because junction splitting turns one source feature into
several graph links. Naming should therefore be resolved at the source-feature
level and propagated, not solved per rendered link.

---

## Why: the ceiling is in the source

The AMDS `NetworkModel` polyline layer **carries no road-name field at all**.
Confirmed by inspecting layer 1: the only name-like fields are `lifeLineRoute`
and `externalSystemName`. Names live entirely in separate tables.

Counts taken directly from the service:

| | Count |
| --- | --- |
| AMDS vehicle source links (`status=1 AND modeVehicle=1`) | 272,441 |
| Rows in join table 13 (`NetworkModel` ↔ `RouteName`) | 98,266 |
| **Distinct links appearing in that join** | **96,862** |
| RouteName records (table 11) | 92,065 |
| RouteName records with `status=1` | 92,020 |

**96,862 / 272,441 = 35.6%.** That is the hard ceiling on AMDS-native naming.
No amount of ingest improvement can exceed it, because 175,579 vehicle links
have no route-name relationship in AMDS at all.

Our measured 33.7% sits just under that ceiling — so the ingest is recovering
most of what AMDS offers, and the gap is a source-data gap first and an ingest
issue second.

---

## Four defects in the current ingest

These do not explain the bulk of the gap, but each is real and each is fixable.

### 1. The route-number fallback cannot fire when it is most needed

```
road_name = name_by_link.get(amds_id)
if not road_name and number:
    road_name = f"SH {number}"
```

`number` comes from `number_by_link`, which is built from **the same join** that
supplied the name. A link with no route-name relationship therefore has no
number either, so the fallback never runs.

Measured consequence: **zero links have a route number but no name.** The
fallback only helps links that already have a relationship but blank name text.

This is why a state highway can render as "(unnamed link)" even though
`rca_code = 1` independently identifies it as one.

### 2. Macrons are discarded

The ingest reads `routeNameFullASCII`. Table 11 also carries **`routeNameFull`**
— the canonical Unicode name — plus an `isMacronated` flag. 92,012 of 92,065
records have a non-empty `routeNameFull`.

Every Māori road name currently loses its macrons for no reason. `routeNameFull`
should be the display value, with an ASCII fold kept only as a search key.

### 3. `status` is fetched and then ignored

`status` is requested from table 11 but never filtered on, so retired records
can enter. Small in practice — 45 of 92,065 are not `status=1` — but it is
unintentional rather than a decision.

Table 13 has **no** `status` field, so retired *relationships* cannot be
filtered at all.

### 4. Primary-name selection is not deterministic

```
if rid in name_by_route and (primary or lid not in name_by_link):
    name_by_link[lid] = name_by_route[rid]
```

With 78,509 primary, 17,349 non-primary and 2,408 null `isPrimary` rows, a link
with two primary relationships takes whichever arrived last from the service.
Row order is not guaranteed, so the same ingest can produce different names.

---

## Unused fields that would improve naming

Table 11 carries a great deal the ingest never reads:

`routeNameFull` · `routeNameAbbreviated` · `isMacronated` · `localityName` ·
`effectiveFrom` / `effectiveTo` · `rampNumber` · `interchangeNumber` ·
`rampType` · `routeNumber2` / `routeAlpha2` · `direction` · `routeGroup` ·
`routeSubGroup` · `authorisedAlternative` · `namePrefix` / `nameBody1` /
`nameType` / `nameSuffix` / `nameBody2`

`localityName`, `rampType` and `effectiveFrom`/`effectiveTo` are directly
relevant to the enrichment design.

---

## Where the unnamed links are

### By controlling authority (authorities with >3,000 links)

| RCA | Links | Unnamed | % |
| --- | --- | --- | --- |
| **Land Information New Zealand** | 75,924 | 75,556 | **99.5%** |
| Waimakariri District Council | 5,723 | 5,665 | 99.0% |
| Upper Hutt City Council | 3,484 | 3,441 | 98.8% |
| Palmerston North City Council | 16,143 | 14,871 | 92.1% |
| Tasman District Council | 4,661 | 4,157 | 89.2% |
| Rangitikei District Council | 3,635 | 2,951 | 81.2% |
| Selwyn District Council | 5,870 | 4,274 | 72.8% |
| Auckland Transport | 54,161 | 33,616 | 62.1% |
| Southland District Council | 8,177 | 5,000 | 61.1% |
| Dunedin City Council | 6,523 | 3,884 | 59.5% |

**This is the single most important row in the audit.** LINZ is the controlling
authority for 75,924 links — 20% of the whole network — and 99.5% are unnamed.

LINZ-administered roads are predominantly **unformed legal road** (paper roads):
legal road corridors that may have no formed carriageway. A large share are
plausibly *genuinely* unnamed rather than missing a name.

Treating those as "coverage failure" would overstate the problem by about a
third of all unnamed links. They need classifying, not naming.

### By length band

| Band | Links | Unnamed | % |
| --- | --- | --- | --- |
| <50 m | 84,133 | 54,918 | 65.3% |
| 50–250 m | 155,876 | 89,912 | 57.7% |
| 250 m–1 km | 98,551 | 67,861 | 68.9% |
| 1–5 km | 35,686 | 22,177 | 62.1% |
| >5 km | 1,450 | 848 | 58.5% |

Roughly uniform. **This is not a problem confined to short stubs or ramps** —
848 links over 5 km are unnamed.

### By urban/rural

| | Links | % unnamed |
| --- | --- | --- |
| Urban | 168,149 | 52.8% |
| Rural | 114,446 | 47.6% |
| **No classification** | **93,101** | **99.4%** |

The 93,101 links with no urban/rural value are 99.4% unnamed. The urban/rural
table is a *different* AMDS relationship, so this says the same links are
missing from multiple attribute tables — a cohort with almost no AMDS
attribution rather than a naming-specific failure.

### State highways

| | Count |
| --- | --- |
| State-highway graph links (`rca_code = 1`) | 20,917 |
| …unnamed | **4,577** |
| State-highway source features | 7,123 |
| …unnamed | **2,673** |
| Links given an `SH …` fallback name | 12,373 |

State highways are the most tractable gap: 2,673 source features, all
attributable to one authority with a well-maintained public asset register.

---

## The screenshot link

Adrian's screenshot showed a 1,371 m state-highway link rendering as
"(unnamed link)". Exactly one link nationally matches those criteria:

| | |
| --- | --- |
| `link_id` | 373604 |
| `amds_id` | `{1991823e-c175-4c71-a684-70f578b699be}` |
| `source_object_id` | 676030 |
| Length | 1,371 m |
| RCA | 1 — Waka Kotahi NZ Transport Agency |
| Position | −38.4104, 175.1151 (King Country) |
| Asset type | 1 (Roadway), two-way, rural, sealed |
| Quality flags | none |
| **Rows in AMDS join table 13** | **0** |

The mechanism is unambiguous: a real state highway with **no route-name
relationship in AMDS whatsoever**. No relationship → no name → no number → no
`SH …` fallback → "(unnamed link)".

### It is resolvable

A single RAMM carriageway candidate lies within 250 m:

```json
{"roadName": "003-0076", "roadID": 2806,
 "roadCorridor": "Hamilton to New Plymouth",
 "startName": "WEIGHT PIT",
 "endName": "ERP / FRONT OF NORTHERN SH3/4 TRAFFIC ISLAND"}
```

`003-0076` is RAMM's route-section identifier: **State Highway 3**, section
0076, in the Hamilton–New Plymouth corridor. One candidate, no ambiguity.

This link should become a named regression case: it can go from
"(unnamed link)" to **"SH 3 — Hamilton to New Plymouth"** with an authoritative
source, a source identifier and a confidence level attached.

Note that RAMM's `roadName` is a *route-section code*, not a human road name.
It supplies route designation and corridor context; it does not supply a local
street name, and must not be presented as one.

---

## What this means for the work

1. **The ceiling is 35.6% from AMDS alone.** External enrichment is not optional
   polish — it is the only route to meaningful coverage.
2. **About a third of unnamed links are LINZ-administered legal road** and
   likely genuinely unnamed. Classifying them correctly is worth more than
   trying to name them, and stops the metric being misread.
3. **State highways are the best first target**: 2,673 source features, one
   authority, an authoritative public register, and a demonstrated match.
4. **Four ingest defects are worth fixing regardless** — the broken fallback,
   the discarded macrons, the ignored status, and the non-deterministic primary
   selection. They are cheap and independent of any external source.

---

## Reproducing

Graph-link coverage:

```sql
SELECT count(*) AS graph_links,
       count(*) FILTER (WHERE road_name IS NOT NULL AND road_name <> '') AS has_name,
       count(*) FILTER (WHERE (road_name IS NULL OR road_name='')
                          AND road_number IS NOT NULL AND road_number<>'') AS number_only,
       count(DISTINCT closure_group_id) AS source_features
  FROM links WHERE snapshot_id = 'amds-national-2026-07-28-5b359d84';
```

### Committed extracts

Only the actionable subsets are committed. The full per-feature table is 24 MB
and entirely derivable from the snapshot, so it is regenerated rather than
stored.

| File | Rows | What |
| --- | --- | --- |
| `data/audits/unnamed-state-highways.csv` | 2,673 | Every unnamed state-highway source feature, with midpoint coordinates — the first enrichment target |
| `data/audits/unnamed-longest-200.csv` | 200 | Longest unnamed features nationally |
| `data/audits/road-name-coverage-by-rca.csv` | 70 | Per-authority coverage and kilometres |

To regenerate the full 272,426-row table:

```sql
\copy (
  WITH src AS (
    SELECT closure_group_id,
           max(road_name) FILTER (WHERE road_name <> '') AS road_name,
           bool_or(rca_code = 1) AS is_sh, max(rca_name) AS rca,
           max(urban_rural) AS urban_rural, count(*) AS graph_children,
           sum(length_m) AS length_m
      FROM links WHERE snapshot_id = 'amds-national-2026-07-28-5b359d84'
     GROUP BY closure_group_id)
  SELECT closure_group_id, (road_name IS NOT NULL) AS named, is_sh, rca,
         urban_rural, graph_children, round(length_m) AS length_m
    FROM src ORDER BY length_m DESC
) TO 'data/audits/road-name-coverage-before.csv' WITH CSV HEADER
```

AMDS join ceiling:

```
GET .../FeatureServer/13/query?where=1=1&outFields=amdsIDNetworkModel
      &returnDistinctValues=true&returnCountOnly=true   →  96,862
```
