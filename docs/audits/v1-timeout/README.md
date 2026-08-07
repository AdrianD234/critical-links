# V1 reports a cancelled corridor search as a finding about the network

**Status:** reproduced against a real database and a real browser, then fixed on
`fix/v1-timeout-is-unresolved`.
**Engine:** V1 — the currently shipped default. Not V2.

---

## The defect in one sentence

`routing.route_many`, the multi-target search behind V1's corridor measure,
returns a bare `{}` both when the search completed and found no pair *and* when
PostgreSQL cancelled it, so `detour._corridor` cannot tell the two apart and
reports "no route to target" for a query that never finished.

Original code, `python/src/nzcl/routing.py`:

```python
    except Exception:  # noqa: BLE001 - caller falls back to per-pair routing
        return {}
```

The comment is also wrong about its own caller: `detour._corridor` does not fall
back to per-pair routing. It reads an absent pair as "no route" and continues to
`return Corridor("DISCONNECTED", ...)`.

---

## Reproduction

Everything below is a real PostgreSQL `statement_timeout` cancellation on a real
PostGIS + pgRouting 3.6.1 database. Nothing is stubbed, patched or simulated.

```
cd python && PYTHONPATH=src python ../docs/audits/v1-timeout/reproduce.py
```

Full transcript: [`observed-before.txt`](observed-before.txt) (before the fix),
[`observed-after.txt`](observed-after.txt) (after).

### The fixture

A 16x16 two-way street grid — the ballast, which is what makes a multi-target
query cost real time — plus a one-way bypass that leaves the grid and rejoins
it:

```
(0,0) --CONN_W--> (0,-400) ==EB1==> (300,-400) ==EB2==> (600,-400) --CONN_E--> (1100,0)
  grid              400 m    one-way   300 m    one-way    300 m      640.3 m     grid
                                                [closed]
```

484 links, 259 nodes, 966 arcs. Closing **EB2** leaves its start node with no
outgoing arc, so the *endpoint* measure — is there a path from the closed link's
start back to its own end — is `DISCONNECTED`. That is correct and routine; it
is the same shape as a state-highway carriageway, where the endpoint question is
ill-posed. It is why the corridor measure exists.

The *through* trip is fine. The grid carries it: 1500.0 m instead of 1240.3 m
between `(0,-400)` and `(1100,0)`, a penalty of **+259.7 m**.

### Step 1 — what the database does

```
raised psycopg.errors.QueryCanceled: canceling statement due to statement timeout

routing.route_many with the same 1 ms budget:
  -> {}   (3.7 ms)
routing.route_many with a 60 s budget:
  -> 66564 pairs (413.4 ms)
```

A search that found nothing and a search that never finished are the same value.

### Step 2 — what the V1 corridor search makes of it

Same network, same closure, same code. Only the budget changes.

| `statement_timeout` | corridor status | detail | penalty |
|---|---|---|---|
| 60 000 ms | `OK` | — | 259.69 m |
| 20 ms | `OK` | — | 259.69 m |
| 5 ms | `DISCONNECTED` | `search space exhausted with no route to target` | `None` |
| 1 ms | `DISCONNECTED` | `search space exhausted with no route to target` | `None` |

`"search space exhausted with no route to target"` is a claim about the road
network. The search space was not exhausted. The clock ran out.

### Step 3 — what the running product tells a user

The whole product, end to end: real FastAPI service, real database, real
Chromium. The only thing changed is the statement-timeout budget, squeezed to
5 ms — enough for the single-pair endpoint searches (~0.8 ms) and not for the
34-source corridor search (~9.8 ms). That is exactly the shape a loaded database
produces.

Captured responses: [`api-response-adequate-budget.json`](api-response-adequate-budget.json),
[`api-response-corridor-timed-out.json`](api-response-corridor-timed-out.json),
and after the fix
[`api-response-corridor-timed-out-after-fix.json`](api-response-corridor-timed-out-after-fix.json).

|  | adequate budget | corridor cancelled |
|---|---|---|
| `forward.status` | `DISCONNECTED` | `DISCONNECTED` |
| `forward.corridor.status` | `OK` | `DISCONNECTED` |
| `forward.corridor.penaltyM` | `259.7` | `null` |
| `qualityFlags` | …`ENDPOINT_MEASURE_UNDEFINED_CORRIDOR_USED` | flag absent |
| status pill | No replacement path in the represented network | No replacement path in the represented network |
| **headline** | **Added distance — through trip: +260 m** | **NO REPLACEMENT PATH** |

Rendered headline, cancelled corridor
([`ui-before-fix-full.png`](ui-before-fix-full.png)):

> **NO REPLACEMENT PATH**
> With this modelled closure, no path exists between the selected link's own
> endpoints, and no through-trip comparison could be computed. Nothing is
> stranded — this measures the link's endpoints, not the surrounding area.

Rendered headline, same closure, adequate budget
([`ui-truth-adequate-budget-full.png`](ui-truth-adequate-budget-full.png)):

> **ADDED DISTANCE — THROUGH TRIP  +260 m**
> This is a one-way carriageway, so there is no path from the link's end back to
> its start and the endpoint measure is undefined. Measured instead between the
> nearest points upstream and downstream at which a driver has a choice.

Two things make this worse than a missing number.

