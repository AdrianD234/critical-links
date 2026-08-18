# At-grade crossings: what was tried, what was measured, and what ships

`split_at_junctions()` split where one link's *endpoint* landed on another
link's interior, and **never** where two interiors crossed, on the inference
that "neither road ends here, therefore it is grade separated". That inference
is false for a flat rural grid, and it costs real distance: closing 675.3 m of
Greendale Road near Darfield returns a **7,944.4 m** replacement path, where
the same request with the missing crossing noded returns **4,915.5 m** — 38.1%
shorter.

The obvious fix was to classify every interior crossing automatically and node
the ones that came back AT_GRADE. **That was built, measured against a gate
declared in advance, and rejected.** This branch carries what survived.

> **This is the compact record.** The full investigation — 1,600 lines of
> audit, four review packs, the holdout answer key, 440 rendered cards, the
> per-card verdicts and the scratch tooling — is preserved on the research
> branch in **PR #7**, `feature/at-grade-crossing-topology` at
> **`c45724d0eadb90d4ac168b581f46e1a1e1214615`**. It is referenced rather than
> copied, because merging 34,000 lines of evidence for a rejected strategy
> alongside the mechanism that replaces it would bury the thing that needs
> reviewing.

---

## 1. The finding: broad automatic noding was tested and rejected

This is the most valuable thing the research produced, and it is a finding
rather than an apology.

**A strategy was proposed, specified, built, and measured against a bar fixed
in writing before the measurement existed. It failed the bar. It is not
shipping.** That is a gate doing its job. The failure mode it prevented — a
national road graph with thousands of junctions that are not on the ground,
published as fact to people deciding which roads matter when one closes — is
considerably more expensive than the work it cost to find out.

### What was tested

That the disposition of an interior-interior road crossing can be decided
automatically, at national scale, from AMDS source attributes plus geometric
heuristics, accurately enough to create canonical graph nodes without a human
looking at each one.

### The bar, fixed before the measurement

Committed before a single card was drawn: **350 AT_GRADE cards, at most 4
failures, unreviewable counted among the failures**, four conditions each able
to veto alone, encoded as `nzcl/promotion.py` with a test per condition.

### The result

| condition | requirement | observed | |
| --- | --- | --- | --- |
| zero confirmed grade-separated false positives | 0 | **0** | **MET** |
| zero confirmed not-a-junction false positives | 0 | **32** | **NOT MET** |
| 95% Wilson lower bound on AT_GRADE precision | >= 97% | **86.1%** | **NOT MET** |
| unreviewable counted among the failures | method | 4, counted | MET |

n = 350. **314 confirmed, 32 contradicted, 4 unreviewable.** Tolerance was 4
failures; there were 36. Reviewed by **four freshly spawned reviewers** on
disjoint 110-card subsets, with no access to the repository, the classifier,
the answer key or each other's work.

Machine-readable summary: `holdout3-summary.json`.

These are figures for performance on a deliberately difficult, stratified
holdout. This is **not** a probability sample and **not** an estimate or formal
lower bound on national precision.

### Why more thresholds would not have fixed it

The evidence rejects the approach in **both** directions at once, which is the
part that matters:

| | |
| --- | --- |
| **the permissive side fabricates junctions** | 32 of 350 AT_GRADE cards are not junctions at all. `JUNCTION_WITNESS` — the *only* HIGH-confidence at-grade rule, the one backed by positive evidence rather than by absence — produced **5 of them**. |
| **the conservative side severs real ones** | `DUPLICATE_GEOMETRY` fires on **9,830** crossings nationally, and reviewers judged **11 of 25** sampled withdrawals to be genuine junctions. `MOTORWAY_CARRIAGEWAY` was called at-grade on 11 of 16. |

There is no threshold between those two. Tightening the at-grade rules
withdraws more real junctions; loosening the withdrawal rules creates more
false nodes. Both errors are already large, and they are large **at the same
settings**.

A further round of rules would be fitted to the review packs — of which four
have now been spent, three burned as development data — producing a classifier
tuned to its own test set rather than a reliable national topology model.

**The conclusion is about the evidence available, not the effort applied.**
AMDS attributes plus geometry do not carry enough information to decide,
unsupervised, whether two crossing centrelines are a junction. The z-values are
a LiDAR terrain drape — known grade-separated motorway crossings come back
0.00–0.09 m apart. The shared-vertex signal appears on 15.4% of crossings.
Topo50 structures recover about 45% of grade-separation candidates on a
motorway where the answer is already known.

### The number that makes the pivot obviously right

The canonical graph has **4,914** AT_GRADE crossing points the classifier would
have noded. At the measured rate, roughly **one in eleven** is not a junction —
on the order of **450 fabricated nodes nationally**, each joining a road to
itself or to nothing.

Reviewing 4,914 crossings to catch 450 is not tractable. Shipping the 450 is
not an option. Ranking them so a human reviews the ones that change an answer
is.

### What survives, and it is not nothing

- **The defect is real and quantified** — the Greendale counterfactual above.
  The causal crossing is Clintons x McLaughlins, not the one in the original
  report, which changes nothing when noded alone.
- **The expensive failure mode did not occur.** Zero grade-separated false
  positives on 350 adversarially chosen cards.
- **`audit_no_invented_movements()`** asserts the safety property rather than
  reasoning about it, and the ingest refuses to load on a violation.
- **Mixed places** are refused under every policy, so noding one pair at an
  interchange cannot hand a grade-separated third road the same movements.
- **The what-if machinery** copies a snapshot, edits it and drops it.
- **The promotion gate as code**, after a summary reported `met: true` about a
  result that did not meet it.
- **A scorer that cannot drop a row** (`nzcl/holdout.py`), after noticing that
  an incomplete review would shrink the denominator instead of counting
  missing verdicts as failures.
- **Credential controls in CI**, after the LINZ key was committed twice.

### What the classifier may still be used for

**Candidate ranking, and nothing else.** A score that orders which unresolved
crossings are worth a human's attention is a good use of a signal that is right
about nine times in ten. Creating a canonical node is not.

> **The classifier must never directly create a canonical graph node.**

---

## 2. What ships instead

See `PIVOT.md` for the full specification. In this branch:

**Evidence-backed overrides.** A canonical junction exists at an interior
crossing where a `crossing_overrides` row says so, and nowhere else. Every row
carries a decision, one of four evidence kinds, a checkable reference, a named
reviewer and a date. `crossing_policy="evidence"` is the default and honours no
classifier disposition at all.

**Fails closed twice.** No override means no node. Overrides that *disagree*
mean no node either, and the conflict is reported — every tempting tie-break
resolves a disagreement between two reviewers by a rule neither of them agreed
to.

The classifier still runs, and its verdict is still recorded on every crossing,
because it is the input to the review queue. It decides nothing.
