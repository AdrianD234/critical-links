# The reported Tokoroa case

**Status: reproduced exactly, from the live national snapshot, before any code changed.**

A user screenshot showed a selected road near Tokoroa with: length ~5,201 m,
state-highway class, no displayed name ("Name not recorded" headline, "No name"
map chip), AMDS-feature closure scope, REVERSE direction, "No replacement path
in the represented network", **10 links stranded** and **13.64 km reported cut
off**.

Every one of those figures is reproduced below from `da09fbb` (the V1 code as
merged to `main`) against snapshot `amds-national-2026-07-28-5b359d84`.

---

## 1. Record identity

| Field | Value |
| --- | --- |
| Snapshot ID | `amds-national-2026-07-28-5b359d84` (national, 375,696 links, status `complete`) |
| Link ID | `375057` |
| AMDS ID | `{1073a927-4c97-4c9a-b41a-bf6f5edf0cad}#12` |
| Closure-group ID | `{1073a927-4c97-4c9a-b41a-bf6f5edf0cad}` |
| Source OBJECTID | `676730` (traceability only; not durable) |
| Graph children in closure group | **17** (`#0` … `#16`, link IDs 375045–375061) |
| Selected child length | **5,201.443692368708 m** → UI "5,201 m" |
| Total closure-group length | **17,147.35 m** |
| Removed link IDs (V1) | 375045, 375046, 375047, 375048, 375049, 375050, 375051, 375052, 375053, 375054, 375055, 375056, **375057**, 375058, 375059, 375060, 375061 |
| Removed arc IDs (V1) | 730172 … 730205 (34 arcs, contiguous) |
| Arcs of the selected segment alone | `730196`, `730197` |
| Source / target node | `46745` / `47743` |
| `oneway` | 2 (two-way; `forward_allowed` and `reverse_allowed` both true) |
| Name status | `unresolved`; `display_name` NULL; `withheld_name_source` NULL |
| Route designation | NULL as stored — **but see §4, a designation was derived and discarded** |
| RCA | `Waka Kotahi NZ Transport Agency` (`rca_code` 1) |
| Locality | Between Tokoroa and Atiamuri; selected segment runs `(175.95632, -38.29917)` → `(175.99888, -38.32953)`. Group bbox `175.88442 … 176.00576 E`, `-38.33762 … -38.24398 S`. Centroid 11.4 km from Tokoroa town centre. |
| Model asset type / surface | 1 (Roadway) / 1 (Sealed) |
| Quality flags on the link | `{SPLIT_AT_JUNCTION}` |
| Speed | 100 kph, `speed_source = estimated_urban_rural`, rural |

The closure group is a **simple chain**, node `5717` → … → node `11681`, with no
branching and no repeated node:

```
#0  5717 →28465    1.99 m      #9  46543→46145  3343.40 m
#1  28465→46563 1871.06 m      #10 46145→47550   810.64 m
#2  46563→46357  164.74 m      #11 47550→46745   184.38 m
#3  46357→46255   43.95 m      #12 46745→47743  5201.44 m  ← SELECTED
#4  46255→47472 1153.58 m      #13 47743→46227   448.94 m
#5  47472→28468  898.17 m      #14 46227→48821   298.81 m
#6  28468→28828   47.80 m      #15 48821→46321   348.91 m
#7  28828→46165  395.31 m      #16 46321→11681   924.50 m
#8  46165→46543 1009.73 m
```

## 2. Reproducing it

The API auto-selects the newest complete national snapshot, so no snapshot
parameter is needed.

```bash
# API (from repo root, with the WSL venv):
python -m uvicorn nzcl.api:app --host 127.0.0.1 --port 8000   # cwd: python/

curl -sS 'http://127.0.0.1:8000/api/v1/links/375057/detour?metric=distance&vehicle=car&closure_scope=physical&direction=both&geometry=false'
```

Equivalently, by durable AMDS id rather than the snapshot-local integer:

```bash
curl -sS --get 'http://127.0.0.1:8000/api/v1/links/%7B1073a927-4c97-4c9a-b41a-bf6f5edf0cad%7D%2312/detour' \
     --data-urlencode 'metric=distance' --data-urlencode 'vehicle=car' \
     --data-urlencode 'closure_scope=physical' --data-urlencode 'direction=reverse'
```

