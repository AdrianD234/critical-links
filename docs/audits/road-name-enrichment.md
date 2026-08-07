# Naming the other two thirds

Dated 7 August 2026. Snapshot `amds-national-2026-07-28-5b359d84`.

The reported defect was a tooltip reading `(unnamed link)` on a section of
State Highway 3. Behind it sat two separate problems: the project's own source
was being read badly, and 62.7% of graph links had no name from any source at
all.

The first is recorded in
[`road-name-native-fixes.md`](road-name-native-fixes.md). This is the second:
matching the remaining links against external authorities, what that is worth,
and what it is safe to display.

---

## Result

| | Graph links | |
| --- | ---: | ---: |
| Named before this work | 139,980 | 37.3% |
| **Named now** | **249,424** | **66.4%** |
| Name known but not displayed | 25,997 | 6.9% |
| Genuinely nothing found | 126,261 | 33.6% |

The reported link — 373604, `{1991823e-c175-4c71-a684-70f578b699be}` — now reads
**State Highway 3**, matched from LINZ at high confidence, classified
`route_designation_only`.

Topology and routing are byte-identical before and after. Naming does not
touch `links`, `nodes`, `arcs` or `arc_transitions`, and that is checked rather
than asserted.

---

## Sources, and the one that could not be used

All three were verified reachable and useful. Only two can be *displayed*.

| Source | Features | Role | Displayed? |
| --- | ---: | --- | --- |
| LINZ NZ Addresses: Road Sections | 250,409 | canonical names, stable ids | ✅ CC BY 4.0 |
| NZTA Street Names | 388,416 | names + the only `isunnamed` flag | ❌ no licence |
| NZTA RAMM carriageway | 10,866 | SH route, corridor, ramp context | ❌, and never a name |

### NZTA Street Names is a basemap layer, not a dataset

Its own portal item describes it: *"Street names for use with aerial photo base
maps. Major road names appear at a higher level and other road names appear the
more you zoom in."* Empty `copyrightText`, empty `licenseInfo`, empty
`accessInformation`, hosted on NZTA's enterprise portal, and absent from
data.govt.nz — which catalogues 78 other NZTA datasets.

That is a cartographic labelling service. Reading it offline to corroborate a
match is one thing; redistributing it as the road names in an application is
another, and nothing published grants that.

**So it is matched against, scored, stored and reported — and never shown.**
25,997 links have a name only from this source. The interface states that those
links have a name it is not licensed to display, which is a different and far
more actionable statement than "unnamed".

The same applies to `officially_unnamed`. NZTA Street Names holds the only
authoritative unnamed classification available anywhere — 78,777 features
flagged `isunnamed` — and 3,245 graph links were classified from it. None are
displayed. **That classification is the single biggest thing a licence
conversation would unlock**, because it is the only way to tell a road that has
no name from a road whose name was not found.

### LINZ is cleared, on evidence

The WFS publishes empty `ows:Fees` and `ows:AccessConstraints`, so the service
itself says nothing. The government's own catalogue does:

```
catalogue.data.govt.nz  package_search
  title      NZ Addresses: Road Sections
  org        Land Information New Zealand
  license_id CC-BY-4.0
  url        .../layer/123109-nz-addresses-road-sections/
```

Same layer id this project reads. Attribution is published with every displayed
name, read from the database rather than hard-coded.

---

## How a match is decided

Matching is at the **AMDS source feature**, reassembled from its graph children
rather than re-downloaded, and propagated back to them. Matching split children
independently is how one road ends up with three different names.

Nearest line is not sufficient, and it fails in a specific way: it produces
*confident* wrong answers at dual carriageways, interchanges, frontage roads and
service lanes, all of which are the nearest line to something they are not.

Each candidate is scored on:

- coverage of the road, from 21 sample points at a 12 m tolerance;
- coverage of the candidate, which catches a long arterial offered for a 40 m
  stub;
