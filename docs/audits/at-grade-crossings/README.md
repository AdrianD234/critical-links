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
| 5 m | 12,725 | 51 (0.4%) |
| 10 m | 12,370 | 108 (0.9%) |
| **25 m** | **9,629** | **228 (2.4%)** |
| 50 m | 8,912 | 280 (3.1%) |

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

## 5a. The record on disk was not the classifier in `src/`

Worth stating plainly, because it silently invalidated the numbers this file
used to quote. `scratch/classify_national.py` says it "runs the SAME function
the ingest will run". It did not. `crossings.build_context()`, which the
ingest uses, computes `duplicate_corridor` from the two centrelines; the
script assembled its context by hand and never set that field, so it defaulted
to `False`. **`DUPLICATE_GEOMETRY` — the larger of the two fixes the blinded
review produced — could not fire on the national record at all**, and the
record still carried the pre-fix classification while `crossings.py` had moved
on.

Fixed, and regenerated. What moved:

| | before | after |
| --- | --- | --- |
| `DUPLICATE_GEOMETRY` | 0 (could not fire) | **712** |
| `TANGENTIAL` | 980 | 409 |
| `STRUCTURE_MAPPED` | 575 | 584 |
| AT_GRADE pairs | 5,631 | **5,549** |
| AT_GRADE places | 5,290 | **5,215** |
| mixed places | 226 | 228 |

`TANGENTIAL` falls even though its threshold *rose* from 20 to 30 degrees,
because `DUPLICATE_GEOMETRY` runs ahead of the angle test and absorbs the
duplicates that used to land there. Both Darfield crossings still classify
AT_GRADE, including the causal one, Clintons x McLaughlins.

The manifest also recorded `"snapshot": null` — a provenance field that
recorded nothing, because it read a key the rows do not carry. It now reads
the snapshot id from the summary beside it.

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

### These 208 cards are DEVELOPMENT DATA

Stated once, plainly, because everything downstream depends on it. The pack in
`blind-verdicts.json` and its answer key are **development / training data, not
validation**. Both surviving classifier rules were derived from them. They are
never to be re-scored, and no figure taken from them may be quoted as
precision. The retracted "100% precision" from the earlier unblinded pack is
not to be reused either.

---

## 6a. The holdout — the sample that does count

`scratch/holdout_review.py`, seed `at-grade-holdout-2026-08-18`, 248 cards,
drawn from the regenerated national record.

### What makes it independent

| removed before the draw | n |
| --- | --- |
| link pairs in the 208-card development pack | 208 |
| link pairs in the earlier unblinded pack | (union: 288 total) |
| further crossings **within 50 m** of any of those | 152 |
| **eligible pool remaining** | **12,616 of 13,056** |

The 50 m rule is the one doing the work. Dropping pairs alone would still admit
a neighbouring pair at the **same physical intersection** — one crossing of two
divided carriageways produces four pairs — which is the same case wearing a
different id. The project's own definition of one place is 25 m; 50 m is used
so a holdout card cannot even be a near neighbour of a development card.

### The strata, re-aimed

A holdout that retests the *old* thresholds measures a classifier that no
longer exists. So the cells sit against the rules as they now stand:
**30-40 degrees** immediately above the new tangential threshold;
**25-70 m** immediately outside the widened structure radius; roads that
**share a name but which `DUPLICATE_GEOMETRY` did not catch**;
`DUPLICATE_GEOMETRY` itself as a decoy, in case it is withdrawing real
junctions; unsealed access roads as the source's best proxy for forestry,
industrial and private tracks; plus urban/rural, state highway, unnamed, and
imagery age. A per-authority cap stops the largest councils supplying every
cell: the draw spans **50 RCAs and 1,269 km**.

Imagery age came from the LINZ Basemaps attribution layer. An "imagery
<= 2018" cell was written first and drew **zero** candidates — Basemaps serves
the current mosaic, not an archive, and the oldest survey over any crossing
nationally is 2019. The cell was retuned to a band that exists rather than left
designed-but-unfilled. Obscuration, which is what actually makes a card hard,
was handled by letting "unclear" stand as a verdict.

### Result

| | n | confirmed | contradicted | unreviewable |
| --- | --- | --- | --- | --- |
| **AT_GRADE** | **196** | **189** | **3** | **4** |
| GRADE_SEPARATED | 24 | 15 | 9 | 0 |
| UNRESOLVED | 28 | 28 | 0 | 0 |

> **Confirmed grade-separated false positives: 0.** The gate requires zero.
>
> **Lower 95% bound on AT_GRADE: 92.8%** counting unreviewable as failures,
> **95.5%** excluding them.

> ### THIS RESULT DOES NOT MEET THE GATE, and this file said it did
>
> The agreed gate was always TWO conditions — zero confirmed grade-separated
> false positives **and** a 95% lower bound of at least 97%.
> `holdout-result.json` encoded the first, recorded the other numbers beside
> it, and reported `met: true`. The counts above were right. The verdict drawn
> from them was not, and it was wrong in the direction that declares success.
>
> Re-scored by `nzcl.promotion.evaluate`, which is now code with a test per
> condition: **the gate is NOT MET.** One condition passes, two fail —
> 92.8% against 97%, and three not-a-junction false positives against a
> requirement of zero. See section 10.

UNRESOLVED accepts every verdict but "unclear" by construction — it makes no
claim about the ground — so its 100% is not a meaningful number and is printed
only for completeness.

**These figures are performance on a deliberately difficult, stratified
holdout. This is not a probability sample and is not an estimate or formal
lower bound on national precision.** The Wilson bound applies to this
holdout's reviewed cases, not to a nationally weighted population. No
population-weighted estimate is offered: the cells overlap and the draw is not
a clean probability sample, and inventing a number would be worse than saying
so.

> **Correction.** This paragraph used to call the figures a "conservative
> floor". That is withdrawn. A floor is a claim about the *population* — it
> says national precision is at least this much — and earning it needs a
> probability sample and population weights. These cells overlap, they were
> chosen for being hard, and no weights exist. Over-weighting hard cases makes
> a number *likely* to be pessimistic; it does not make it a bound. The
> difference matters, because "floor" is the word that would let a reader
> treat 92.8% as a guarantee about 375,696 links.

### The three AT_GRADE misses are all one defect, and it is not grade separation

| card | roads | angle | called |
| --- | --- | --- | --- |
| H001 | Council Place Loop x Council Place Loop | 31.6 | not a junction |
| H040 | Hansen Road x Hansen Road | 31.4 | not a junction |
| H192 | Ngaio Road x (unnamed) | 87.3 | not a junction |

All three are **one road recorded twice** that `DUPLICATE_GEOMETRY` did not
catch (`duplicateCorridor` false on all three). Two graze at 31.4 degrees —
just past the new 30-degree threshold, so the fix moved the line and these sit
immediately beyond it. The third crosses itself at 87 degrees, where neither an
angle test nor a corridor test can see it.

Noding these joins a road to itself and fabricates a turn. That is a real
defect. It is a **different and smaller** one than inventing a motorway
turn, which is what the promotion gate is about, and it is why the gate is
met while the classifier is still not perfect.

### The finding that matters most is on the other side

`MOTORWAY_CARRIAGEWAY` confirmed **2 of 8**.

| rule | confirmed |
| --- | --- |
| STRUCTURE_MAPPED | 9 / 10 |
| CONNECTOR | 2 / 3 |
| RAMP | 2 / 3 |
| **MOTORWAY_CARRIAGEWAY** | **2 / 8** |