Or in-process, without the HTTP layer:

```bash
python -c "
import dataclasses, json
from nzcl.detour import compute
r = compute('amds-national-2026-07-28-5b359d84', 375057,
            metric='distance', profile='car', closure_scope='physical')
print(json.dumps(dataclasses.asdict(r), indent=2, default=str))"
```

The full captured response is `v1-detour-both-distance-car.json` in this
directory. The `segment-vs-source-feature.txt` file is the raw output of the
comparison in §3.

### What comes back (reverse direction, as screenshotted)

```json
"closure":  { "scope": "physical", "removedLinkCount": 17, "removedArcCount": 34 }
"selectedLink": { "lengthM": 5201.4,
                  "naming": { "status": "unresolved", "label": "Name not recorded",
                              "routeDesignation": null, "withheldSource": null } }
"reverse":  { "status": "DISCONNECTED",
              "isolation": { "side": "downstream", "pocketNodeCount": 10,
                             "pocketLinkCount": 10, "pocketLengthM": 13642.5,
                             "bounded": false, "exact": true } }
```

`13642.5 m` → **13.64 km**. `pocketLinkCount: 10` → **10 links stranded**.
`DISCONNECTED` → **"No replacement path in the represented network"**.
`label: "Name not recorded"` → the headline. Forward is identical except that
`isolation.side` reads `upstream`. Every screenshot element is accounted for.

## 3. Is 13.64 km wrong?

**The number is internally correct under V1's rules, and it is answering a
question the user did not ask.** Both halves of that matter, so both are set out.

### It is arithmetically correct

The ten stranded links sum to exactly `13,642.527 m`:

| link_id | closure_group_id | length m | nodes |
| --- | --- | --- | --- |
| 48310 | `{47b42d1d…}` | 1278.43 | 46354–46355 |
| 48617 | `{a3efbb10…}#0` | 4133.19 | 46742–46743 |
| 48618 | `{a3efbb10…}#1` | 554.60 | 46743–46744 |
| 48619 | `{a3efbb10…}#2` | 305.03 | 46744–**46745** |
| 48689 | `{2f41701f…}` | 2237.32 | 46743–46355 |
| 49790 | `{72e48905…}` | 2346.72 | 47566–46355 |
| 49791 | `{7952ef13…}` | 459.84 | 47566–47567 |
| 49792 | `{5e9d012e…}#0` | 490.60 | 47566–47567 |
| 49793 | `{5e9d012e…}#1` | 453.74 | 47567–47568 |
| 49937 | `{bf42e65f…}` | 1383.06 | 46744–47676 |
| | | **13642.53** | |

They are all `rca_code` 100 (Land Information New Zealand), `model_asset_type`
1 — the Kinleith forest block road network, not a settlement. And they really do
lose all access once the whole closure group is removed: their only connection
to the rest of the network is node **46745**, and the only two other links
incident on 46745 are `375056` (184.38 m) and `375057` (5201.44 m) — **both
members of the closure group**. So under V1's own closure definition, the
stranding is real and the arithmetic is right.

### It is answering the wrong question

The user selected a **5,201 m** segment. V1 removed **17,147 m** — 3.3× more
road — because its default scope removes every graph child of the AMDS source
feature. The result is dominated by that over-closure.

Closing only the segment that was actually selected:

```
=== SEGMENT-ONLY (link 375057, arcs 730196+730197) ===
  forward  46745->47743: OK   dist=26643.1 m
  reverse  47743->46745: OK   dist=26643.1 m
  isolation: side=none nodes=0 links=0 len=0.000

=== V1 SOURCE-FEATURE (17 links, 34 arcs) ===
  forward  46745->47743: DISCONNECTED
  reverse  47743->46745: DISCONNECTED
  isolation: nodes=10 links=10 len=13642.527  exact=True

=== can node 46744 (inside the pocket) reach the network? ===
  segment-only  : reachable node count from 46744 = 5001 (bound not reached)
  source-feature: reachable node count from 46744 = 10   (terminated)
```

So for the closure the user believes they asked for:

* a replacement path **does** exist, **26,643.1 m** long, in both directions —
  a network penalty of **+21,441.7 m** over the 5,201.4 m segment;
* **nothing at all is isolated**. Zero links, zero metres.