- mean and maximum separation;
- heading difference, undirected;
- endpoint proximity and length ratio;
- locality, territorial authority and state-highway agreement;
- **the margin over the best candidate carrying a different name.**

The last one does most of the work. A match is trustworthy when the
alternatives are visibly worse, and 1,055 candidates in the proof of concept
were held back from high confidence for exactly that reason.

### Three rules that came from the data, not from theory

**Fragments of one name are merged before scoring.** A 5 km rural road is eight
LINZ road sections all carrying the same name. Scored individually each covers
an eighth of the road and none reaches high confidence. The scoring query
returns the full separation profile per candidate and the caller takes the
element-wise minimum across same-named features, which is exact rather than
approximate. This alone took the `long` cohort from 0 high-confidence matches
to 32 of 50.

**A designation is not a rival to a street name.** NZTA calls a road "GREAT
SOUTH ROAD"; LINZ calls the same alignment "State Highway 1". Reading that as a
disagreement suppressed real street names and was the largest single source of
false conflicts. The road has a name and carries a route; both are recorded.
Two *different* designations — State Highway 1 against State Highway 59 — is a
real conflict and stays one.

**A ramp is detected from the candidate names.** AMDS flags ramps on the
route-name record, which is exactly what an unnamed feature lacks, and RAMM's
`shRampType` misses many. But both name sources write ramps out in full —
"QUARRY ROAD OFF RAMP", "LAMBIE DRIVE ON RAMP" — so any candidate name matching
a ramp pattern marks the whole feature as a ramp, and a ramp is never adopted
automatically. It runs alongside the mainline for its whole length; geometry
cannot separate them however good the numbers look.

---

## Proof of concept

3,131 distinct source features, drawn from the unresolved population.

| Cohort | Size | HIGH | MEDIUM | LOW | NONE |
| --- | ---: | ---: | ---: | ---: | ---: |
| every unresolved state highway | 2,673 | 757 | 1,616 | 57 | 243 |
| urban | 150 | 115 | 21 | 4 | 10 |
| rural | 150 | 112 | 26 | 2 | 10 |
| LINZ-controlled | 100 | 19 | 3 | 2 | 76 |
| ramps | 50 | 0 | 46 | 2 | 2 |
| longest unresolved | 50 | 32 | 6 | 3 | 9 |

Cohorts overlap; a feature is scored once and reported under each cohort it
belongs to.

Sampling is by `md5(closure_group_id)`, not a random draw — no seed to record,
and a rule change is measured against the same roads.

### Measured precision

Precision is measured by a check that is **independent of the decision**. The
matcher decides from geometry and margin. Whether NZTA Street Names and LINZ
Road Sections — maintained separately, by different organisations — arrive at
the same name is not an input to that decision, so their agreement is a
measurement of it rather than a restatement.

| | |
| --- | ---: |
| HIGH-confidence matches | 1,034 |
| …where both sources hold a comparable name | 657 |
| …that agree | 652 |
| **Two-source agreement** | **99.24%** |

The first version of the matcher scored **94.0%** on this measure. The three
rules above are what closed the gap, and each was derived by reading the
disagreements rather than by tuning a threshold.

**The limitation, stated plainly:** 377 of the 1,034 high-confidence matches
(36%) have only one source holding a comparable name, so no cross-source check
is possible for them. Their geometry is statistically indistinguishable from the
corroborated ones — coverage median 1.00 against 1.00, mean separation median
1.18 m against 0.76 m — but that is a similarity argument, not a measurement.

### The five remaining disagreements, individually

