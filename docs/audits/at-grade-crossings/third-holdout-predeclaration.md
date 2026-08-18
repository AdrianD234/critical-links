# Third holdout: sample size and stopping rule, declared before the draw

**Status: DECLARED. No cards have been drawn. No verdicts exist.**

This file is committed before `holdout3_review.py build` is run for the first
time. That ordering is the whole point of it. Choosing `n` after seeing a bound
come in short — "another fifty cards would settle it" — turns a gate into
something that can always eventually be passed, and it is the same class of
error as tuning a classifier against its own holdout. Two samples have already
been burned that way. This one is decided in advance, in writing, in the
repository.

---

## What the gate is

Encoded in `nzcl/promotion.py` and pinned by `tests/test_promotion.py`. All
four conditions must hold:

| condition | requirement |
| --- | --- |
| zero confirmed grade-separated false positives | must be 0 |
| zero confirmed not-a-junction false positives | must be 0 |
| 95% lower bound on AT_GRADE precision | must be >= 97% |
| unreviewable cards | counted as **failures**, not excluded |

`met` is the AND of all four. There is no override and no waiver.

---

## The declaration

| | |
| --- | --- |
| **AT_GRADE cards drawn** | **350** |
| **maximum tolerated failures** | **4** |
| what counts as a failure | a contradiction of ANY kind, **plus** every unreviewable card |
| lower bound at exactly 4 failures | 97.10% — clears 97% |
| lower bound at 5 failures | 96.70% — **fails** |

The minimum `n` for four tolerated failures is 339
(`required_sample_size(4)`). 350 is drawn instead of 339 so the declaration is
not sitting exactly on its own boundary, and so a handful of cards that cannot
be rendered do not silently move the arithmetic.

Decoys are drawn on top of this and are **not** part of `n`: the gate is about
AT_GRADE precision, and a pack that is entirely AT_GRADE tells the reviewer the
answer before they look. Roughly 90 GRADE_SEPARATED and UNRESOLVED cards are
included for that reason, and they are scored and reported separately.

### The stopping rule

1. Draw 350 AT_GRADE cards. Review all of them.
2. Evaluate `promotion.evaluate()` on the result.
3. If it fails, **stop and report the failure.** Do not draw more cards, do not
   re-review the unclear ones hoping for a different answer, and do not change
   a classifier rule and re-score this pack. A fourth pack, drawn independently
   of all three previous ones, is the price of changing the classifier again.
4. If it passes, the rebuild MAY be recommended. It is still Adrian's decision.

---

## The risk this declaration is taking, stated before the result

Four tolerated failures on 350 cards is a **1.14% failure rate**, and
unreviewable cards count against it. The 248-card holdout had **4 unreviewable
in 196 AT_GRADE cards — 2.0%** — which on 350 cards would be about 7, and 7
failures does not clear 97% at any `n` below roughly 1,070.

So it is entirely possible that this pack fails on imagery quality rather than
on classifier quality. That is a real risk and it is being accepted with its
eyes open, because the alternative — deciding after the fact that unreviewable
cards should be excluded — is precisely the move that made the previous gate
report a pass it had not earned. Excluding them measures precision on the
subset that was easiest to see, which is the subset least likely to contain the
errors.

Two things are done to reduce the rate honestly, and both are decided now
rather than after seeing a result:

- cards are rendered at **zoom 19** and **512 px** rather than zoom 18 and
  400 px, so the reviewer is not guessing from four pixels of carriageway;
- "unclear" stays available and stays a failure. The remedy for an unreadable
  card is a better rendering, never a softer rule.

If the pack fails on unreviewable cards alone, that is a finding about the
evidence available for validating this classifier, and it belongs in the report
as such — not as an argument for moving the gate.

---

## Independence of the draw

Excluded before drawing, exactly as the second holdout excluded the first:

- every crossing in the **208-card** development pack;
- every crossing in the **248-card** second holdout;
- every crossing in the earlier unblinded review pack;
- every candidate **within 50 m** of any of the above.

The 50 m rule is the one doing the work. One physical intersection of two
divided carriageways produces several crossing points, so excluding recorded
crossings alone would still admit the same place wearing a different id. The
project's own definition of one place is 25 m; 50 m is used so a holdout card
cannot even be a near neighbour of a burned one.

---

## The population being sampled has changed, and that is deliberate

The previous packs were drawn from `classified.jsonl`, which was built in SQL
over the **links** table — the graph after splitting — with a crossing angle
measured over a fraction of each line's length and one row per crossing PAIR.
The classifier that ships runs over AMDS **source features**, before any graph
exists, with a fixed 10 m angle window and one row per crossing POINT.

Those are different populations, and the difference is not academic: two of the
three AT_GRADE contradictions in the 248-card holdout are artefacts of the
record rather than errors of the classifier. See `classify_national_v2.py`.

This pack is drawn from `classified-v2.jsonl`, which is produced by the same
calls `topology.split_at_junctions` makes, in the same order. A holdout drawn
from anything else measures something that is not shipped.

---

## Strata

Aimed at the rules as they now stand, not at the ones the previous packs
tested. Every cell over-weights a way the classifier could be wrong, so the
result is ~~a conservative floor rather than an estimate of national
precision~~ **performance on a deliberately difficult, stratified holdout —
not a probability sample, and not an estimate or formal lower bound on
national precision** — and the pack says so rather than offering a
population-weighted number it cannot support.

> **Correction, made before any card was reviewed and recorded here rather
> than made silently.** The struck words are the ones this file was committed
> with. They claimed too much and the claim is withdrawn.
>
> "Conservative floor" is a statement about the *population*: it says national
> precision is at least this much. Earning that needs a probability sample and
> population weights. This draw has neither — the cells overlap, they were
> chosen precisely because they are hard, and no weights exist. Deliberately
> over-weighting hard cases makes a result *likely* to come in below national
> precision; it does not make it a bound, and the two are not the same claim.
>
> The Wilson interval computed by `nzcl.promotion.evaluate` applies to this
> holdout's reviewed cases. It is not a nationally weighted figure and must
> not be quoted as one.
>
> Nothing that governs the outcome has moved: n is still 350, the tolerated
> failures are still 4, unreviewable still counts as a failure, and the four
> gate conditions are unchanged. This corrects how the result may be
> *described*, not what it has to clear.

| cell | why it exists |
| --- | --- |
| angle 30-40 deg | immediately above the tangential threshold |
| angle 40-60 / 60-80 / 80-90 deg | the rest of the range, for contrast |
| structure 25-70 m away | immediately outside the widened structure radius |
| same name, DUPLICATE_GEOMETRY did not fire | the survivors of the duplicate rule |
| **withdrawn only by the corridor walk** | the new rule's own false-positive risk: 85 crossings nationally moved out of AT_GRADE by `corridor_polyline`, and if it over-fires these are where |
| junction witness | the only HIGH-confidence AT_GRADE rule |
| unsealed access | the source's best proxy for forestry, industrial and private tracks |
| unnamed both / state highway / urban / rural | coverage |
| imagery year | the oldest band that actually exists (2019+) |

Decoy cells cover `STRUCTURE_MAPPED` and `NAMED_STRUCTURE` — the two surviving
GRADE_SEPARATED rules — and a sample of UNRESOLVED, including the crossings
`MOTORWAY_CARRIAGEWAY` now leaves unresolved rather than asserting.

---

## Review standard

The second review must be performed by a **fresh isolated agent** with no
previous transcript, no access to the classifier source, no prior verdicts and
no score summary, on anonymous randomised cards. If that review cannot be
completed to that standard, **the 2.1.0 rebuild does not proceed** and the
blockage is reported. An honest statement that it was unavailable is required,
and it is not a waiver.