Every miss is an ordinary at-grade urban intersection where a state highway is
coded one-way: one-way pairs, divided arterials, roundabout approaches —
Dee Street West, Cambridge Road westbound, SH3 Interchange 401 Roundabout,
Ruahine Street, Clyde Street East, SH20 x Oakley Avenue. This file already
called it "the weakest surviving GRADE_SEPARATED rule" on a 4-of-5 check. At
n=8 it is simply wrong.

Its failures **cost connectivity rather than inventing movements**: a real
junction is left severed, which forces exactly the perimeter detour this whole
branch exists to remove. So it does not block AT_GRADE noding — but the
classifier is under-connecting urban state highways, and that is not a small
thing to leave unsaid.

**How big is it.** `MOTORWAY_CARRIAGEWAY` decides **728 pairs nationally**. At
2 of 8 confirmed the point estimate is ~546 real junctions wrongly severed,
and the 95% interval on 2/8 is wide — [7.1%, 59.1%] confirmed — which puts the
range at roughly **300 to 680**. n=8 is small and that interval is honest about
it. Even its optimistic end is the same order as the Greendale defect this
branch was opened to fix. It is measured here and deliberately not acted on;
see below.

### Nothing was tuned against this sample

Both findings above are **recorded, not acted on**. Rewriting
`MOTORWAY_CARRIAGEWAY` or widening `DUPLICATE_GEOMETRY` against this pack
would burn it exactly as the first sample was burned, and there would then be
no clean measurement left at all. They are work for a third pack.

---

## 6b. Double-review (gate 9)

40 of the holdout cases were re-rendered with fresh codes from a different
seed, reshuffled, and stripped of link ids and road names, then coded a second
time.

| | |
| --- | --- |
| raw agreement | **38 / 40 = 95.0%** [83.5-98.6] |
| expected by chance | 67.4% |
| **Cohen's kappa** | **0.847** |

Kappa is reported because raw agreement flatters any task with one dominant
answer: a coder who always said "at grade" would score about 90% here while
demonstrating nothing.

Two limitations, both real:

1. **It is the same reviewer twice.** This is intra-rater, test-retest
   consistency. It is not inter-rater agreement, and it is a weaker claim.
2. **Pass 2 was taken after pass 1 was scored**, so the reviewer already knew
   `MOTORWAY_CARRIAGEWAY` over-calls grade separation. That can only push pass
   2 towards repeating pass 1, so **0.847 is an upper bound**. A repeat should
   recode before scoring.

Both disagreements were adjudicated on the imagery at zoom 21, and both landed
on pass 1:

- **H001** (car park): both lines follow the same aisle before diverging by a
  couple of metres — one aisle recorded twice. Stays `not_a_junction`, so it
  **stays a contradiction against the classifier**. The other reading would
  have improved the headline figure.
- **H107** (SH1): the motorway is on a bridge — parapet along both sides of the
  deck, the second road visible passing underneath with a vehicle on it. Stays
  `grade_separated`; the classifier is right. Scoring-neutral either way,
  because GRADE_SEPARATED accepts both answers.

---

## 6c. Provenance for possible-graph routes (gate 8)

The POSSIBLE graph has been able to answer "would this change if the crossings
we cannot resolve were junctions?" since the policy landed. The answer was a
single bit, and that flattens the distinction which decides whether a finding
can be checked at all: **a result hinging on ONE unresolved junction is a claim
one aerial photograph settles; a result needing four stacked assumptions is a
chain nobody has measured.** They must not read identically.

`nzcl/provenance.py` is the first reader of the `crossings` table, which until
now was written and never queried. Per route it reports:

    robustness   ROBUST | ONE_UNRESOLVED_CROSSING | MULTIPLE_UNRESOLVED_CROSSINGS
    crossings    each one's id, source pair, coordinates in 2193 AND 4326,
                 reason, confidence, angle, place, and the two links the route
                 used to enter and leave it
    changedByOneCrossing         is any single one of them decisive?
    requiresMultipleAssumptions  no single one is, yet the route still depends
                                 on unresolved topology

Two decisions worth stating, because the obvious implementations are wrong:

- **"Relied on" is a join on the route's ORDER, not a proximity buffer.** A
  crossing counts only where the route actually turns from one source feature
  onto the other at that point. A route running *straight through* a noded
  crossroads sits at zero distance from it and assumed nothing - without the
  node those two links would still be one link and the route would be
  identical. A buffer test calls that a dependency, and is wrong every time.
- **Decisiveness compares the DISTANCE, not the link sequence.** Removing a
  node always changes the link sequence, so a sequence comparison would call
  every crossing decisive and mean nothing. Where no re-routing hook is
  supplied the method is reported as `UNTESTED_COUNT_ONLY` and decisiveness is
  `null` - **not `false`**, because "not measured" and "measured, no" are
  different answers.

### A speculative route can never be drawn as the teal replacement path

Teal is the colour of the answer this system publishes. A route that exists
only because of an unresolved assumption is not that, and drawing it in that
colour would present an unverified claim as a measured result.

The rule is enforced **structurally, not by convention**. `routeTarget()` is
the single place the choice is made, and it selects a *source and a layer*
rather than a colour: the speculative route is fed from its own source, which
the teal layer never reads. `NetworkMap.tsx` no longer names either route
layer, either source, or `palette.route` at all.

Proved rather than asserted: `tests/unit/speculative-route.test.ts` includes a
test that reads `NetworkMap.tsx` and fails if those names reappear. Breaking
`routeTarget()` so it always returns the canonical target **fails 4 tests** -
checked by doing it, because a guard that cannot fail is not a guard.

### Not done, deliberately

`impactv2.py` does not yet pass the lookup on the shipping V2 path. The seam
exists and is one line. It was left off because enabling it adds a query to
every canonical request that nobody asked for, on a branch whose classifier is
frozen under a scored holdout. No inspector panel renders the provenance block
yet either - only the `RELIES_ON_UNRESOLVED_CROSSING` quality flag and the
sentence explaining what it means.

---

## 7. Status against the gates

| gate | status |
| --- | --- |
| 1. mixed places — hard stop | **met**: withdrawn entirely under all policies, invariant asserted in code and in the ingest, fixture added |
| 2. blind the review | **met**: 208-card blinded pack, answer key separated, randomised; the old review relabelled |
| 3. expand the AT_GRADE sample | **NOT MET.** 196 AT_GRADE were reviewed on a fresh holdout, and the result does not clear the agreed gate: lower bound 92.8% against 97%, and three not-a-junction false positives against a requirement of zero. It DOES meet the condition it was reported against — 0 confirmed grade-separated false positives. The 248-card pack is now development data too. See section 10 |
| 4. Greendale attribution | **met**: Clintons x McLaughlins is named as the cause throughout; Clintons x Greendale is recorded as real but not causal. Both still node under the revised classifier |
| 5. classification language | **met**: `ORDINARY_CROSSROADS` is MEDIUM and says "PROBABLY" |
| 6. promotion / rebuild | **not done, and it must not be done.** Gate 3 does not pass, so no `processingVersion` 2.1.0 snapshot may be built. See section 10 |
| 7. clustering radius | **met**: 5/10/25/50 m reported, monotonicity proved and tested, noding shown independent of it |
| 8. G_possible provenance | **met, and the decisiveness defect in it is fixed.** `nzcl/provenance.py` names which unresolved crossings a route relied on and where. Decisiveness is now established by re-routing at every count instead of being inferred from one; the shipping path supplies the hook; a possible-graph route is drawn on its own dashed layer and cannot reach the teal one. See sections 6c and 10 |
| 9. double-review | **NOT MET as an independent review.** 40 cases recoded, 38/40 agreement, kappa 0.847, both disagreements adjudicated — but it is the SAME reviewer, after scoring, so it is intra-rater and an upper bound. An independent review by a fresh isolated reviewer has not been performed. See section 6b |
| 10. repo hygiene | **met**: both derived records are a sha256 manifest plus a deterministic 250-row sample; the tracked screenshots went from 21.6 MB to 456 kB. See section 8 |
| 11. the gate is code | **met**: `nzcl/promotion.py`, with a test per condition and one that proves each condition can veto alone |
| 12. the record IS the classifier | **NOT MET before this change, and it is why gate 3 needs re-reading.** The national record was built in SQL over graph links; the classifier runs over source features. Rebuilt by `classify_national_v2.py`. See section 10 |

