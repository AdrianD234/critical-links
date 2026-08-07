# The boundary-movement model — PR 2, phases 6 to 12

What changed, why, and what is still not true. Written against the V1 semantics
audit in `v1-semantics-audit.md`, which established that the endpoint measure is
itself the defect.

## The measure

V1, and PR 1's V2, both answer:

> can the closed segment's own start node still reach its own end node?

This answers:

> did trips go through here, and what does each of them have to do instead?

A **movement** is an ordered pair of boundary crossings — an entry port and an
exit port — whose cheapest route in the INTACT network traverses at least one
removed arc. The port arcs establish that traffic can reach the entry from the
open network and discharge from the exit back into it, in compatible
directions. The trip itself is measured between the two ports' **closure-side**
nodes.

### Why not between the ports' outside nodes

The first implementation measured between the ports' outside nodes, which looks
more thorough and is wrong. On a square block with the south side closed, the
outside nodes of the two crossings are the block's other two corners, and the
cheapest intact route between those runs straight along the north side. So:

| leg | measured | why it is wrong |
| --- | --- | --- |
| intact | 300 m (round three sides) | constrained to pass through the closure |
| replacement | 100 m (the north side) | not constrained at all |
| "detour" | **−200 m** | the two legs answer different questions |

Two faults in one. The comparison was not like-for-like, and it contradicted the
endpoint measure on exactly the case where PR 2 promised not to change the
answer.

Measured between the **closure-side** nodes the same square gives intact 100 m,
replacement 300 m, penalty +200 m — which is what the endpoint measure gives,
because on a simple two-way segment the ports sit on the segment's own
endpoints. The compatibility property holds by construction rather than by
coincidence, and `TestSimpleSquare` pins it to the metre.

## What each stage is, and where it lives

| stage | module | question |
| --- | --- | --- |
| closure resolution | `nzcl.closure` | what exactly is removed |
| port generation | `nzcl.ports` | where the closure meets the open network |
| intact movements | `nzcl.movements` | which trips actually went through here |
| replacement paths | `nzcl.replacement` | what each of those has to do instead |
| corridor selection | `nzcl.corridor` | where a driver diverts and rejoins |
| geometry | `nzcl.routegeom` | what can honestly be drawn |
| isolation | `nzcl.physical` | what, if anything, is cut off |
| orchestration | `nzcl.impactv2` | all of the above, timed separately |

Replacement, physical isolation and directed access are computed by different
code on different graphs and reported in different blocks. Nothing merges them.
V1's central defect was letting a routing failure produce an isolation headline.

## The corridor choice rule

Among candidate (upstream, downstream) pairs, in order:

1. a represented replacement route exists between them;
1b. **preference** — both ends are junctions, where any such pair qualifies;
2. minimum outward distance from the closure, read as the *farther* of the two
   sides;
3. minimum combined outward distance;
4. minimum replacement-path cost;
5. strongest road-continuity evidence;
6. stable-id tie-break.

Rule 1b is a preference and not a requirement because requiring it returned NO
corridor on a fixture that plainly had one: with the closure removed, the
junctions either side of it were the same node, so the only admissible pair was
degenerate. Which tier was used is reported as `admissibilityLevel`.

### "Stable id" has to mean stable

The first implementation hashed `arc_id`. That is perfectly reproducible on one
database and completely reassigned by the next ingest, because `link_id`,
`arc_id` and `node_id` are handed out by the noding pass in the order source
features arrive.

Shuffling the input of a nine-link fixture — the shuffled-input test the brief
asks for — **flipped the selected corridor pair on three seeds out of eight,
with no road changing.** That is the stop condition "movement selection depends
on row order", found by the test written to find it.

`nzcl.stableid` names things the publisher chose instead:

- a link → its AMDS source feature GUID;
- an arc → that GUID plus the traversal direction;
- a node → its position in EPSG:2193 metres, to a millimetre (finer than the
  10 mm node-assignment tolerance, so two nodes cannot collide onto one key).

Ports, movements and corridor candidates now order and tie-break on those.
Twelve shuffled seeds agree on every reported figure.

## Road-continuity evidence

