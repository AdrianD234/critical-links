# At-grade crossings: the junctions AMDS does not split

`split_at_junctions()` applied a binary rule. It split where one link's
*endpoint* landed on another link's interior, and never where two links'
*interiors* crossed, on the inference that "neither road ends here, therefore
it is grade separated". That inference is false for a flat rural grid, where a
through road crosses another through road at grade and neither terminates.

Everything below was measured on `amds-national-2026-07-28-5b359d84`. The
national snapshot was never written to.

---

## 1. The Greendale counterfactual

The exact request the browser makes on a click: `scope=segment`, no direction,
`metric=distance`, `vehicle=car`, on link 234872, 675.3 m of Greendale Road
near Darfield, Canterbury.

| | replacement | penalty | ratio | arcs |
| --- | --- | --- | --- | --- |
| national snapshot, unmodified | **7,944.4 m** | 7,269.1 m | 11.76x | 17 |
| the same request, crossings noded | **4,915.5 m** | 4,240.3 m | 7.28x | 15 |

**3,028.9 m shorter — a 38.1% reduction, for closing 675 m of road.**

The original route ran Greendale -> Wards -> Clintons -> Bangor -> McLaughlins
-> Greendale: a perimeter circuit. The corrected route runs Greendale ->
Cressy Place -> McLaughlins -> **[the crossing]** -> Clintons -> Wards ->
Greendale.

### Which crossing, exactly

This is the part that must not be blurred.

24 interior crossings lie within 5 km of the closure. Noding all 24 gives
4,915.5 m. Noding them one at a time isolates the cause:

| what was noded | replacement |
| --- | --- |
| **Clintons Road x McLaughlins Road** (links 232709 x 234053) alone | **4,915.5 m** |
| Clintons Road x Greendale Road (links 232708 x 234875) alone | 7,944.4 m — *unchanged* |
| both | 4,915.5 m |

**The crossing named in the original report is not the crossing that changes
this route.** Clintons x Greendale is a genuine missing junction — 0.000 m
separation, disjoint node sets — and connecting it alone moves nothing. The
causal junction is Clintons x McLaughlins, 2.8 km away and not visible in the
screenshot that started this.

Evidence for the causal crossing:

- separation **0.000000 m**, intersection `POINT(1525968.995 5182907.627)`
- node sets `{218879, 216894}` and `{214155, 215729}` — fully disjoint
- different AMDS source features
- **0 near misses within 25 m**, which is why `topologyConfidence` cannot see
  it: that system keys off near misses, and this is not a near miss

### Nothing else explains the original route

- **Turn restrictions**: 43 nationally, exactly 1 applies to a car. It names
  links 179595 and 210580. **0 of them lie within 20 km of this closure.**
  `turnCheck` on the original result: `ok=true, checked=true,
  applicableRestrictions=1, violations=[]`.
- **Vehicle mode and one-way**: all 15 links on the shortcut are two-way and
  car, heavy and emergency eligible. Re-running the national baseline under
  the least restrictive profile (`vehicle=emergency`) returns the identical
  7,944.4 m.

### How it was run, and rolled back

`db.get_pool()` hands out **autocommit** connections, so an uncommitted
transaction is invisible to every query the engine makes on another one. A
transaction cannot do this job. `nzcl/whatif.py` copies the snapshot under a
new id, nodes the chosen crossings on the copy, and drops it again; the drop
cascades from the `network_snapshots` row. Verified after rollback: 0 links in
the copy, **375,696 links still in the national snapshot**.

Artefacts: `greendale-counterfactual.json`, `greendale-minimal.json`.

---

## 2. Three numbers that are not the same number

A previous investigation reported "12,942 interior-interior crossings
nationally". That figure conflates three different things.

| | count |
| --- | --- |
| crossing **pairs** of graph links | **13,084** |
| ...of which the intersection is not point-like (collinear overlap: duplicate geometry, not a crossing) | 28 |
| individual intersection **points** | **18,675** |
| distinct **places**, clustered at 25 m | **9,629** |

A pair is not a place. One physical intersection of two divided carriageways
produces four pairs and four points. 692 pairs cross at more than one point.

### The clustering radius is an audit convention

It groups a review display and gates the mixed-place rule. **It never drives
noding**: every cut is made at its own exact crossing point, for its own
specific source pair (`test_cuts_land_on_the_exact_crossing_point`).