---

## 8. Reproducing any of this, and what is deliberately not kept

The full national crossing record is derived and not committed. Its sha256,
row count, snapshot id and exact command are in `classified-manifest.json`;
`classified-sample.jsonl` holds a deterministic 250-row extract. Regeneration
steps are in `scratch/README.md`.

**No review pack is committed**: every tile URL in one carries a LINZ Basemaps
API key. Rebuild them from the committed generators —

    python ../scratch/review_sheets.py  <outdir>            # pack 1, unblinded
    python ../scratch/blind_review.py   build  <outdir>     # pack 2, development
    python ../scratch/holdout_review.py build  <outdir>     # pack 3, the holdout
    python ../scratch/holdout_review.py score  <outdir> ../docs/audits/at-grade-crossings/holdout-verdicts.json
    python ../scratch/holdout_review.py recode <outdir> 40
    python ../scratch/holdout_review.py agree  <outdir> ...holdout-verdicts.json ...holdout-verdicts-recode.json
    python ../scratch/holdout_review.py zoom   <outdir> H001,H107 21 820

`scratch/review/`, `scratch/blind/` and `scratch/holdout/` are all gitignored
for that reason. The **verdicts** are committed — `review-verdicts.json`,
`blind-verdicts.json`, `holdout-verdicts.json`,
`holdout-verdicts-recode.json`, `holdout-result.json`,
`holdout-agreement.json` — because those are the evidence; the key-bearing
pages are not.

### The screenshot decision

`docs/screenshots/at-grade-crossings/` held **nine PNGs, 21.6 MB**. It now
holds **three JPEGs, 456 kB**, and this was a decision rather than an accident
of capture:

- Every pack is **deterministic** — same seed, same script, same cards — so
  the raster was never the evidence. The verdicts and the generator are.
  Carrying 21.6 MB of regenerable screenshots is the same mistake as carrying
  the 10.9 MB derived record, which this audit had already fixed once.
- **One page of the superseded unblinded pack** is kept, so the record that it
  existed and what its cards looked like survives the retraction of its
  headline number.
- **Two holdout pages** are kept, so a reader can see what a blinded card
  actually shows without regenerating anything.
- They are downscaled to 62% and saved as quality-74 progressive JPEG. They are
  illustrative samples, not the artefact; the artefact is reproducible from the
  commands above.

### Credential hygiene

The earlier generators embedded `VITE_LINZ_API_KEY` in every tile URL.
Generated pages were committed twice and purged twice by force-push. **A
force-push does not remove a pushed blob from the remote** — it only makes it
unreachable, and the object stays retrievable by its sha until the host
garbage-collects it. So the old key must be treated as disclosed.

**The fix is not "be more careful".** `holdout_review.py` now emits tile URLs
with **no query string at all** and injects the key at load time from
`linz-key.js`, a sidecar written into the pack directory and gitignored twice
over — by the repo root and by a `.gitignore` the generator drops beside it.
Commit a generated page by accident now and it discloses nothing. Verified:
the regenerated pack's HTML contains zero occurrences of the key, and renders
byte-for-byte identically to the pages the holdout was actually reviewed on.

**`scripts/check-no-secrets.sh`**, wired into CI as a job that runs alone and
first, is the standing control. Three guards:

1. **shape** — anything matching a LINZ key (`c01` + 24 base32 chars) in any
   tracked file, in any format, URL or not;
2. **context** — a `basemaps.linz.govt.nz` tile URL with a literal `api=`
   value, which catches a key format not seen yet;
3. **structure** — the generated pack directories being tracked at all.

It reports file and line and **redacts the value**, so the guard cannot itself
become the leak. It was tested against planted fakes: guards 1 and 2 each fire
independently, and the tree is clean at 441 tracked files.

State of the branch, checked rather than assumed: every blob reachable from
any ref was scanned. **Zero reachable hits.** 33 blobs in the local object
store still contain the key and all 33 are **dangling** — unreachable from any
branch, tag or remote-tracking ref, which is exactly what the two purged
commits leave behind on the machine that made them.

**Rotation is deferred, not done.** The owner has accepted the residual risk:
this is a free LINZ Standard Basemaps key for public tiles, rate-limited, with
no access to private data, the machine, GitHub or any billable account, and
Standard keys are replaced within 90 days regardless. The realistic downside is
quota consumption or LINZ disabling the key. **Rotation is mandatory before
public deployment or before this repository is made public**, and unexplained
429 responses should be reported immediately.

The branch history has **not** been rewritten again. Two force-pushes are
enough, and nothing here justified a third.

---

## 9. May a 2.1.0 rebuild proceed?

**No. Superseded — see section 10 for the current answer and the reasoning.**

The table and recommendation below are kept as the record of what was said
after the second holdout, because two of its statements were wrong and the
correction is more legible beside them than in place of them.

| | |
| --- | --- |
| credential item closed | **yes** — pages carry no key, CI guards it, rotation deferred on accepted risk and recorded as mandatory before public deployment |
| first review labelled development data | **yes** — section 6, and it is not re-scored |
| fresh holdout passes | ~~yes on the hard condition~~ — **NO.** The gate is both conditions, and 92.8% does not clear 97% |
| double-review complete | ~~yes, with its limits stated~~ — **NOT AS AN INDEPENDENT REVIEW.** Intra-rater is not a second reviewer |
| mixed places safely handled | **yes** — withdrawn under every policy, invariant asserted in code and ingest |
| possible-route provenance | see gate 8 |
| mandatory tests pass | see the CI run recorded on the PR |

### The recommendation, as it was written then

> **A 2.1.0 rebuild may proceed on the AT_GRADE noding, and should not also
> adopt the current GRADE_SEPARATED rules without a further look.**

The reasoning, separated so it can be argued with — and the first bullet is the
one that was wrong:

- ~~**The gate's hard condition is met**~~ — the gate is not one condition. It
  is zero grade-separated false positives AND a 97% lower bound, and it was
  encoded in `holdout-result.json` as the first alone.
- **The residual AT_GRADE error is bounded and is not the dangerous kind.**
  Three misses in 196, all of them one road recorded twice. Still true, and
  section 10 shows two of the three were artefacts of the record rather than
  errors of the classifier.
- **`MOTORWAY_CARRIAGEWAY` at 2 of 8 is the real open problem**, and it is an
  argument for *more* connectivity, not less: it severs real urban junctions.
  Acted on in section 10: it no longer asserts anything.
- **The measurement is a floor.** The pack over-weights the hard cells, so the
  true national AT_GRADE precision is very likely better than 92.8%. Still
  true, and it is not a reason to pass a gate the measurement does not clear.

### What must not happen next

Do not fix `MOTORWAY_CARRIAGEWAY` or widen `DUPLICATE_GEOMETRY` against this
holdout and then re-score it. That is precisely how the 208-card pack stopped
being evidence. A third pack, drawn independently of both, is the price of
changing the classifier again.

---

## 10. What the second holdout actually found, once the gate was written down

Four things changed after the 248-card holdout was scored. Every one of them
was driven by that pack, so **the 248 cards are development data now**, exactly
as the 208 were. They are not re-scored anywhere in this file.

### 10.1 The gate was one condition wearing the name of two

`holdout-result.json` recorded four numbers under `promotionGate` and tested
one of them:

