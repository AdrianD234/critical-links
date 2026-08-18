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

UNRESOLVED accepts every verdict but "unclear" by construction — it makes no
claim about the ground — so its 100% is not a meaningful number and is printed
only for completeness.

**These figures are a conservative floor, not national precision.** The pack
deliberately over-weights the cells most likely to fail. No population-weighted
estimate is offered: the cells overlap and the draw is not a clean probability
sample, and inventing a number would be worse than saying so.

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

## 7. Status against the gates

| gate | status |
| --- | --- |
| 1. mixed places — hard stop | **met**: withdrawn entirely under all policies, invariant asserted in code and in the ingest, fixture added |
| 2. blind the review | **met**: 208-card blinded pack, answer key separated, randomised; the old review relabelled |
| 3. expand the AT_GRADE sample | **met**: 196 AT_GRADE reviewed on a FRESH holdout independent of the development pack. **0 confirmed grade-separated false positives**, lower bound 92.8% / 95.5%. The 208-card pack is relabelled development data and is not re-scored |
| 4. Greendale attribution | **met**: Clintons x McLaughlins is named as the cause throughout; Clintons x Greendale is recorded as real but not causal |
| 5. classification language | **met**: `ORDINARY_CROSSROADS` is MEDIUM and says "PROBABLY" |
| 6. promotion / rebuild | **not done, and deliberately so.** Gate 3 now passes, but no `processingVersion` 2.1.0 snapshot has been built. See section 9 |
| 7. clustering radius | **met**: 5/10/25/50 m reported, monotonicity proved and tested, noding shown independent of it |
| 8. G_possible provenance | **partial**: `crossing_policy='possible'` exists and is tested; per-route provenance of which unresolved crossings a route uses is **not yet implemented** |
| 9. double-review | **met, with its limits stated**: 40 cases recoded, 38/40 agreement, kappa 0.847, both disagreements adjudicated. Intra-rater, and taken after scoring, so it is an upper bound. See section 6b |
| 10. repo hygiene | **met**: the derived record is a sha256 manifest plus a deterministic 250-row sample; the tracked screenshots went from 21.6 MB to 456 kB. See section 8 |

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