The 13.64 km is produced by sibling link **375056**, a 184-metre piece of the
same AMDS parent that happens to be the pocket's other way out. Closing the
selected 5.2 km segment does not strand the forest block. Closing a 184 m
segment 5 km up the road, together with it, does.

**Verdict: not an arithmetic error. A scope error.** V1 computes, correctly, the
consequence of closing 17.1 km of State Highway 1, and labels it with the length
of the 5.2 km piece the user clicked. The headline figure, the "no replacement
path" status and the stranded set are all artefacts of closing sixteen links
nobody selected.

Two further points, both of which V1 also gets wrong on this record and which
Phase 3 addresses:

* `"exact": true` is claimed on a **directed, bounded** reachability walk that
  then takes `min()` of the upstream and downstream sets. The walk terminated
  inside its 5,000-node bound here, so the set is right — but "the smaller of
  two directed reachable sets" is not the same object as "the component that
  loses physical access", and the code does not distinguish them.
* link 375057 is **not an undirected bridge** — a 26.6 km cycle exists through
  it. Under a correct isolation test, a single-segment closure here can never
  produce a "road cut off" headline at all.

## 4. The naming failure is a separate, provable bug

`link_names` records `name_status = 'unresolved'` for this group. That is not
because no source knows the name. `road_name_candidates` holds 17 rows for it,
and the top-ranked candidate from **each** of the two independent sources is
`State Highway 1`:

| source | rank | name | score | covered_frac | mean_sep_m | max_sep_m | heading_diff |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `linz_road_sections` | 1 | **State Highway 1** | 0.89995 | 1.0 | 2.24 | 7.10 | 0.1° |
| `nzta_street_names` | 1 | **State Highway 1** | 0.92459 | 1.0 | 1.33 | 4.13 | 0.9° |
| `linz_road_sections` | 2 | Campbell Road | 0.08600 | 0.0476 | 7820.19 | 15591.56 | 71.5° |

The LINZ match clears every `_geometry_is_convincing` threshold
(`covered ≥ 0.90`, `mean_sep ≤ 6.0`, `max_sep ≤ 18.0`, `heading ≤ 20.0`), and
`linz_road_sections` is `display_cleared = true` under CC BY 4.0. It should have
been displayable.

It was discarded by this line in `python/src/nzcl/namematch.py`:

```python
streets      = [s for s in scored if s.name_key and not s.is_designation_name]
designations = [s for s in scored if s.name_key and     s.is_designation_name]
named = streets or designations
```

`streets` is non-empty — it contains Campbell Road (score 0.086, 4.8 % covered,
7.8 km mean separation) and six other pieces of junk that happen to lie within
the search radius of a 17 km corridor. So `named` becomes the street list,
`best` becomes Campbell Road, `_geometry_is_convincing(best)` is false,
`covered_frac` 0.0476 fails even the LOW floor, and the outcome is `NONE`.

`classify()` *does* compute `designation = "State Highway 1"` correctly on the
line below. But `names.py` then applies the confidence floor to the whole
outcome:

```python
if rank[o.confidence] < floor:   # floor = HIGH, o.confidence = NONE
    continue                     # designation thrown away with everything else
```

So a road that two independent sources identify as State Highway 1 with ~1 m
mean separation over 100 % of its length renders as **"Name not recorded"**.
This is not a licensing question and not a coverage question — the data is
present, correct, licensed and already in the database. Phase 4 fixes the
display side of this; the matcher fix itself is called out as PR 2 work in the
pull request, because re-running enrichment is out of scope here.

## 5. What this case is evidence for

1. **Closure scope must default to the selected segment.** The headline number,
   the status and the stranded set were all wrong for the question asked, purely
   because of scope. (Phase 2)
2. **Isolation must be exact and undirected.** V1 asserts `exact: true` on a
   directed bounded walk and picks the smaller side by `min()`. Here that
   happened to land on the right set; it is not guaranteed to, and the concept
   is wrong regardless. (Phase 3)
3. **"Road cut off" must be gated on a real bridge test.** Link 375057 is not a
   bridge. No single-segment closure of it can cut anything off. (Phase 3)
4. **The map must never render a bare "No name"** for a road that the database
   can describe as State Highway 1, or failing that as a state-highway section
   near Tokoroa managed by NZTA. (Phase 4)