Route designation, canonical road name, degree-two continuity, state-highway
status, road class / model asset type, and heading continuity — carried as a
ranked tuple rather than collapsed into a score, so "strongest evidence" is a
comparison a reader can check. Heading is ranked LAST, because a straight-ahead
side road defeats it, which is the exact failure in V1's straightest-
continuation walk.

**RAMM corridor identity is not used.** `docs/LICENSING.md` records it as not
cleared — `copyrightText` empty, not catalogued. It is also absent from this
database entirely, and its `roadCorridor` spans hundreds of kilometres, so every
link on a state highway would share one value and it would separate nothing.
Excluded on all three counts.

## Geometry

Never a straight line across a hole in the data. pgRouting guarantees a route is
topologically continuous; it guarantees nothing about the geometry. Two links
can share a node and have their drawn ends metres apart.

So `nzcl.routegeom` emits **separate pieces** either side of a gap, records
where each gap is and how wide, and marks the route `animationSafe: false` — a
reveal animation that sweeps along a line asserts the line is unbroken. The API
returns a MultiLineString even for a single piece, so a client cannot flatten it
into one coordinate list and thereby draw across a gap.

Gap tolerance is 50 mm: the junction splitter's own tolerance. Anything wider
than the rule that decided two ends were the same place is, by that rule's own
standard, not the same place.

## The timeout contract

A search that did not finish is UNRESOLVED. It is never DISCONNECTED and never a
finding about a road. This is enforced structurally rather than by convention:
`routing.route_many_paths` returns a search STATUS alongside its paths, and no
V2 code may read an absent pair as "no route" unless that status is OK.

**The older `routing.route_many` cannot express the difference**, and V1's
corridor search reads its empty return as failure — `detour._corridor` falls
through to `Corridor("DISCONNECTED", ...)` when a statement timeout is swallowed.
V1 is frozen, so that behaviour is recorded here and left alone. Nothing in V2
uses `route_many`.

## What is bounded, and what the bound costs

| bound | value | reported as |
| --- | --- | --- |
| ports per side entering the pairing | 20 | `candidateBound`, `truncated` |
| corridor beam width | 6 | `searchBounds.beamWidth` |
| corridor hops | 12 | `searchBounds.maxHops` |
| corridor outward distance | 5,000 m | `searchBounds.maxOutwardM` |
| corridor candidates per side | 20 | `searchBounds.maxCandidatesPerSide` |
| corridor pairs routed | 400 | `searchBounds.maxPairs` |

Every candidate the bound refused is still RETURNED, as an explicitly
not-evaluated row with a reason. A candidate that vanishes without a row is
indistinguishable from one the engine decided against, and the difference is the
whole audit trail.

## Performance

Measured on the national snapshot (375,696 links / 731,286 arcs), warm:

| stage | p50 | p95 |
| --- | --- | --- |
| closure resolution | 0 ms | 1 ms |
| port generation | 1 ms | 2 ms |
| intact movements | 448 ms | 462 ms |
| isolation | 19 ms | 111 ms |
| replacement paths | 457 ms | 486 ms |
| corridor expansion | 490 ms | 1,118 ms |
| geometry assembly | 1 ms | 2 ms |
| **total** | **1,414 ms** | **1,916 ms** |

Under the 5 s ceiling. **Not** under the 1 s p95 target, and it will not get
there this way: three logically distinct edge sets are needed — intact, and
closure-removed twice — at roughly 460 ms per pgRouting edge-set load, which is
a 1.4 s floor. Correctness outranks the target, so the loads stay.

### The optimisation that was not the optimisation

The beam walk looked like the expensive part, so an early stop was added. The
stage got **slower**. Profiling 12 national requests:

```
_annotate_degree    71 calls   10,888 ms   153.4 ms each
route_many_paths    10 calls    4,921 ms   492.1 ms each
_step_rows          51 calls      115 ms     2.3 ms each
```

The walk was never the cost, and it only ever reached two to four hops. ONE
degree lookup was, and the early stop had added five more per request. `links`
carries no index on `source_node` or `target_node`, and the query joined on
`(source_node = n OR target_node = n)`, which cannot use one anyway — a
sequential scan of 375,696 rows to ask about four nodes. Rewritten as two
indexed lookups over `arcs`, UNIONed. The same shape was in closure resolution.