`ResultStatus.tsx` classifies `DISCONNECTED` as a **finding** — amber, styled
deliberately so it "must not look like an error" — and faults as neutral
charcoal with the status code shown. A cancelled query is dressed as the most
important result the tool produces.

And the API attaches this to the corridor field:

> Through-trip comparison between the nearest upstream and downstream points at
> which a driver has a choice.

There was no comparison. The user is told traffic cannot get past a closure it
can get past with an extra 260 m.

---

## The fix

Three changes, and nothing else.

1. **`python/src/nzcl/routing.py`** — `route_many` returns `ManyCostResult`
   (`status` + `costs`) instead of a bare mapping. A statement timeout maps to
   `UNRESOLVED_TIMEOUT`, any other database failure to `API_ERROR`, exactly as
   the single-pair `route` has always done a few lines above. `costs` is the
   same mapping as before, built the same way.

2. **`python/src/nzcl/detour.py`** — `_corridor` returns a `Corridor` carrying
   that status when the search did not resolve, instead of falling through to
   `DISCONNECTED`. When the search *did* resolve, an absent pair still means "no
   route" and the code below is untouched.

3. **`apps/web/src/inspector/ResultView.tsx`** — the headline for an unresolved
   result is **"Analysis unresolved"**. It covers both the direction-level fault
   (previously the vaguer "No result") and the new case: endpoint measure
   `DISCONNECTED`, corridor unresolved, where the honest statement is that the
   through trip is unknown rather than absent.

Not changed: corridor selection, the probe schedule, closure semantics, the
isolation measure, `SOLE_ACCESS`, or the meaning of `DISCONNECTED` anywhere it
is reached by a search that finished.

The status *pill* still reads "No replacement path in the represented network",
because that is about the endpoint measure, which resolved and genuinely found
none. Only the headline — the through-trip question, the one the reader actually
has — becomes "Analysis unresolved".

### After

[`observed-after.txt`](observed-after.txt), same fixture, same budgets:

| `statement_timeout` | corridor status | detail | penalty |
|---|---|---|---|
| 60 000 ms | `OK` | — | 259.69 m |
| 20 ms | `OK` | — | 259.69 m |
| 5 ms | `UNRESOLVED_TIMEOUT` | `statement timeout after 5 ms` | `None` |
| 1 ms | `UNRESOLVED_TIMEOUT` | `statement timeout after 1 ms` | `None` |

And in the browser ([`ui-after-fix-full.png`](ui-after-fix-full.png)), same
5 ms squeeze, same closure:

> **ANALYSIS UNRESOLVED**
> The endpoint measure found no path from the link's start back to its own end,
> which is routine on a one-way carriageway. The through-trip comparison that
> would say whether traffic still gets past did not finish, so whether it does
> is unknown. This is not a finding that the road is cut off.

## Mutation verification

`python/tests/test_corridor_timeout.py` has to fail on the defect, not on the
refactor. [`mutation/mutate.mjs`](mutation/mutate.mjs) puts back the behaviour
the fix removed — `route_many` swallowing every database failure and returning
an empty result that still looks resolved — while leaving the type in place, so
the tests fail on their assertions rather than on an `AttributeError`.

| | pytest exit | result |
|---|---|---|
| [old behaviour restored](mutation/with-old-behaviour.txt) | `1` | 4 failed, 7 passed |
| [with the fix](mutation/with-the-fix.txt) | `0` | 11 passed |

The load-bearing failure:

```
>       assert cancelled.status == "UNRESOLVED_TIMEOUT"
E       AssertionError: assert 'DISCONNECTED' == 'UNRESOLVED_TIMEOUT'
```

The seven that pass under *both* are the point of the other half: the
genuinely-no-route cases still report `DISCONNECTED`, so this is not a
relabelling of every negative result.

## Byte-identity

Every ordinary response is unchanged, and shown to be rather than asserted. See
[`identity/RESULT.txt`](identity/RESULT.txt): 92 detour responses across two
snapshots, captured from the running API before and after, `diff -r` clean and
sha256-identical.

## Reproducing the browser capture

```
python docs/audits/v1-timeout/ui_fixture.py build          # load at Wellington coords
bash   docs/audits/v1-timeout/start-api.sh 5               # squeeze; "none" for adequate
API_PORT=8010 npm run dev --workspace apps/web -- --port 5174
node   docs/audits/v1-timeout/capture-ui.mjs before-fix
```

## Gates, run locally before the branch was pushed

| gate | command | exit | result |
|---|---|---|---|
| Types | `npm run typecheck` | `0` | — |
| Unit | `npx vitest run` | `0` | 150 passed, 16 skipped (the integration suite, which needs a running API) |
| Build | `npm run build` | `0` | `dist/index.html`, `dist/assets`, 15 bundled `.woff2` |
| Python, PostGIS-backed | [`run-python-suite.sh`](run-python-suite.sh) with `NZCL_REQUIRE_NO_SKIPS=1` | `0` | **329 passed, 0 skipped** |
| Executed-contracts gate | CI's own script against that run | `0` | [`python-suite-gate.txt`](python-suite-gate.txt) — `test_corridor_timeout` contributed 11 executed tests |
| Browser + accessibility | `npx playwright test` | `0` | [`playwright.txt`](playwright.txt) — 67 passed across desktop, laptop, mobile-smoke and the axe scan |

No test command was piped anywhere that could substitute a consumer's exit
status for the runner's; every exit code above is the runner's own.