```json
"promotionGate": {
  "confirmedGradeSeparatedFalsePositives": 0,
  "requirement": "must be 0",
  "met": true,
  "confirmed": 189, "contradicted": 3, "unreviewable": 4
}
```

The agreed gate was always both zero confirmed grade-separated false positives
**and** a 95% lower bound of at least 97%. The counts were right. The verdict
drawn from them was not, and the direction matters: it declared success.

The gate is now `nzcl/promotion.py` — a function that takes counts and returns
a verdict per condition — with `tests/test_promotion.py` pinning each one, and
the test that matters most proving **each condition can veto alone**. Four
conditions:

| condition | requirement | the 248-card holdout |
| --- | --- | --- |
| zero confirmed grade-separated false positives | must be 0 | **0 — passes** |
| zero confirmed not-a-junction false positives | must be 0 | **3 — fails** |
| 95% lower bound on AT_GRADE precision | >= 97% | **92.8% — fails** |
| unreviewable counted as failures | method | applied |

**Gate not met.** It meets the condition it was reported against and fails the
other two.

### 10.2 The record on disk was still not the classifier in `src/`

Section 5a caught this once: `classify_national.py` never set
`duplicate_corridor`, so `DUPLICATE_GEOMETRY` could not fire on the national
record. The same defect was still present in a larger form, and it is the
reason the three AT_GRADE contradictions need re-reading.

`classify_national.py` applied `crossings.classify()` to rows SQL had assembled
over the **links** table — the graph *after* `split_at_junctions` cut it. The
classifier that ships runs *inside* `split_at_junctions`, over AMDS **source
features**, before any graph exists. Three differences follow:

| | the record | the classifier |
| --- | --- | --- |
| rows | one per crossing **pair** (`ST_PointOnSurface` of the multipoint) | one per crossing **point** |
| crossing angle | over ±0.02 of each line's **length** | over a fixed **10 m** window |
| geometry | a graph **link** — a piece of a feature | the whole **feature** |

±0.02 of a 12.7 m link is ±25 cm, which measures the noise between two
digitised vertices; ±0.02 of a 3.6 km link is ±73 m, which measures the wrong
corner.

`classify_national_v2.py` rebuilds the record by calling the same functions
`split_at_junctions` calls, in the same order. Its one stated limitation: the
AMDS extract is not in this worktree, so source features are reassembled by
re-merging the link pieces of each `closure_group_id`. Re-merging is exact for
shape — splitting inserts vertices, it does not move the line — and **0 of
272,426 features failed to merge back into a single LineString**.

| | old record (link pairs) | new record (feature points) |
| --- | --- | --- |
| rows | 13,056 | **22,062** |
| AT_GRADE | 5,549 | **4,914** |
| GRADE_SEPARATED | 1,479 | **1,010** |
| UNRESOLVED | 6,028 | **16,138** |
| DUPLICATE_GEOMETRY | 712 | **9,830** |
| MIXED_PLACE | 228 | **946** |
| places at 25 m | 9,629 | 13,422 |

The two counts are not comparable line for line — one counts pairs of links,
the other counts points between features — and the file says so rather than
inviting the subtraction.

### 10.3 The three AT_GRADE contradictions, re-diagnosed on the shipping code

Replayed by `scratch/replay_cards.py`, which runs the real
`split_at_junctions` over the real neighbourhood of each card:

| card | roads | what the RECORD said | what the CLASSIFIER does |
| --- | --- | --- | --- |
| **H001** | Council Place Loop × Council Place Loop | AT_GRADE, 31.6° | **not noded.** Two crossings, 21.8° and 4.4°, both `TANGENTIAL`. The 31.6° is the SQL window measuring ±25 cm of a 12.7 m link |
| **H040** | Hansen Road × Hansen Road | AT_GRADE, 31.4° | **not noded.** Two crossings 11.1 m apart at 14.1° and 48.9°; they disagree, so the place is `MIXED_PLACE` and nothing there is noded |
| **H192** | Ngaio Road × unnamed | AT_GRADE, 87.3° | **was a real false node.** Fixed — see below |

So one of the three was a genuine defect in the classifier. The other two were
defects in the measurement, and they were counted against the classifier. That
correction moves the headline in the flattering direction, which is exactly why
it is stated with the replay script beside it rather than asserted.

**H192, the real one.** Near Kimbolton, source feature `61c2fcad` is 14.7 m of
a 1,959 m chain that runs **6.8 to 9.8 m** from feature `7d966e5b` for the
whole of its length. A constant offset over 2 km is one road recorded twice.
The two records swap sides once, and that swap is the 87° "crossing" that got
noded. `is_duplicate_corridor` could not see it: it needs 60 m of run either
side, a 14.7 m feature has none in any direction, and every direction being
skipped returned `False`, which reads as "two different roads".

The fix is **not** a shorter run. At 30° — the tangential threshold, so the
shallowest crossing still callable AT_GRADE — two genuinely crossing roads stay
within 8 m of each other for ±16 m, so any run short enough to judge a 15 m
feature would withdraw real junctions wholesale. The fix is
`crossings.corridor_polyline`: continue each feature through the features it
joins end to end, then ask the question about the ROAD. The walk follows
coincident endpoints only, at the same 50 mm tolerance the splitter treats as
one node; takes the straightest continuation at a fork rather than the one that
flatters the test; never revisits a feature; and does nothing at all when the
feature is already long enough.

**What it costs, measured rather than assumed** (`scratch/corridor_impact.py`
runs the national pipeline twice, with the walk on and off):

| | walk off | walk on | delta |
| --- | --- | --- | --- |
| AT_GRADE | 4,999 | **4,914** | **−85** |
| DUPLICATE_GEOMETRY | 9,160 | 9,830 | +670 |
| TANGENTIAL | 690 | 404 | −286 |

So the 9,830 national `DUPLICATE_GEOMETRY` total is almost entirely the level
change, not the new rule: 9,160 of them fire without the walk. The walk itself
withdraws **85 of 4,999 AT_GRADE crossings — 1.7%** — and that is a real cost,
because `DUPLICATE_GEOMETRY` is a never-node reason.

`scratch/corridor_spotcheck.py` measures how far past the 60 m threshold those
cases actually run. On a sampled dozen — the first six of each transition, so a
convenience sample and not a random one — the two corridors stay within 8 m of
each other for a **median of 165 m** and up to **2,000 m**, with one marginal
case at 65 m. A road recorded twice runs parallel for as long as the road does.
Nothing else looks like that. The third holdout draws a stratum from exactly
these cases, because a convenience sample is not evidence and the rule's own
false-positive risk should be measured by someone looking at photographs.

**Both Darfield crossings still node**, including the causal one, Clintons ×
McLaughlins.

### 10.4 GRADE_SEPARATED: the rules that were asserting more than they knew

`GRADE_SEPARATED` scored 15 of 24, and the failures were not spread evenly:

| rule | confirmed | decides nationally |
| --- | --- | --- |
| STRUCTURE_MAPPED | 9 / 10 | 1,010 |
| RAMP | 2 / 3 | 69 |
| CONNECTOR | 2 / 3 | 35 |
| **MOTORWAY_CARRIAGEWAY** | **2 / 8** | 452 |

One line separates the rule that survived from the ones that did not.
`STRUCTURE_MAPPED` and `NAMED_STRUCTURE` are **positive evidence that a
structure exists at this point**: an independent national mapping agency drew a
bridge or tunnel centreline here and it lines up with one of these two roads,
or the road carries the word "overbridge" in its own name. `RAMP`, `CONNECTOR`,
`MOTORWAY_CARRIAGEWAY` and the ramp/interchange words that used to sit inside
`NAMED_STRUCTURE` all argue instead that this is the KIND of road that is
usually grade separated. That is a prior about road classes, not evidence about
this crossing — the same absence-of-evidence reasoning `ORDINARY_CROSSROADS` is
recorded at MEDIUM for, stated with more confidence and pointing the other way.

