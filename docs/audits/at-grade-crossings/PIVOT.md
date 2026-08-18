# The pivot: stop classifying, start overriding and testing sensitivity

**Status: specification. Adopted after the third holdout rejected broad
automatic noding on evidence — see `README.md` sections 13 and 14.**

The classifier is not being iterated. Four review packs have been spent, three
of them burned as development data, and the third holdout rejected the
approach in both directions at once: the permissive rules fabricate junctions
(32 of 350, including 5 from the only HIGH-confidence rule) and the
conservative rules sever real ones (11 of 25 sampled `DUPLICATE_GEOMETRY`
withdrawals). There is no threshold between those.

> **The classifier survives only as a candidate-RANKING score. It must never
> directly create a canonical graph node.**

---

## Mechanism 1 — evidence-backed overrides

A canonical junction is created **only** where a crossing carries explicit,
reviewable evidence:

| evidence kind | what it is |
| --- | --- |
| `AUTHORITATIVE_SOURCE` | an intersection or topology dataset that states this is a junction |
| `MANUAL_AERIAL_REVIEW` | a named reviewer confirmed it from imagery, on a date |
| `SOURCE_DATA_CORRECTION` | a formal correction accepted upstream |
| `PROJECT_OVERRIDE` | a durable project decision carrying reviewer, evidence and date |

Everything else stays **disconnected** in the canonical graph. Absence of
contrary evidence is not evidence, which is the whole lesson of
`ORDINARY_CROSSROADS`.

An override without a reviewer, an evidence reference and a date is not an
override. Two overrides that disagree about the same crossing **fail closed** —
the crossing stays disconnected and the conflict is reported. A gate with a way
round it is a recommendation.

## Mechanism 2 — per-analysis topology sensitivity

When a result looks suspicious — a large perimeter replacement route, a
`DISCONNECTED`, a bridge or isolation finding — identify the unresolved
crossings near the affected geometry and run **bounded counterfactuals**:

1. node one candidate, temporarily, on an isolated copy;
2. re-run the **same** movement;
3. measure whether the answer changes;
4. optionally test a very small pair, where one alone changes nothing;
5. report the exact assumed junctions.

**Do NOT build one national "possible" graph with every unresolved crossing
connected.** That is how a route comes to rely on a chain of speculative
motorway turns, each individually plausible and jointly absurd. The existing
`crossing_policy='possible'` graph is a sensitivity instrument and must not
become a published answer.

Target output shape:

> Topology-sensitive. The canonical represented route is **7.94 km**, but falls
> to **4.92 km** if the unresolved Clintons Road x McLaughlins Road crossing is
> an at-grade junction.

That is more useful than confidently publishing 7.94 km, and far more honest
than silently inventing a junction and publishing 4.92 km.

A counterfactual route is **never** drawn as the normal replacement path.

## Mechanism 3 — review the crossings that matter

Use the critical-links workflow itself to produce the queue:

- largest route reduction under one candidate junction;
- `DISCONNECTED` becoming `OK`;
- bridge becoming non-bridge;
- large physical-isolation change;
- later: traffic volume, state highway status, lifeline routes.

This turns "review 22,062 crossings" into "review the crossings that
materially change a real finding", which is a tractable amount of human
attention.

---

## How the work is split

**PR #7 is preserved as the research branch.** It is not merged as written and
it is not rewritten. It holds the Greendale counterfactual, crossing detection
and persistence, the what-if snapshot machinery, the mixed-place safeguards,
possible-route provenance, the promotion gate, the credential controls, and
the review tooling and evidence.

| PR | contents | explicitly NOT included |
| --- | --- | --- |
| **7A — topology audit and manual overrides** | crossing candidate table; evidence-backed override table; Greendale regression; what-if tooling | automatic ordinary-crossroads noding; any national snapshot rebuild |
| **7B — topology-sensitive analysis** | per-request candidate identification; one-at-a-time counterfactual routing; explicit assumption provenance; UI warning and alternative sensitivity result | any change to the canonical route; a counterfactual drawn as the teal replacement path |

## Required tests

Each of these is a behaviour, not a unit:

1. one crossing changes distance but not status;
2. one crossing changes `DISCONNECTED` to `OK`;
3. one crossing changes bridge to non-bridge;
4. two crossings jointly required, neither sufficient alone;
5. an equal-cost alternative makes a used crossing NON-decisive;
6. an unresolved motorway crossing never becomes canonical;
7. a manual confirmed override creates the expected node;
8. conflicting overrides fail closed;
9. a counterfactual route never appears as canonical;
10. no national possible graph is consulted.

The canonical/counterfactual separation is **mutation-tested**: break it
deliberately and show the tests fail, as was done for `routeTarget()`.

## PR #6 resumption gate — redefined and reachable

PR #6 no longer waits for every crossing in New Zealand to be resolved. It
resumes when all of:

- [ ] topology-sensitive analysis is wired into V2;
- [ ] Greendale is DETECTED as topology-sensitive;
- [ ] canonical and counterfactual answers are visually distinct;
- [ ] no topology-sensitive `DISCONNECTED` or isolation result is presented as
      definitive;
- [ ] browser, API, PostGIS and accessibility suites pass;
- [ ] the exact remote head is green.

## Standing constraints

Do not delete V1. Do not build an automatically noded 2.1.0 snapshot. Do not
run a national all-links criticality batch. Do not draw another broad
classifier holdout. Do not merge PR #7 as written. No history rewrite.