- **4** are cases where LINZ names a road ("Service Lane", "Little Regent
  Street", "Fantail Avenue") while the highest-scoring NZTA feature at the same
  place is one it marks unnamed. NZTA generally also holds a named feature
  there; the unnamed sibling simply scores higher.
- **1** is a real conflict: NZTA says "WHAKATU DRIVE", LINZ says "Salisbury Road
  Extension", on a state highway near Hastings. Surfaced, not resolved.

No systematic failure appears on ramps, divided carriageways, parallel roads or
grade-separated crossings. Zero high-confidence matches carry a ramp-shaped
name, zero come from RAMM, and zero are corridor- or route-code-shaped.

### What review means here

The five disagreements above were examined individually. A deterministic sample
of 45 high-confidence matches was read in full, with source, corroboration
state, coverage, separation and rival for each. Automated checks assert that no
high-confidence match is a corridor name, a RAMM route code, a ramp name, or a
reference-station string.

**A human has not reviewed these matches.** The 99.24% figure is a two-source
agreement rate, and it is offered as what it is.

---

## National enrichment

Applied at HIGH confidence only, and only to features with no native AMDS name.
**A native AMDS name is never overwritten and never even compared against** —
the question at this stage is only what to do about links that have none.

| Confidence | Source features |
| --- | ---: |
| HIGH | 98,559 |
| MEDIUM | 23,785 |
| LOW | 4,061 |
| NONE | 54,326 |

Every candidate and its evidence is retained in `road_name_candidates` — 516,211
rows — so a rule change can be re-scored without re-running the spatial work,
and a reviewer can ask why something was rejected.

### Name states, nationally

| State | Source features | Graph links |
| --- | ---: | ---: |
| `externally_enriched` | 95,722 | 132,124 |
| `amds_named` | 88,002 | 127,127 |
| `unresolved` | 82,172 | 100,273 |
| `route_designation_only` | 3,657 | 12,695 |
| `officially_unnamed` | 2,799 | 3,245 |
| `ambiguous_conflict` | 74 | 232 |

Where a name is displayed, the state is what the reader sees; where it is not,
the interface says which kind of no-name it is:

| State | Shown in the name position |
| --- | --- |
| `officially_unnamed` | "Unnamed road" |
| `ambiguous_conflict` | the chosen name, plus "sources disagree" |
| `unresolved` | "Name not recorded" |
| withheld for licensing | "Name not recorded", plus "name withheld pending licence" |

`(unnamed link)` was doing all four of those jobs at once.

### Attribution moves, names do not

Where both sources independently reach the same name and both would stand on
their own, the name is credited to the one whose licence permits display. That
is attribution only — a source is never promoted to supply a name it did not
earn on its own geometry, and there is a test for it.

The effect is large: it took the withheld count from **135,919** links down to
**25,997**, without changing a single displayed name.

---

## Nothing moved

`nzcl-names verify` digests every table a route search reads, and re-runs 41
real detours — evenly spaced by link id, plus link 373604 — recording every
number they produce. Taken before the naming work and again after the full
national enrichment:

```
  arc_transitions    unchanged
  arcs               unchanged
  closure_groups     unchanged
  geometry           unchanged
  links              unchanged
  nodes              unchanged
  routing            unchanged

PASS: every topology and cost fingerprint is byte-identical
```

---

## What is not done

- **NZTA Street Names has no licence.** 25,997 named links and the entire
  `officially_unnamed` classification are stored, evidenced and withheld. This
  is a conversation with NZTA, not an engineering task.
- **36% of high-confidence matches have no second source.** Their geometry
  matches the corroborated population, but that is inference.
- **23,785 MEDIUM matches are unused.** Most were downgraded by the
  rival-name rule, which is the correct treatment in the absence of a
  tie-breaker; a human review pass would recover a large share of them.
- **LINZ-controlled roads remain mostly unnamed** — 76 of the 100 sampled found
  nothing. Why is still unestablished. It is the largest single unresolved
  cohort at 67,300 source features, and the earlier claim that these are
  "predominantly unformed legal road" was withdrawn as unsupported and has not
  been replaced with anything better.
- **Refresh cadence for either external source is unknown.** Names are matched
  against a snapshot taken on one day; nothing re-checks them.