All four are now **UNRESOLVED**.

- **Nothing about the canonical graph changes.** `GRADE_SEPARATED` and
  `UNRESOLVED` are both left disconnected. No severed crossing becomes
  connected, and `tests/test_crossings.py` pins that for each of the four.
- **What changes is the claim**, and that the crossing now enters the POSSIBLE
  sensitivity graph, so a route depending on it is reported as depending on it
  instead of the doubt being swallowed by a rule that is wrong three times in
  four.
- **The cost is a looser sensitivity bound around motorways.** Under
  `crossing_policy='possible'` these crossings are now noded, so the possible
  graph will contain some turns onto motorway carriageways that do not exist.
  That is the correct direction to be loose in for an instrument whose whole
  job is to ask "what if these were junctions?", and it is stated here because
  it is a real consequence and not a free one.
- **The fixture carrying "never noded under any policy" changed with it.** It
  used to be a one-way state-highway carriageway. A rule wrong three times in
  four is not what should stand between the engine and an invented motorway
  turn, so the invariant now rests on a mapped Topo50 structure.

`MOTORWAY_CARRIAGEWAY` was **not tuned** against the holdout. It was demoted,
which is the honest move available without a fresh sample: it stops asserting,
and it stops severing 452 crossings on an argument measured at 2 of 8.

### 10.5 The possible-graph decisiveness defect

`provenance.analyse` called a lone relied-on crossing decisive **by
construction**: the route used the only speculative node it had, so removing it
must change the route. It changes the LINKS. It does not change the ANSWER.
Where an equal-cost way round exists — and a rural grid is made of them — the
minimum-distance result is identical, the finding never hinged on that
crossing, and the payload said `changedByOneCrossing: true`. Somebody who drove
out and photographed that intersection would have learned nothing about the
number.

There is now one path at every count: take the crossing out, route again,
compare the distance. With no hook, nothing is claimed — `decisive`,
`changedByOneCrossing` and `requiresMultipleAssumptions` all serialise as
`null` and the method says `UNTESTED_COUNT_ONLY`, because `false` is its own
claim and exactly as unfounded as the `true` it replaces.
`DECISIVENESS_SINGLE` is gone rather than deprecated: it named an inference.

**And the hook is now reachable.** Section 6c recorded that `impactv2.py` did
not pass the lookup on the shipping V2 path and that the seam was "one line".
A decisiveness field nobody reaches is not a fixed defect. `impactv2.analyse`
now asks the DATA whether the snapshot is a possible graph — a noded UNRESOLVED
crossing exists under exactly one policy — and hands `replacement.compute` a
lookup carrying a re-route FACTORY, because the re-route needs each path's own
endpoints and the closure, which vary per movement while the snapshot does not.

`provenance.reroute_for` suppresses a crossing **without copying the
snapshot**, which `nzcl/whatif.py` does for the audit and which is far too
expensive to sit on a request. For shortest paths there is an exact identity:

```
d_unnoded(s,t) = min( d(s,t | side A's arcs at the node removed),
                      d(s,t | side B's arcs at the node removed) )
```

Splitting the node into two leaves a shortest path exactly three options,
because over positive weights it never visits a node twice: avoid the node, run
through on road A, or run through on road B. Turning from A onto B is the one
thing the split forbids and the one thing neither branch of the minimum allows.
So the whole re-route is two ordinary shortest-path calls with arcs excluded —
something the router already does for closures. It is checked against the graph
it stands in for: the same fixture built under the CONFIRMED policy, where the
crossing was never noded.

Both halves were proved by breaking them:

- reverting `analyse` to the count-based shortcut fails 3 tests, including one
  asserting the equal-cost case is not decisive;
- removing the re-route factory from `impactv2` fails the integration test with
  `UNTESTED_COUNT_ONLY` where `REROUTED_WITHOUT_EACH_CROSSING` is required.

No UI renders the provenance block yet, so nothing describes a crossing as
decisive today. The TypeScript type now says `boolean | null` for both
booleans, so a renderer cannot treat "not tested" as "no" without saying so.

### 10.6 A migration that could not reshape its own table

`sql/migrations/008_crossings.sql` creates `crossings` with `CREATE TABLE IF
NOT EXISTS`, and the table was created earlier in this branch's development
with a column called `plausible_junction` that was later replaced by
`safe_to_node` and `confidence`. Editing the `CREATE TABLE` upgraded a fresh
database and silently left every existing one behind — and the ingest COPY
names the new columns, so any machine that had run the earlier version would
have failed the next ingest on a missing column. It had never been hit only
because no snapshot with crossings has ever been built. Fixed with idempotent
`ALTER ... IF NOT EXISTS`, found by being the first thing to write that table.

---

## 11. May a 2.1.0 rebuild proceed? — the current answer

> **SUPERSEDED BY SECTION 13.** This section was written while the third
> holdout was declared and undrawn. The holdout has since been drawn, reviewed
> by four fresh isolated reviewers and scored: **the gate is NOT MET, at 86.1%
> against 97% with 32 not-a-junction false positives against a requirement of
> zero.** The answer below is unchanged — no rebuild — but the reason has
> moved from "there is no evidence yet" to "there is evidence, and it says
> no". Section 13 is the current record.

**No, and not yet for a good reason rather than a bad one.**

| | |
| --- | --- |
| promotion gate on the best available holdout | **NOT MET** — 92.8% against 97%, and 3 not-a-junction false positives against 0 |
| a holdout the classifier was not derived from | **does not exist.** All four fixes above were driven by the 248-card pack, so it is development data now |
| independent review | **has never been performed.** The double-review was the same reviewer, after scoring |
| classifier frozen | **yes**, as of the commit that declares the third holdout |
| third holdout | **declared, drawn and rendered — not reviewed, not scored.** 350 AT_GRADE cards plus 90 decoys, at most 4 failures. Declaration in `third-holdout-predeclaration.md`; what was actually drawn is section 12 |

The classifier is in better shape than it was: one real false node fixed, two
apparent ones shown to be measurement artefacts, four over-confident
GRADE_SEPARATED rules stopped from asserting, a decisiveness field that no
longer invents findings, and a national record that is finally produced by the
code that ships. None of that is evidence. Every one of those changes was
derived from the sample that would have to score them, and a figure re-scored
against it would be optimistic by construction — which is the trap this audit
has now walked into twice and is not walking into a third time.

**What must happen before a rebuild is recommended**

1. ~~Draw the third holdout at the declared size, from `classified-v2.jsonl`,
   excluding all three previous packs and everything within 50 m of them.~~
   **Done — section 12.** 350 AT_GRADE plus 90 decoys, rendered as
   self-contained images so a reviewer can be handed cards rather than a URL.
2. Have it reviewed by a **fresh isolated reviewer** — no previous transcript,
   no access to the classifier source, no prior verdicts, no score summary,
   anonymous randomised cards. If that cannot be done to that standard, the
   rebuild does not proceed and the blockage is reported. An honest statement
   that it was unavailable is required and is **not** a waiver. **Not yet
   done.**
3. Evaluate `nzcl.promotion.evaluate` on the result. If it fails, stop and
   report. Do not add cards, do not re-read the unclear ones, do not change a
   rule and re-score.

**What must not happen next**

Do not tune `corridor_polyline`, `DUPLICATE_GEOMETRY` or anything else against
the third pack. Three samples have now been burned this way. A fourth pack is
the price of changing the classifier again, and there is a limit to how many
independent samples this dataset can supply.

---

## 12. The third holdout, as drawn — 440 cards, no verdicts