Corridor expansion 1,473 → 490 ms; closure resolution 69 → 0 ms.

### Routers

All four the brief names are present in pgRouting 3.6.1 and were benchmarked.

| router | p50 | p95 | exact? |
| --- | --- | --- | --- |
| `pgr_dijkstra` | 462.7 ms | 500.4 ms | yes |
| `pgr_bdDijkstra` | 466.2 ms | 471.3 ms | yes |
| `pgr_aStar` | 756.5 ms | 1,047.6 ms | yes |
| `pgr_bdAstar` | 751.9 ms | 775.6 ms | **NO** |

A* is slower here because the dominant cost is loading the edge set and the A*
edge query needs two extra joins for node coordinates; on this graph that costs
more than the guided search saves.

**`pgr_bdAstar` returns a non-shortest path.** On link 150288 (nodes 141607 →
140873, its own two arcs excluded) it reports 28,878.857 m over 37 edges where
the other three all report 26,085.832 m over 39 — 10.7% too long. Re-summing the
Dijkstra path's arc costs directly from the `arcs` table gives 26,085.832 m. The
same call with `heuristic => 0` is correct, so the fault is in how the
bidirectional search terminates with a heuristic, not in the graph.

Stated carefully: `pgr_bdAstar` **did not satisfy the shortest-distance
contract in this configuration on this graph**. No standalone upstream
reproduction has been done, so this is not a report of a general pgRouting
defect — it is a reason not to use that function here. Nothing does.

## Five defects found in review, and what each one was

Every one was reproduced before it was fixed. The failing output is recorded
because "it is fixed now" is not evidence.

### 1. A closure piece 50 km away ranked as zero metres from the selection

`ports.py` read the along-closure offset as `offsets.get(inside, 0.0)`. The
Dijkstra that builds `offsets` is seeded only from the selected segment's own
two nodes, so a node in any OTHER connected piece of the closure is never
reached and fell through that default.

Reproduced with one AMDS source feature in two pieces 50 km apart:

```
entry closure_node=5  x=50300  distance_from_selected_m=0.0  Far East
entry closure_node=4  x=50000  distance_from_selected_m=0.0  Far West
entry closure_node=1  x=  300  distance_from_selected_m=0.0  Near East
entry closure_node=0  x=    0  distance_from_selected_m=0.0  Near West
```

All four far ports reported 0.0 m and sorted FIRST. Ports are ordered and
truncated by that number, so an unrelated piece could take the candidate
allowance, change which movements were evaluated, and change corridor
selection.

Now: every port carries `closureComponentId` and `inSelectedComponent`;
along-closure distance exists only within the selected piece and is `null`
elsewhere, never zero; ordering is (own piece, component, distance, stable
key). Movements are identified WITHIN a piece — a trip through the closure
crosses one contiguous closed stretch, and pairing across pieces describes two
interruptions rather than one movement. Cross-piece pairs are counted, not
routed.

### 2. A truncated search still produced a definitive headline

The sample recorded 10 truncated movement analyses that nonetheless said
"Through movement diverts" or "has no represented replacement". Each reads as a
statement about EVERY movement the closure interrupts, and an unevaluated pair
could hold the worst detour, the only disconnected movement, or the one the
reader cares about.

Now: `MovementSet.exhaustive`, and any headline in `DEFINITIVE_HEADLINES` is
downgraded to **Partial analysis** with `HEADLINE_WITHHELD_NOT_EXHAUSTIVE`
whenever movement or corridor generation truncated. Resolved sub-results stay
visible.

Separately, the routing was bounded at 20×20 per side but the AUDIT PAYLOAD was
not: `_truncation_rows` built the entire dropped cross-product as JSON. A
bounded computation with an unbounded report is still unbounded. Now exact
counts plus a deterministic sample capped at 100.

### 3. The 5 km corridor bound was not a bound

`MAX_OUTWARD_M = 5_000.0` was documented as a cap, and the sample recorded
chosen corridor ports at 6,613 m, 7,863 m and 10,582 m. All three sit at
`hops=0` — they are SEEDS, whose distance is the boundary arc's own length,
inserted before a check that only ever runs on a step. On a rural state highway
one link can be over ten kilometres.

