# V1 / V2 shadow comparison: first sample

**Snapshot:** `amds-wellington-2026-07-27-6ef785ad` — the only snapshot with a
complete V1 batch (37,059 rows), so the only one where V1 and V2 can be
compared without computing V1 afresh for the whole network.

**Scope:** `source_feature`, deliberately. It is the only scope under which the
two engines answer the same question. Comparing V1's whole-source-feature
closure against V2's segment default would produce differences explained
entirely by scope, and reporting those as engine disagreement would be
worthless. Scope effects are quantified separately in
[`v1-semantics-audit.md`](v1-semantics-audit.md).

**Sample:** 120 links drawn with a fixed seed (`random.seed(20260807)`) from the
4,443 Wellington results where V1 returned `DISCONNECTED` with
`isolation_link_count > 0` — that is, from the population where V1 tells a
reader a road was cut off. This is not a random sample of the network; it is a
census-weighted sample of the claim under test. Profile `car`, metric
`distance`, both directions.

Reproduce with `nzcl.shadow.compare`, or per link:

```
GET /api/v2/links/{link_ref}/shadow-comparison?scope=source_feature&metric=distance&vehicle=car
```

Results persist to `closure_shadow_comparisons`, keyed by snapshot / link /
scope / profile / metric / direction / V1 version / V2 version.

---

## Agreement

| Difference kind | Results |
| --- | --- |
| none — the engines agree entirely | 102 |
| `isolation` | 18 |
| `classification` | 6 |
| `metric` | 0 |
| `closure_set` | 0 |

At equal scope the two engines remove **identical closure sets** in every one of
the 120 cases, and produce **identical replacement-path distances** in every
case. That matters: it means V2 has not changed the routing, and every
difference below is attributable to the isolation measure alone.

## Where the wording changes

| V1 wording | V2 wording | Results |
| --- | --- | --- |
| Road cut off | Road cut off | 108 |
| Through route found | Through route found | 6 |
| Road cut off | **Directional access loss** | 3 |
| Road cut off | **No endpoint route** | 2 |
| No replacement path | **Road cut off** | 1 |

Five results in 120 (4.2%) told a reader a road was cut off when nothing was
physically separated from the principal connection. In three of those the
endpoints simply stopped being mutually reachable — a one-way artefact, not a
road losing access. In two there was no directed path and no separation at all.

One result moved the other way: V1 reported no replacement path and no
stranding, while V2 finds a component genuinely separated. V1's under-claim and
its over-claims have the same root cause — a directed reachable set is not a
component of the undirected graph, and it can be larger or smaller than one.

## How much road is claimed

| | Total stranding attributed across the 120 results |
| --- | --- |
| V1 | **285 km** |
| V2 | **95 km** |

V1 attributes three times as much stranded road as exists. This is the same
defect the Tokoroa case shows from the other side: there, V1 reported 13.64 km
where the exact answer across all resulting components was 27.17 km. V1's
figure is neither an upper nor a lower bound on the truth. It is the size of
whichever directed reachable set terminated first inside a 5,000-node cap,
which is not a quantity anyone asked about.

## Topology confidence and anchor ambiguity

Two fields added after review, reported for the first time here.

| | Results |
| --- | --- |
| `topologyConfidence: low` | **12 of 120** |
| `principalSideAmbiguous: true` | **0 of 120** |

Twelve closures have an unresolved near-miss endpoint within 25 m. For those,
the connectivity result may be an artefact of the ingest tolerance rather than
a fact about the road: if the two endpoints are one junction in reality, the
link the engine calls a bridge is not one. That is a tenth of the sample, and
it is exactly the population where a "cut off" headline is least safe.

No closure in this sample had an ambiguous principal side, which is what one
would expect from a sample drawn near state highways - the anchor is decisive
when a state highway sits on one side. The field exists for the rest of the
network, where it is not.

> **These figures were regenerated after the review fixes and differ from the
> first run.** The earlier version of this note reported 299 km / 123 km and
> 109 identical wordings. The isolation figures moved because two defects were
> corrected: the principal-side tie-break was following BFS order on exact ties,
> and a bridge closure whose child subtree won the anchor contest reported the
> parent side with non-zero counts and an empty id list. Both are now covered by
> tests. The V1 column moved by one row because the earlier table included a
> stray comparison from a single-link check.

## Runtime

120 comparisons - both engines, end to end - in 47.0 s.

| | median | p90 | max |
| --- | --- | --- | --- |
| V1 | 239 ms | 276 ms | 348 ms |
| V2 | **147 ms** | **177 ms** | **253 ms** |

V2 is faster despite computing an exact partition rather than a bounded walk,
because the commonest case - one link that is not a bridge - is answered from
the precompute with no traversal at all. A bridge closure does walk, but only
the separated side; the principal side is derived by subtraction from the
precomputed component aggregates and is never visited. These figures exclude
the Gu build, which is paid once per snapshot and profile (Wellington 52 ms,
national 1.35 s) and is persisted.

Note that "exact" here means the partition of the represented graph, not a
claim that the graph models the real network. Those are reported separately as
`calculationExact` and `graphExact`; the second is always false.

## What this does not show

* Wellington only. The V1 batch does not exist for the national snapshot and
  this PR does not run one. Wellington is urban-weighted and its one-way share
  is not representative.
* 120 of 4,443 available cut-off results. The proportions above carry ordinary
  sampling error and should not be quoted to more than one significant figure.
* Nothing here compares the two engines under `segment` scope, because V1 has
  no such scope. The case for the new default rests on the semantics argument
  and the Tokoroa reproduction, not on this table.