**Status: DRAWN AND RENDERED. NOT REVIEWED. NOT SCORED.** No verdict exists
for any card in this pack, by anyone, and none may be recorded by whoever
drew it.

Built by `scratch/holdout3_review.py build`, seed `at-grade-holdout3-2026-08-18`,
from `classified-v2.jsonl` — the record produced by the code that ships, not
the SQL record the first two packs were drawn from.

### Why the cards are images and not a web page

The two previous packs are browser-rendered HTML that pulls LINZ tiles live.
That cannot meet the standard this holdout has to meet. A reviewer handed a
URL is holding a browser, a network connection and — one directory up — the
classifier, its source, its previous verdicts and its score summaries. So
every card is rendered once into a self-contained JPEG. The reviewer is given
a directory of images and nothing else: no repository, no network, no way to
recover what the classifier said.

Each card carries the aerial imagery at zoom 19 in a 512 px viewport, the two
road centrelines in two colours, a ring on the crossing point, a scale bar
with the ground distance marked, "north is up", and an anonymous id.

It carries **no** disposition, **no** deciding rule, **no** confidence, **no**
stratum, **no** link or source-feature ids — and **no road names**. The
previous packs printed the names. "Hansen Road × Hansen Road" gives away the
answer before the reviewer has looked at a pixel, and two of the three
AT_GRADE failures of the 248-card pack were exactly that case. Ids are
assigned after shuffling AT_GRADE cards and decoys together, so T001 is no
more likely to be anything than T440 is, and **which centreline is drawn in
which colour is randomised per card**, so colour cannot correlate with state
highway status or with anything else the classifier used.

The LINZ key is read from `.env` at render time and used only on the wire. A
JPEG has nowhere to put a URL, so unlike the HTML packs this artefact cannot
carry a key even by mistake. All 440 files were scanned for the key and for a
tile URL before anything was committed.

### What was drawn

| | |
| --- | --- |
| AT_GRADE cards | **350** — the predeclared n, unchanged |
| decoys, scored separately and not part of n | **90** |
| total cards | **440**, 50.0 MB of JPEG |
| eligible pool after exclusions | 21,037 of 22,062 crossing points |
| excluded because a reviewer has already seen that source-feature pair | 719 crossing points |
| further excluded as within 50 m of a burned card | 306 |
| spread | 1,300 km of northing, 7 occupied 200 km bands, **54 road controlling authorities** |
| per-authority cap | 42 of 350 (12%) |

Decoys: 24 `GRADE_SEPARATED/STRUCTURE_MAPPED`, 16
`UNRESOLVED/MOTORWAY_CARRIAGEWAY`, 25 `UNRESOLVED/DUPLICATE_GEOMETRY` (14 of
them the crossings the corridor walk and only the corridor walk withdrew), 14
`NO_EVIDENCE_EITHER_WAY`, 10 `TANGENTIAL`, 1 `MIXED_PLACE`.

### Stratification achieved, against the strata predeclared

The predeclaration names the cells; it does not split 350 across them. The
split was committed in `holdout3_review.py` **before the first draw**, for the
same reason the sample size was.

Cells overlap and a card is drawn once, so "drawn" below is which cell reached
it first and **"in pack" is its true stratum membership** — the number that
should be reported when the pack is scored. Every card carries the full list
in `qualifyingCells`.

| cell | intended | in pack | nationally eligible |
| --- | --- | --- | --- |
| angle 30-40 deg | 40 | **40** | 45 |
| angle 40-60 deg | 28 | 44 | 248 |
| angle 60-80 deg | 28 | 71 | 776 |
| angle 80-90 deg | 36 | 195 | 3,511 |
| structure 25-70 m away | 28 | **32** | 36 |
| same name, duplicate rule did not fire | 25 | **5** | **5** |
| unsealed access | 25 | 110 | 1,046 |
| unnamed both | 21 | 136 | 1,666 |
| state highway | 21 | 36 | 201 |
| urban | 21 | 150 | 2,549 |
| rural | 25 | 85 | 606 |
| junction witness | 21 | 94 | 940 |
| imagery <= 2023 | 25 | 69 | 591 |
| imagery year unknown | 6 | **0** | **0** |
| top-up, no cell | — | 30 | — |

Three cells could not be filled as intended, and none of it is a reason to
move a number:

1. **`same_name_not_dup` has five members in the entire eligible pool, and all
   five are in the pack.** This is the stratum that produced every one of the
   248-card holdout's three AT_GRADE failures. On the source-feature path
   `DUPLICATE_GEOMETRY` fires on 9,830 crossings rather than 712, and it has
   all but emptied the cell. The pack therefore cannot retest that failure
   mode at n=25 — it retests it at n=5, because n=5 is all there is.
2. **`imagery_unknown` has no members at all.** Basemaps attribution now
   covers 21,035 of 21,037 eligible crossings.
3. **`gs_named_structure` has no members**, so the decoy pack is 90 rather
   than 94. `NAMED_STRUCTURE` is described in the predeclaration as one of the
   two surviving GRADE_SEPARATED rules. On the shipping path it decides
   **nothing nationally** — zero of 22,062 crossing points. It is not wrong;
   it is inert, and no card can be drawn to test it.

The 30 cards short of 350 were topped up from the unstratified eligible
AT_GRADE pool by the same seeded rank and carry cell `topup`. Under-filling
instead would have moved n, which is the one number the declaration fixes.

### Where everything is

| | |
| --- | --- |
| the 440 card images | `scratch/holdout3/cards/` — gitignored, 50 MB, regenerate with the command in the manifest |
| every card's sha256 and size | `holdout3-cards-manifest.json` (committed) |
| three sample cards | `holdout3-sample-cards/` (committed) |
| the exact text the reviewer is given | `holdout3-reviewer-instructions.md` (committed), also written into the pack as `cards/INSTRUCTIONS.md` |
| **the answer key** | **`holdout3-answer-key.json` (committed, and the reviewer must never see it or anything derived from it)** |

### What has NOT happened, and must not happen out of order

No card has been reviewed. The pack was built by the same agent that wrote the
generator, which is precisely why that agent must not score it: the review
must be performed by a **fresh isolated reviewer** with no prior transcript, no
access to the classifier source, no prior verdicts and no score summary. That
reviewer is given `cards/` and `INSTRUCTIONS.md` and nothing else.

`scratch/holdout3_review.py score` then evaluates `nzcl.promotion.evaluate` on
the result. **If it fails, stop and report.** Do not draw more cards, do not
re-read the unclear ones, and do not change a rule and re-score this pack.

### One practical finding about reviewing 440 images

440 cards is a large amount of imagery for one reviewer to hold. Splitting the
pack across several fresh reviewers, each given a disjoint subset and the same
instructions, is compatible with everything the predeclaration requires — the
cards are independent and the blinding is per card. Splitting it across
several *passes by the same reviewer* is not, and is the exact mistake the
kappa 0.847 "double review" already made.

### 12a. Four corrections applied before any verdict is scored

**1. The scorer fails closed.** `nzcl/holdout.py`, under test in
`tests/test_holdout.py`. The loophole it closes: 350 cards are drawn, a
reviewer returns 340, and a scorer that joins on what came back computes a
bound over n=340 — the ten missing cards leaving the *denominator* instead of
counting as failures. They are not a random ten, they are the hard ones, and
the omission moves the number in the flattering direction by exactly the
mechanism the gate exists to forbid. `promotion.evaluate` cannot see it: it
takes counts, and by then the loss has happened.