The behaviour is right: the first node outside the closure is the first place a
driver could act on, however far away, and there is no nearer answer to give.
The claim was wrong. It is now `MAX_EXPANSION_OUTWARD_M`, bounding the walk;
`searchBounds.seedMayExceedExpansionBound` is reported; an over-bound candidate
carries `beyondSearchBound`; and a corridor whose chosen pair contains one
reports `confidence: low` and says so in its explanation.

### 4. A per-path INVALID_GRAPH did not poison the request

A replacement that traverses its own closure means the routing exclusion did
not take. Every other path in that set came from the SAME edge query under the
SAME exclusion, so none of them is trustworthy either. Previously only the
offending path was marked, and a different movement could still become the
principal result and carry an ordinary headline — on a request where the engine
had just caught itself routing through a closed road.

Now one broken path sets the whole `ReplacementSet` to `INVALID_GRAPH`, which is
in `UNRESOLVED_STATUSES`, so the headline becomes "Analysis unresolved" and no
figure from the set is reported.

### 5. Corridor selection had no intact witness

Every term in the choice rule is about the post-closure world. Nothing checked
that anybody ever travelled between the two chosen decision nodes THROUGH the
closure — so a pair could have a perfectly good replacement route while the
cheapest intact route between those nodes never touched the closure, and the
"corridor" would describe a diversion nobody needs to make.

Now each candidate pair must produce a WITNESS — upstream trail, entry port, the
intact crossing, exit port, downstream trail — validated as directionally
continuous arc by arc, starting and ending at exactly the chosen decision
nodes, and genuinely using a declared closure arc. Pairs that cannot are struck
out BEFORE the choice.

### And one the oracle found in itself

`reviewv2.load_oracle_graph` stored one edge per node pair — the cheapest arc —
then deleted edges whose stored arc was closed. Where two arcs run between the
same nodes and only the cheaper is closed, that removed the connection
entirely: the oracle reported "no path" for Lyon Street's endpoint pair while
the engine correctly routed 108 m. Had it done the same on a MOVEMENT pair,
engine and oracle would have agreed on `None` for opposite reasons and the
agreement would have been worthless. Parallel arcs are real here — 731,286 arcs
over 725,497 node pairs.

## Turn restrictions

Checked AFTER routing, because the multi-target searches run on the plain arc
graph. A route using a banned manoeuvre is marked
`TURN_RESTRICTION_UNSUPPORTED` — unresolved, not DISCONNECTED, because a legal
way round may exist and this search did not find it — and is never offered as
the canonical detour.

The measured exposure is smaller than expected:

| | |
| --- | --- |
| restricted-turn records nationally | 43 |
| that restrict any modelled vehicle class | **1** |
| with all three mode flags false | 42 |

So for `profile='car'` the national exposure is ONE banned manoeuvre, involving
two links. A record with every mode flag false bans nothing, and treating it as
a ban would invent a constraint AMDS did not publish.

This does not make any route road-legal, and nothing in the interface says it
does.

## The national sample

500 links from the 374,786-link frame, drawn deterministically by
`nzcl.samplev2` under seed `detour-v2-pr2-boundary-sample-1`. Raw rows,
summary and review pack in `national-sample/`.

**This is a sample, not a national estimate.** The strata deliberately
over-represent awkward cases, which is what makes it useful for finding defects
and what makes any percentage from it meaningless as a description of the
country. **No human has reviewed these results.** The review pack is a worklist.

### Coverage

Both islands (325 north / 175 south), 70 road-controlling authorities, 15 state
highway / 485 local, 232 urban / 238 rural, 21 one-way / 479 two-way, 195
bridge / 305 not, 298 short / 104 medium / 98 long source features, 250 single-
child / 250 multi-child, 412 named / 88 unresolved. Topology confidence low on
81 and medium on 419 — never high, as designed.

The state-highway and one-way counts are the required floors plus whatever the
round-robin top-up across 70 authorities happened to add. They are thin, and a
question specifically about one-way behaviour needs its own sample.

### Result

| | count |
| --- | --- |
| unresolved or timed out | **0** |
| errored | **0** |
| geometry gaps in a replacement route | **0** |
| movement candidate searches truncated by the bound | 10 |
| replay digest | `bb7681d22afda98320a52630ed5314ffba6e329a099fcfbe033c549be040cab7` |