| radius | places | mixed |
| --- | --- | --- |
| 5 m | 12,725 | 48 (0.4%) |
| 10 m | 12,370 | 106 (0.9%) |
| **25 m** | **9,629** | **226 (2.3%)** |
| 50 m | 8,912 | 270 (3.0%) |

25 m was chosen because it is wide enough to hold one intersection of two
divided carriageways — four points over 20-30 m — and narrower than an urban
block.

Mixedness is **monotone** in the radius: merging clusters can only add
disagreement, never remove it. So a place mixed at 5 m is necessarily inside a
place mixed at 25 m, and clustering wider withdraws a **superset**. "Mixed at
5 m but clean at 25 m" cannot happen. Pinned by
`test_mixedness_is_monotone_in_the_radius`.

---

## 3. What evidence exists, and what does not

| source | usable? | why |
| --- | --- | --- |
| **AMDS z-values** | **No** | Layer 1 has `hasZ=true` and the ingest simply never asked for it. Asking gets real elevations — and they are a **LiDAR terrain drape**. Known motorway interchange crossings, grade separated by construction, come back **0.00-0.09 m** apart. A hillside digitising artefact gives 10 m. Layer 4's own metadata confirms it: `zAccuracyMethodUsed` is LiDAR on 611,884 of 621,679 rows and **Surveyed on none**. |
| `modelAssetType` | Partly | No structure value exists in the domain. `Connector` (6) usefully marks ramps. |
| **LINZ Topo50 bridge + tunnel centrelines** | **Yes — the only authoritative structure evidence for NZ** | 18,007 loaded (`ext_structures`). Requires **alignment**: of 1,056 within 15 m of a crossing, **420 cross both roads** — river bridges beside a junction. Recovers ~45% of grade-separation candidates on the SH1 Southern Motorway, so its absence proves nothing. |
| AMDS height restrictions | Demoted | 174 links nationally. AMDS publishes `startMeasure`/`endMeasure` with each restriction and the ingest keeps **neither**, so a limit cannot be placed on the link it belongs to. |
| a node within 1 m | Yes | A third link *ends* at the crossing: the source already calls that spot a junction. |
| **shared digitised vertex** | **Measured and rejected** | Only 15.4% of crossings have one on both lines, and the proved Darfield case has a vertex on **neither** (1.51 m and 0.10 m away). |
| state highway status | **Never used as a classifier** | Many SH crossings are ordinary at-grade intersections; some local roads pass over others. Carried for prioritisation only. Pinned by `test_state_highway_alone_never_decides_anything`. |

---

## 4. The three dispositions

    AT_GRADE          create a shared node        — the ONLY one that changes the graph
    GRADE_SEPARATED   leave disconnected
    UNRESOLVED        leave disconnected, FLAG it, and lower the confidence of
                      any route a different answer here would change

GRADE_SEPARATED and UNRESOLVED are identical in the graph. The whole difference
is how confident the answer claims to be — which is why the bar for
GRADE_SEPARATED is high and anything below it is demoted rather than defended.

`ORDINARY_CROSSROADS` is recorded at **MEDIUM** confidence and its detail
begins "PROBABLY a junction". Its rule is the absence of contrary evidence,
which is not evidence for the conclusion. `JUNCTION_WITNESS` is HIGH: a third
road ends there.

---

## 5. Mixed places: the hazard, and the refusal

A graph node promises that every arc arriving may leave by every other arc. It
cannot say "A may turn into B here, but C passes overhead".

At an interchange that distinction is the whole point. Noding the at-grade pair
of a mixed place can hand a grade-separated third road the same movements —
**reintroducing the exact defect the never-node rule existed to prevent**, in a
form that looks fine.

So where the pairs at one place disagree, **nothing there is noded, under any
policy including POSSIBLE**. The crossings stay recorded as UNRESOLVED with
reason `MIXED_PLACE`, and what the evidence said beforehand is kept alongside.

This is the conservative one of the three available options. Level-specific
nodes, and expressing permitted movements through the edge-expanded transition
graph, are better answers and are not attempted here.

`topology.audit_no_invented_movements()` **asserts** the safety property rather
than reasoning about it: for every crossing not noded, the two source features
must share no graph node there. The ingest runs it and refuses to load on a
violation. A test deliberately breaks a graph to show the check can fail.

---

## 6. Blinded validation — and why the first review did not count

The first review pack printed `AT_GRADE - ORDINARY_CROSSROADS` on every card
**before** the reviewer looked at the imagery. It reported 100% precision. That
number should not be used. The pack, its verdicts and its screenshots are kept
in `review-verdicts.json` and `docs/screenshots/at-grade-crossings/` as a
record of what was done, labelled as **single-reviewer, model-assisted, not
independent ground truth**.

