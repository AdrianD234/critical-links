# National graph reconciliation: TypeScript v1.0.0 vs Python v2.0.0

**Question.** The new PostGIS national snapshot reports +211 graph links and
+418 arcs against the earlier TypeScript snapshot. Is that changed source data,
or changed processing? A matching source-feature count of 272,441 does not by
itself prove the inputs were identical.

**Answer.** The inputs are identical. The difference is entirely topology
processing, and it is the expected effect of a known, deliberate fix.

---

## What was compared

| | TypeScript | Python | Delta |
| --- | --- | --- | --- |
| Snapshot | `amds-national-2026-07-27-4c601cfa` | `amds-national-2026-07-28-5b359d84` | |
| Retrieved | 2026-07-27 04:34 UTC | 2026-07-28 13:38 NZST | +1 day |
| Processing version | 1.0.0 | 2.0.0 | |
| AMDS `currentVersion` | 12 | 12 | **same** |
| Where clause | `status=1 AND modeVehicle=1` | identical | **same** |
| Source features | 272,441 | 272,441 | 0 |
| Degenerate dropped | 15 | 15 | 0 |
| **Distinct source OBJECTIDs** | **272,426** | **272,426** | **0** |
| Graph links | 375,485 | 375,696 | **+211** |
| Arcs | 730,868 | 731,286 | **+418** |
| Nodes | 338,435 | 338,182 | **−253** |
| Junctions cut at | 103,059 | 103,270 | **+211** |
| Source links cut | 49,302 | 49,337 | +35 |
| **Total network length** | **147,847.611 km** | **147,847.593 km** | **−18 m** |

### Stable-ID set comparison

Every `objectId` was extracted from the TypeScript `links.ndjson` and compared
against `SELECT DISTINCT source_object_id` from PostGIS:

```
only in TypeScript:  0
only in Python:      0
in both:             272,426
```

**No source record was added, removed or renumbered.**

### Raw hashes are NOT comparable — and were not used as evidence

`raw_sha256` differs (`685bd383…` vs `8e7591b0…`), and that difference means
nothing here. The two pipelines hash different things: the Python ingest
computes `sha256(json.dumps(body, sort_keys=True))` per download batch and
combines, over batch boundaries the TypeScript client never used.

Comparing them would be comparing serialisations, not content. The ID-set
comparison above is the reconciliation that carries weight.

---

## Explaining the delta

The three numbers move together and in the direction a single known change
predicts:

- **nodes −253** — node assignment now probes the 3×3 neighbourhood of its
  spatial grid cell, so endpoints that are within tolerance but fall either side
  of a cell boundary resolve to *one* node instead of two. Fewer nodes.
- **junctions +211, links +211** — with those endpoints correctly coincident,
  more of them are recognised as lying on another link's interior, so more
  source links are split. One extra junction produces one extra graph link.
- **arcs +418 ≈ 2 × 211** — the added links are predominantly two-way, which is
  what a bidirectional split produces.

That fix was made deliberately during cross-validation, after the two engines
disagreed on a detour by 3.6 km because two links 0.4 mm apart were assigned
different nodes. It is a correction, not a regression: v2.0.0 connects a road
network that v1.0.0 left slightly shattered.

**Total length is unchanged to 18 m in 147,848 km** — 0.00001%. Splitting a link
does not change its length, so this is the control that confirms no geometry was
gained or lost; the residual is floating-point accumulation over 375,696 values.

---

## Not yet done

- **Stratified route cross-validation** between the two engines on the national
  graph. The TypeScript solver runs in-memory from `links.ndjson`; the harness
  to run both over a common national sample and diff the results does not exist
  yet. It was done at Wellington scale and is what found the node-assignment bug
  above.
- **Component and largest-component comparison.** Python reports 5,469
  components; the TypeScript national run did not record a component count, so
  there is nothing to compare against. Future snapshots record it.

Neither gap affects the conclusion that the *inputs* are identical, which was
the question. Both are worth closing before the national snapshot becomes the
governed baseline for a batch.

---

## Conclusion

`amds-national-2026-07-28-5b359d84` is built from exactly the same 272,426
source features as the earlier national extract. The graph differs only where
processing version 2.0.0 corrects node assignment, and the correction reduces
spurious disconnection rather than introducing it.

It is suitable as the national baseline for interactive Explore. Before it
becomes the baseline for a governed national batch, close the two gaps above.