Runtime per request: p50 1,543 ms, p95 2,359 ms, max 2,725 ms.

### Endpoint measure versus boundary measure

These measure **different quantities**. A difference is not evidence that
either is wrong.

| transition | count |
| --- | --- |
| DISCONNECTED → DISCONNECTED | 241 |
| OK → OK | 152 |
| DISCONNECTED → no movement identified | 74 |
| **OK → DISCONNECTED** | **26** |
| OK → no movement identified | 4 |
| **DISCONNECTED → OK** | **3** |

Where both produce a penalty (152 cases) the median difference is **0 m** —
they agree exactly on the ordinary case, which is the compatibility property
holding on real data rather than on a fixture. The p95 difference is 728 m and
the maximum 12.7 km.

Every one of the 26 **OK → DISCONNECTED** cases is a multi-link closure where
`reducesToEndpoints` is false, so the two are measuring different node pairs.
Link 375011 is the clearest: 13 links removed, the endpoint measure reports a
108 m alternative between the selected child's own two nodes, and the boundary
measure finds no replacement for the through movement at all — while physical
isolation reports that something IS cut off. A reassuring 108 m is exactly the
failure the V1 audit describes.

`isolationChanged: 0` — the endpoint and boundary paths agree on physical
isolation for all 500, as they must: both call the same exact computation.

### Where the diversion starts

Chosen corridor port distance from the closure, over the 168 links with a
chosen pair: p50 **126 m**, p95 3,131 m, max 10,583 m. Half the time the
diversion begins at the first junction outside the closure; the tail is rural
state highway where the next junction genuinely is kilometres away.

### The 78 with no through movement

All are dead-end spurs: every crossing sits on ONE node, because the far end has
no other link and becomes an interior node with no ports. There was never a trip
through a cul-de-sac. V1 calls 74 of them "No endpoint route" and the endpoint
measure calls 73 the same, both of which read as a failed analysis. 76 of the 78
separate nothing, because a closure that merely detaches itself has cut nothing
off.

### The geometry-gap number was an artefact, twice over

The first run of this sample reported a geometry gap on **237 of 500**. None
were real: the closure and the selected segment were being run through the
*route* assembler, so a fifteen-child source feature came out as "fourteen gaps,
the widest 406 m" — the distance between links that were never adjacent. After
`routegeom.collect` the count is **0**.

So the gap machinery has fired zero times on real national data. It is not
untested — a fixture nudges a link's geometry 3 m in the database, leaving its
nodes alone, and asserts three pieces, two 3 m gaps, no bridging, and the
animation disabled. But nothing in this sample exercised it, and that is worth
knowing rather than reading 0 as proof the guard works.

## Caching

**None was added.** `impactv2` computes every request from scratch. Two cache
defects in this project have taken the form "correct key for the object it was
designed for, but something selection- or location-specific bundled into the
payload beside it", and the cheapest way not to ship a third is not to add one
until the payload has settled. The client keeps the boundary result under its
own query key, never a parameter on the endpoint one, so neither can be served
out of the other's entry.

## What is still not true

- Time figures are derived from estimated speeds and are measured ALONG the
  distance-minimal path. The canonical answer is minimum represented-network
  DISTANCE, and every time value carries
  `TIME_ALONG_DISTANCE_MINIMAL_PATH`.
- "Represented" is load-bearing: the answer is the shortest path in the graph
  built from AMDS, not the shortest path on the ground.
- Where two routes cost exactly the same, one through the closure and one round
  it, which one the router returns decides whether the pair counts as a
  movement. Such a pair has a network penalty of exactly zero and is flagged
  `CLOSURE_NOT_NECESSARY_EQUAL_COST_ALTERNATIVE`. The independent oracle asserts
  only the strict cases in both directions rather than claiming something the
  data does not determine.
- Turn restrictions are not applied to the multi-target searches. AMDS publishes
  60 nationally, so the exposure is negligible, but it is not zero and it is not
  handled.
- Corridor candidates are named by road name where one is resolved and by node
  id where none is. That is honest and it is not friendly.