The blinded pack shows imagery, both centrelines, the crossing point, ids and
names, and nothing else. The answer key is a separate file, joined only after
every verdict was written. Order randomised. 208 cards — 160 AT_GRADE plus 48
decoys, so the pack is not "here are 160 crossings, all AT_GRADE" — spread over
1,286 km of New Zealand.

### Result, reported as the coordinator asked

| | n | confirmed | contradicted | unreviewable |
| --- | --- | --- | --- | --- |
| **AT_GRADE** | **160** | **143** | **14** | **3** |
| AT_GRADE / ORDINARY_CROSSROADS | 145 | 130 | 12 | 3 |
| AT_GRADE / JUNCTION_WITNESS | 15 | 13 | 2 | 0 |

**Lower 95% confidence bound on AT_GRADE precision: 83.6%.** Not "100%".

### The promotion gate is NOT met

> zero confirmed grade-separated false positives — **failed: 1**
> (C062, SH8 on a truss bridge over a river, the other road on the bank)

### What the blinding found that the unblinded pass could not

| stratum | confirmed | contradicted |
| --- | --- | --- |
| **angle 20-30 deg** | **8** | **8** |
| angle 30-60 deg | 17 | 1 |
| angle 60-80 deg | 18 | 0 |
| angle 80-90 deg | 27 | 1 |
| structure 15-60 m away | 13 | 2 |
| rural / unnamed / urban / SH | 49 | 1 |

Two systematic failures, both fixed, **both derived from this sample**:

1. **The tangential threshold was in the wrong place.** The 20-30 degree band
   was a coin toss. Raised to **30 degrees**.
2. **Eleven of the seventeen misses were one road recorded twice.** Paulin Road
   crossing Paulin Road; Wallson Crescent crossing Wallson Crescent. Different
   AMDS source features, so `SAME_SOURCE_FEATURE` never fired, and several at a
   healthy angle, so the tangential veto never fired either. Neither an id nor
   an angle catches this. `DUPLICATE_GEOMETRY` now checks whether the two
   centrelines stay within 8 m of each other for 60 m either side.

The single grade-separated false positive was a mapped Topo50 structure just
outside the 15 m match radius; widened to **25 m**, alignment still required.

**Because these fixes were derived from this sample, any figure re-scored
against it is optimistic and does not clear the gate.** A fresh blinded sample,
drawn with a different seed, is required before the classifier may rebuild the
canonical national graph.

---

## 7. Status against the gates

| gate | status |
| --- | --- |
| 1. mixed places — hard stop | **met**: withdrawn entirely under all policies, invariant asserted in code and in the ingest, fixture added |
| 2. blind the review | **met**: 208-card blinded pack, answer key separated, randomised; the old review relabelled |
| 3. expand the AT_GRADE sample | **met in size** (160 reviewed, stratified as specified); **promotion threshold NOT met** — 1 confirmed false positive, lower bound 83.6% |
| 4. Greendale attribution | **met**: Clintons x McLaughlins is named as the cause throughout; Clintons x Greendale is recorded as real but not causal |
| 5. classification language | **met**: `ORDINARY_CROSSROADS` is MEDIUM and says "PROBABLY" |
| 6. promotion / rebuild | **BLOCKED by gate 3.** No new national snapshot has been built. |
| 7. clustering radius | **met**: 5/10/25/50 m reported, monotonicity proved and tested, noding shown independent of it |
| 8. G_possible provenance | **partial**: `crossing_policy='possible'` exists and is tested; per-route provenance of which unresolved crossings a route uses is **not yet implemented** |
| 9. double-review | **not done**: the recode pack generator exists (`blind_review.py recode`); the second pass has not been run |
| 10. repo hygiene | **met**: 46,462 -> 7,280 insertions; the 10.9 MB derived record replaced by a sha256 manifest, a deterministic 250-row sample and a regeneration script |

---

## 8. Reproducing any of this

The full national crossing record is derived and not committed. Its sha256,
row count and exact command are in `classified-manifest.json`;
`classified-sample.jsonl` holds a deterministic 250-row extract. Regeneration
steps are in `scratch/README.md`.

The review packs are not committed either: their tile URLs carry a LINZ
Basemaps API key. Rebuild them with `scratch/review_sheets.py` and
`scratch/blind_review.py build`.
