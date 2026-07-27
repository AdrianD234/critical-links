# Metric definitions

Notation: closing link *e* joining nodes *u* and *v*. *C* is the set of directed
arcs removed by the closure. All distances are metres in EPSG:2193; all times
are seconds.

---

## Primary measure — link endpoints

**`alternative_distance_m`**

> Length of the shortest valid path from *u* to *v* after every arc in *C* has
> been removed.

$$\text{alternative\_distance}_e = \sum_{a \in P_{G-C}(u,v)} \text{length}_a$$

**`normal_shortest_path_m`** — shortest *u*→*v* distance on the intact graph.
Frequently **shorter** than the closed link itself, when a shortcut exists
between the same two endpoints.

**`added_distance_vs_link_m`** = `alternative_distance_m` − `selected_link_length_m`

Can be negative: if the closed link was itself a long way round between its own
endpoints, the replacement is shorter than the link.

**`network_penalty_m`** = `alternative_distance_m` − `normal_shortest_path_m`

**The more rigorous of the two.** It makes no assumption that the closed link
was the normal route between its endpoints — on a divided carriageway or a slip
lane it frequently is not. Prefer this for ranking.

**`detour_ratio_vs_link`** = `alternative_distance_m` / `selected_link_length_m`

A ratio of 3 means the replacement path is three times the length of the closed
road. Null when the link has zero length.

Times (`normal_path_time_s`, `alternative_time_s`, `added_time_s`) mirror the
distance measures with `cost_time_s = length_m / (speed_kph × 1000 / 3600)`.
**All are estimates** — see `KNOWN_LIMITATIONS.md` §3.

---

## Secondary measure — corridor

Reported when the primary measure returns `DISCONNECTED`, which on one-way
carriageways is routine and says little about real disruption.

- **entry node** — walk upstream from *u* along the corridor
- **exit node** — walk downstream from *v* along the corridor
- expand outward until a replacement path exists between them

**`corridor.normalDistanceM`** — intact shortest path entry→exit
**`corridor.alternativeDistanceM`** — same with *C* removed
**`corridor.penaltyM`** = alternative − normal

This is *the distance genuinely added to a through trip*. It is reported
alongside the endpoint measure, never instead of it: they answer different
questions.

---

## Tertiary measure — isolation

When no replacement path exists, what is cut off?

- **`isolation.side`** — `downstream` (beyond the closure) or `upstream`
  (behind it). Which side is stranded depends on the direction under test;
  measuring the wrong one returns the whole network.
- **`isolation.pocketLinkCount`** — links in the stranded pocket
- **`isolation.pocketLengthM`** — road length stranded
- **`isolation.exact`** — false when the search bound was hit, making the count
  a lower bound

Interpretation: ≤3 links is a cul-de-sac or driveway; ≥100 links is a
substantial area losing its connection.

---

## Status values

These are **never** conflated.

| Status | Meaning |
| --- | --- |
| `OK` | A valid replacement path was found. |
| `DISCONNECTED` | No replacement path exists between the link's own endpoints. A genuine finding about the network — but read the corridor and isolation fields before concluding traffic cannot get past. |
| `UNRESOLVED_TIMEOUT` | The search exceeded its budget. **The answer is unknown.** This is not a finding. |
| `INVALID_GRAPH` | The request referenced nodes outside the graph. |
| `SOURCE_DATA_ERROR` | The link cannot be routed on, e.g. it starts and ends at the same node. |
| `UNSUPPORTED_PROFILE` | The requested vehicle profile cannot use this link. |
| `API_ERROR` | An application fault. Not a statement about the network. |

Tested: `tests/unit/routing.test.ts` case 13 asserts a state-budget exhaustion
returns `UNRESOLVED_TIMEOUT` and specifically **not** `DISCONNECTED`.

---

## Closure scope

| Scope | Removes |
| --- | --- |
| `physical` (default) | Every arc in the closure group — all pieces of the source road, both directions. "This road is shut." |
| `directed` | Only the single arc travelling in the direction under test. The opposite direction stays open. "This carriageway is shut." |

---

## Vehicle profiles

| Profile | Requires |
| --- | --- |
| `car` | `modeVehicle` |
| `heavy` | `modeVehicleHeavy` |
| `emergency` | `modeEmergencyManagement` |

Profiles also select which turn restrictions apply.

---

## Quality flags

| Flag | Meaning |
| --- | --- |
| `SPEED_ESTIMATED` | Speed was inferred, not sourced. Always set — AMDS has no speed field. |
| `TIME_ESTIMATED` | Set on time-metric results. |
| `CLIPPED_EXTRACT` | Snapshot is a regional extract, not national. |
| `DISCONNECTED_UNVERIFIED_OUTSIDE_EXTRACT` | A detour leaving the extract cannot be ruled out. |
| `ROUTE_USES_BUFFER` | The replacement route used links outside the analysis area. Valid — that is what the buffer is for. |
| `ENDPOINT_MEASURE_UNDEFINED_CORRIDOR_USED` | The endpoint measure had no answer; read the corridor figures. |
| `SOLE_ACCESS` | Nothing can reach the far end, and the corridor found no way past. |
| `ISOLATES_CUL_DE_SAC` | Stranded pocket ≤3 links. |
| `ISOLATES_SIGNIFICANT_AREA` | Stranded pocket ≥100 links. |
| `SELF_LOOP` | Link starts and ends at the same node. |
| `SPLIT_AT_JUNCTION` | This graph link is a piece of a longer source link. |
| `NO_URBAN_RURAL_COVERAGE` | No AMDS urban/rural record; speed fell back to asset type. |
| `ONEWAY_UNSET_ASSUMED_TWO_WAY` | Direction attribute missing. Does not occur on the vehicle subset. |
| `MULTIPART_GEOMETRY_FIRST_PATH_USED` | Source feature had several paths. |
| `HEIGHT_LIMIT_*`, `WEIGHT_LIMIT_*` | Physical restriction recorded. **Not enforced in routing.** |

---

## What none of these measure

How much traffic uses each alternative route. See `KNOWN_LIMITATIONS.md` §1.