`collate` returns **one row per card in the pack, always**, and raises unless
every card has exactly one valid verdict. A missing verdict, a duplicate, an
unknown card code and an invalid label each refuse to score, and the scorer
exits 2 without computing or writing a promotion verdict. `score
--materialise-missing` is the documented fallback: every missing, duplicated
or unparseable verdict becomes `unclear`, counted as a failure, and every
substitution is named. An unknown card code is fatal in both modes — it cannot
be materialised into anything, and it means the verdict file does not describe
this pack. The pack itself is asserted first: exactly 350 AT_GRADE cards, no
repeated code.

The tests prove each failure mode is impossible, including the one that
matters most: **a reviewer who omits ten hard cards and one who marks the same
ten `unclear` land on identical numbers.** If omission ever scored better the
incentive would point the wrong way.

**2. Independence fails closed, and was re-verified after the fact.** The
generator now stops rather than warns if any prior reviewed card cannot be
located or mapped. `seal` re-checks the drawn pack against every prior pack
rather than quoting the draw's own report:

| | |
| --- | --- |
| prior reviewed cards expected | 537 (208 + 248 + 81) |
| prior reviewed cards located as points | **537 — all of them** |
| prior link ids with no closure group | 0 of 1,054 |
| prior link pairs not mappable to a source-feature pair | 0 of 536 |
| link pairs collapsing onto another source pair | 1 — two graph pieces of one source pair, not a lost row |
| drawn cards sharing a burned source-feature pair | **0** |
| drawn cards within 50 m of a burned point | **0** |
| closest any drawn card comes to a burned point | **52.8 m** |

The exclusion is therefore exactly as strong as declared, with nothing
assumed.

**3. "Conservative floor" is withdrawn.** It appeared in the predeclaration,
in section 6a above and in `holdout-result.json`, and it claimed more than the
design supports. A floor is a statement about the *population*; earning it
needs a probability sample and population weights, and this draw has neither —
the cells overlap and were chosen for being hard. Over-weighting hard cases
makes a result likely to come in below national precision; it does not make it
a bound. All three sites now read: **performance on a deliberately difficult,
stratified holdout; not a probability sample, and not an estimate or formal
lower bound on national precision.** The Wilson interval applies to this
holdout's reviewed cases, not to a nationally weighted population. The
predeclaration carries the correction with the original words struck rather
than replaced, because a pre-registration that can be quietly rewritten is not
one. Nothing that governs the outcome moved: n is 350, the tolerance is 4,
unreviewable still counts as a failure.

**4. The draw is sealed.** `holdout3-seal.json`, written by
`holdout3_review.py seal` and pushed before the reviewer receives anything.
440 cards present, 0 bytes changed since the manifest, 0 files unaccounted
for, **0 unresolved tile failures**, and the sha256 of every card, of the
classifier (`crossings.py`, `topology.py`, `promotion.py`, `holdout.py`), of
the three generators, of the national record and its manifest, and of the
answer key.

The answer key appears there **as a hash and nothing else**, so the checkpoint
can be published — the repository is now public — and even shown to the
reviewer without disclosing a single disposition.

A card with a transient missing tile is now **retried as the same card**. If a
hole survives the retry the build stops. It is never answered by dropping the
card or drawing a replacement: cards that fail to render are not a random
subset, and swapping an awkward case for a convenient one is sample selection
performed by the network.

**After the seal, no card may be redrawn, re-rendered or substituted.**

> **Defect in the seal, stated rather than quietly fixed.** `sealedAtGitHead`
> was written as an empty string. The first version of that code ran
> `git rev-parse HEAD` with `cwd=` and captured only stdout, so when git
> declined the directory the field recorded nothing while the file still
> looked complete. The code now uses `git -C`, surfaces stderr, and **refuses
> to seal** if the head cannot be read.
>
> The seal was deliberately **not** re-run to repair the field, because
> re-running it now would stamp a commit that postdates the review, which is
> worse than an empty field. The anchor that matters is not damaged: the seal
> is committed in `fd3f0e2` and was pushed before the review verdicts arrived,
> and its 440 card hashes were re-checked at scoring time against both the
> pack and the copies the reviewers were given — **440 of 440 identical, on
> both**. What the empty field failed to record, git history and those hashes
> record instead.

### 12b. What the reviewer receives, exactly

`scratch/holdout3/cards/` — `T001.jpg` … `T440.jpg` and `INSTRUCTIONS.md`.

Nothing else. No repository access, no answer key, no manifest (it names
`missingTiles` and card counts but the seal is the public artefact), no
transcript, no prior verdicts, no score summary.

One isolated reviewer may work the fixed pack in batches. **No scoring and no
feedback between batches** — a reviewer who learns how they did on batch one
is no longer blind for batch two, which is exactly what made the kappa 0.847
"double review" an upper bound rather than an independent check.

---

## 13. The third holdout, reviewed and scored — THE GATE IS NOT MET

**Result: NOT MET. Two of the four conditions fail.** Full numbers in
`holdout3-result.json`, verdicts in `holdout3-verdicts/`.

| condition | requirement | observed | |
| --- | --- | --- | --- |
| zero confirmed grade-separated false positives | 0 | **0** | **MET** |
| zero confirmed not-a-junction false positives | 0 | **32** | **NOT MET** |
| 95% Wilson lower bound on AT_GRADE precision | >= 97% | **86.1%** | **NOT MET** |
| unreviewable counted among the failures | method | 4, counted | MET |

n = 350. **314 confirmed, 32 contradicted, 4 unreviewable.** Lower bound 86.1%
counting unreviewable as failures, 87.2% excluding them. The tolerance was 4
failures; there were 36.

These are figures for performance on a deliberately difficult, stratified
holdout. This is not a probability sample and is not an estimate or formal
lower bound on national precision.

### How the review was run

Four separate reviewers, **freshly spawned**, each with no access to this
transcript, the repository, the classifier, the answer key, prior verdicts or
any score summary. Disjoint subsets of 110 cards each — A = T001-T110,
B = T111-T220, C = T221-T330, D = T331-T440 — copied **out** of the repository
into four separate directories, so no reviewer could reach the answer key or
another reviewer's cards. Each received the verbatim `INSTRUCTIONS.md` text
and nothing else about the project. No feedback of any kind was given to
anyone, and no reviewer saw another's work.

**They are independent AGENTS, not independent people.** That is a real
improvement on the kappa 0.847 "double review", which was the same reviewer
after scoring, and therefore intra-rater and an upper bound. It is **not human
expert ground truth**, and nothing here should be read as if it were.

Verified in the scorer rather than taken on trust: 440 lines, 440 unique card
ids, no duplicates, every card T001-T440 present exactly once with exactly one
of a/g/n/u, no label outside the four, each per-reviewer file covering its own
range and no other, and the combined file agreeing with the four per-reviewer
files on all 440 rows. Distribution: 357 at-grade, 23 grade-separated,
54 not-a-junction, 6 unclear.

Also verified, because only this side could: **every one of the 440 images the
reviewers were given is byte-identical to the card sealed in
`holdout3-seal.json`**, and each reviewer's directory held their 110 images
and nothing else.

### Where the failures are

| stratum (membership) | n | failures | rate |
| --- | --- | --- | --- |
| **same name, duplicate rule did not fire** | **5** | **5** | **100%** |
| angle 30-40 deg | 40 | 14 | 35% |
| unsealed access | 110 | 23 | 21% |
| rural | 85 | 14 | 16% |
| angle 40-60 deg | 44 | 7 | 16% |
| unnamed both | 136 | 18 | 13% |
| structure 25-70 m | 32 | 3 | 9% |
| angle 60-80 deg | 71 | 5 | 7% |
| junction witness | 94 | 6 | 6% |
| imagery 2023 or older | 69 | 4 | 6% |
| urban | 150 | 8 | 5% |
| angle 80-90 deg | 195 | 10 | 5% |
| state highway | 36 | 1 | 3% |
| top-up (unstratified) | 30 | 0 | 0% |

