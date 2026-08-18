# The invented-movement audit measures a metre, not a centimetre

**Status: recorded, not acted on.** The guard is unchanged. This branch is a
record of a finding, not a fix, and is not proposed for merge.

---

## What happened

A national ingest on `main` (`a8ff042`) refuses to load:

```
crossing policy 'evidence': 0 crossings noded
refusing to load: 387 crossing(s) left disconnected by the classifier
are connected in the graph anyway.
```

The guard is `topology.audit_no_invented_movements`, and it is a good guard.
The property it states is narrow and correct:

> for every crossing NOT noded, the two source features must share no graph
> node **at that crossing point**.

## The mismatch

Nodes are assigned by merging coordinates within `node_tolerance_m`, which the
ingest passes as **0.01 m**. The audit accepts any shared node within **1.0 m**
of the crossing:

```python
if (px - x.x) ** 2 + (py - x.y) ** 2 > 1.0:   # squared -> a 1.0 m radius
    continue
```

That is **100×** the distance at which a node exists at all. So the audit can
report a crossing as connected because the two roads legitimately meet at a
*different* point up to a metre away.

## The shape that triggers it

Not exotic — a road that crosses another and joins it just past the crossing,
which is what an overbridge with a slip lane looks like from above:

```
    A  -----------------+--+-----------------
                        |  |
                   crossing  T-junction 0.5 m away
                        |  |
                        B--+
```

`B` crosses `A`'s interior at `(50, 0)` — correctly refused, no override. `B`
then ends on `A` at `(50.5, 0)`, an ordinary T-junction, cut normally. They
share a node at the T-junction, **not** at the crossing, so the stated property
holds. The audit reports a violation anyway.

`python/tests/test_invented_movement_audit.py` pins this as
`xfail(strict=True)`, so the claim is executable and the suite stays honest
about it.

## What this does and does not establish

**Does:** the current audit can false-positive, on an exact synthetic case,
through a mechanism identified in the code.

**Does not:** that all 387 national violations are false positives. Three
controls in the same file show the guard still catches real invented movements
— an exact shared node at the crossing, and a third road ending exactly on it —
and correctly stays silent when a nearby node connects only one of the two
partners. Some share of the 387 is likely genuine.

**Narrowing the radius without measuring the cohort would be premature**, which
is why nothing here changes it.

## Also fixed on this branch

The violation message named the pair in row order, flipping between `A x B` and
`B x A` with whichever source was read first. The verdict never depended on it,
so no graph was accepted or refused differently — but the violation list is the
only record of the cohort, and deduplicating it into unique physical places
cannot key on a string that changes with ingest order. Now emitted in canonical
order.

## What would settle it

One national run that dumps, per violation: the crossing coordinate, the shared
node coordinate, and **the exact distance between them**, banded at
`≤ 0.01 / 0.01–0.05 / 0.05–0.25 / 0.25–1.00 m`. Everything at or below the node
tolerance is a real violation; everything well above it is a candidate false
positive.

That is deliberately *not* done here: it needs a full national download, and
the finding does not block using the product. Tracked as an issue for the next
governed snapshot refresh.

## Why this is not urgent

The live application runs on the complete `2.0.0` national snapshot and is
unaffected. And a successful `2.1.0` ingest would node **zero** interior
crossings anyway, because the override table is empty — the crossing work is
evidence-gated, and there is no reviewed evidence yet. Fixing this guard would
unblock the pipeline; it would not change the map.