By rule: `ORDINARY_CROSSROADS` confirmed 226 of 256, `JUNCTION_WITNESS` 88 of
94. The one HIGH-confidence AT_GRADE rule produced **5 of the 32** false
nodes, which is the more uncomfortable half of that line.

### Every failure is the same failure, and it is not grade separation

**All 32 are "not a junction". Not one is "grade separated".** The condition
that guards the expensive mistake — a node invented where one road passes over
another, fabricating a turn onto a motorway — **passed at zero.**

The 32 split into two families, which all four reviewers described
independently, in the same terms, without having seen each other's work:

- **one road drawn twice** — both centrelines running along the same single
  strip of tarmac;
- **a centreline with no road under it** — crossing paddocks, houses,
  watercourses and shelterbelts.

That four reviewers converged on the same two families, on disjoint cards, is
worth more than any single verdict in the pack. 16 of the 32 have both sides
unnamed, 19 are unsealed, 12 are rural, and they span 15 road controlling
authorities. 13 sit in the 30-40 degree band immediately above the tangential
threshold — but 8 sit between 80 and 90 degrees, where no angle test can help.

### same_name_not_dup: 0 of 5, and the fix did not hold

The stratum that produced every AT_GRADE failure of the 248-card holdout has
five members in the entire eligible pool. All five are in the pack. **None was
confirmed** — four called not-a-junction, one unreadable. Four of the five are
the same road: Birch Road North crossing Birch Road North, at 30.4, 34.5, 35.0
and 54.0 degrees.

`DUPLICATE_GEOMETRY` now fires on 9,830 national crossings and has all but
emptied this cell. On the five it did not catch, it caught none of them.

### Imagery quality was NOT the cause

The predeclaration accepted, in writing and in advance, the risk that this
pack would fail on unreadable cards rather than on classifier quality: the
248-card pack was 2.0% unreviewable, which on 350 cards is about 7, and 7
failures clears 97% at no n below roughly 1,070.

**It did not happen.** 4 of 350 AT_GRADE cards were unreadable — 1.1%. Zoom 19
and 512 px did their job. Had every one of the four been confirmed instead,
the gate would still fail, on 32 not-a-junction false positives and a bound of
86.9%. The five-unreadable threshold that would have failed the gate on its
own was not reached.

The four: T172, T279, T297 (all `ORDINARY_CROSSROADS`) and T310
(`JUNCTION_WITNESS`). Two decoys were also unreadable, both
`MOTORWAY_CARRIAGEWAY`.

### No single reviewer explains it

| | n | confirmed | not-a-junction FPs | lower 95% |
| --- | --- | --- | --- | --- |
| reviewer A | 80 | 75 | 5 | 86.2% |
| reviewer B | 92 | 80 | 11 | 78.6% |
| reviewer C | 91 | 81 | 7 | 80.9% |
| reviewer D | 87 | 78 | 9 | 81.5% |

Their rates do differ — B calls not-a-junction about twice as often as A — and
it lands on **both** decoys and AT_GRADE cards, so it is partly calibration
and partly disagreement. It does not change the answer. Leave any one reviewer
out entirely and the gate still fails:

| | not-a-junction FPs | lower 95% | |
| --- | --- | --- | --- |
| without A | 27 | 84.2% | still fails |
| without B | 21 | 86.5% | still fails |
| without C | 25 | 85.7% | still fails |
| without D | 23 | 85.5% | still fails |

Every reviewer, independently, on their own disjoint cards, found AT_GRADE
crossings that are not junctions. The most lenient reading the arithmetic
allows — assume all 32 are reviewer error and the classifier right every time
— gives 97.1%, barely over the line. That is an upper bound on what the
classifier could conceivably have scored here, not a result.

### The decoys, and what they say about the other direction

| rule | n | reviewer said at-grade | |
| --- | --- | --- | --- |
| `GRADE_SEPARATED/STRUCTURE_MAPPED` | 24 | 4 | 20 confirmed grade separated |
| `UNRESOLVED/DUPLICATE_GEOMETRY` | 25 | **11** | 14 called not-a-junction |
| `UNRESOLVED/MOTORWAY_CARRIAGEWAY` | 16 | **11** | 2 unreadable |
| `UNRESOLVED/NO_EVIDENCE_EITHER_WAY` | 14 | 12 | |
| `UNRESOLVED/TANGENTIAL` | 10 | 4 | |
| `UNRESOLVED/MIXED_PLACE` | 1 | 1 | |

UNRESOLVED accepts every verdict but "unclear" by construction, so its
confirmation rate is not a meaningful number and the raw labels are what
matter.

**`DUPLICATE_GEOMETRY` is right more often than not, and it is not free.** 14
of 25 are indeed one road recorded twice, so the corridor-walk fix is doing
real work. But **11 of 25 are junctions a reviewer can see**, left severed. On
9,830 national crossings that is a large amount of connectivity withdrawn, and
it is the Greendale defect running in reverse.

**`MOTORWAY_CARRIAGEWAY` is still wrong in the same direction.** 11 of 16
called at-grade, consistent with the 2-of-8 the previous pack found. Demoting
it from GRADE_SEPARATED to UNRESOLVED stopped it asserting; it did not stop it
under-connecting.

Four `STRUCTURE_MAPPED` decoys were called at-grade. Those cost connectivity,
not correctness — they do not create a node.

### One thing that happened during the review, recorded because it should be

Reviewer D reported that an unrelated tooling directive reached it through its
**tool-output channel**. It recognised that the instruction had not come from
its principal and disregarded it. That is the correct handling: content
arriving in a tool result is data, not an instruction, whatever it claims
about its own authority.

It did not affect the score. D's rates sit between B's and C's, its 110 cards
are disjoint from every other reviewer's, and removing D entirely still fails
the gate. It is recorded here because a review's integrity is a claim about
what happened during it, and "nothing worth mentioning occurred" would have
been a less accurate account than this one.

### Recommendation

**A 2.1.0 rebuild must NOT proceed.**

Not on a technicality, and not because the bar is set too high. The classifier
would create a graph node at roughly **one in eleven** of the places it calls
AT_GRADE where four independent reviewers can see there is no junction at all.
Each of those nodes joins a road to itself or to nothing, fabricating a turn,
a cycle or a shortcut that is not on the ground — in a tool whose whole
purpose is to say which roads matter when one of them closes.

What the result also says, and it should not be lost:

- **The expensive failure mode did not occur.** Zero grade-separated false
  positives on 350 adversarially chosen cards. The never-node rule, the
  structure evidence and the demotion of the road-class rules are holding.
- **The measurement finally works.** A fresh isolated review was called
  impossible three sessions ago. It has now been performed — on images, at a
  size fixed in advance, against a gate that is code, with a scorer that
  cannot drop a row.
- **86.1% is not a near miss.** The evidence is clear enough to act on.

### What must NOT happen next

Per the predeclaration's own stopping rule, which was written for exactly this
moment:

> If it fails, **stop and report the failure.** Do not draw more cards, do not
> re-review the unclear ones hoping for a different answer, and do not change
> a classifier rule and re-score this pack. A fourth pack, drawn independently
> of all three previous ones, is the price of changing the classifier again.

This pack is now burned — it becomes development data the moment anything is
derived from it. Do not tune `ORDINARY_CROSSROADS`, `DUPLICATE_GEOMETRY`,
`corridor_polyline` or the tangential threshold against these 350 cards and
re-score. Four samples have now been spent, and there is a limit to how many
independent ones this dataset can supply.

The honest next step, if the classifier is to be improved, is to use **these**
findings to design the change — the two failure families the reviewers named
are specific and actionable — and to accept that a fourth, independently drawn
pack is what would have to score it.
