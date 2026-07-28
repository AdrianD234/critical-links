# National ingest: incident record and pipeline follow-up

**Date:** 28 July 2026
**Outcome:** national snapshot `amds-national-2026-07-28-5b359d84` recovered and
serving. Roughly two and a half hours lost across three attempts.

This record exists because two of the three failures were caused by how the
recovery was conducted rather than by the data, and because the third exposed a
real design weakness that is still present.

---

## What happened

### Attempt 1 — ran 74 minutes, wrote nothing

`build_arc_transitions()` sat at 99.6% CPU with 1.2 GB RSS, no temp files, and
`arc_transitions` unchanged throughout. The whole ingest — download, junction
splitting, node assignment, and the COPY of 375,696 links and 731,286 arcs — was
inside one open transaction with it.

### Attempt 2 — never started

`pkill -f nzcl.ingest` killed the Python client. **PostgreSQL does not abort a
running query when its client disconnects**; it notices at the next write or
socket check, which a long CPU-bound join does not reach. Backend 2530 kept
executing for another hour holding its locks.

The replacement ingest launched, called `db.migrate()`, and blocked on those
locks for 28 minutes with a zero-byte log. Migration `003_coverage.sql` was
blocked behind the same locks and had not applied either.

### Attempt 3 — completed

After `pg_terminate_backend(2530)` and a genuinely fresh process, the ingest
completed. The preflight guard reported **1,851,262 expected transitions, worst
node 22 in / 22 out**, and the build produced 1,138,261 rows.

---

## Correction to the root-cause claim

An earlier report stated, with more confidence than the evidence supported, that
absent planner statistics after `COPY` caused a nested-loop cross product of
roughly 730,868² comparisons.

**That was not demonstrated, and testing contradicted it.** Reproducing the exact
no-statistics state on real Wellington arcs — a scratch copy with matching
indexes, `pg_statistic` rows deleted and `reltuples` reset — the planner chose a
**hash join either way**:

```
PLAN WITHOUT STATISTICS   Hash Join → Bitmap Heap Scan ×2
PLAN WITH STATISTICS      Hash Join → Seq Scan ×2
```

`ANALYZE` is retained after `COPY` because the estimates are used by everything
downstream, **not** because it was shown to be the fix. Which change made
attempt 3 succeed was never isolated, and this record does not claim otherwise.

### What *was* demonstrated

The transition join is quadratic in node degree. Its exact output size is

    expected transitions = Σ over nodes of (in-degree × out-degree)

and that is one cheap aggregate to compute. Measured:

| Snapshot   | Arcs    | Expected  | Built     | Worst node | Build time |
| ---------- | ------- | --------- | --------- | ---------- | ---------- |
| Wellington | 69,944  | 171,619   | 104,355   | 12 / 12    | 1.16 s     |
| Auckland   | 144,615 | 360,759   | 221,934   | 6 / 6      | 1.73 s     |
| National   | 731,286 | 1,851,262 | 1,138,261 | 22 / 22    | —          |

Near-linear. The function was never inherently slow, and the national graph was
never malformed — worst node 22 is an ordinary complex interchange.

The ingest now computes this estimate **before** building, prints it with the
worst in/out degree, and refuses above 10,000,000 with the five highest-degree
nodes and their NZTM coordinates named. A silent multi-hour hang becomes an
immediate failure with an address. **The threshold must not be raised to make an
ingest proceed** — exceeding it means node assignment has snapped unrelated arcs
together, and that is the thing to investigate.

---

## Process failures

Four claims were made before their postconditions were checked:

| Claimed             | Actually                                          |
| ------------------- | ------------------------------------------------- |
| migration applied   | DDL was blocked; psql exited after being cancelled |
| old run killed      | only the Python client; the backend ran on         |
| new ingest running  | blocked before its first meaningful step           |
| ANALYZE is the fix  | not demonstrated; testing contradicted it          |

Each cost real time and sent the next decision in the wrong direction.

**Operational actions are now reported in three parts** — command issued,
postcondition verified, conclusion drawn — with the verification query shown.
An action is not "done" until the database says so.

---

## The design weakness, still present

The ingest performs all of this in **one transaction**:

snapshot row → nodes → links → arcs → restrictions → **full transition
expansion** → counts → commit.

A failure in the last derived structure discards the national download, the
topology processing, and every loaded row. That is why a fault in an auxiliary
table for 43 turn restrictions destroyed 272,441 downloaded features, twice.

### Follow-up A — split core from derived

*Phase A*, committed: snapshot, nodes, links, arcs, restrictions, QA. Mark the
snapshot `core_complete`.

*Phase B*, separate and restartable: analyse, preflight, build transitions,
validate. Mark `complete`.

A Phase B failure then leaves a repairable national core graph in PostGIS
instead of nothing.

### Follow-up B — reuse the pinned raw download

A complete, sha256-pinned 272,441-feature national extract exists on disk at
`data/processed/amds-national-2026-07-27-4c601cfa/`, produced by the TypeScript
pipeline at processing version 1.0.0. The Python ingest has no import path for
it, so every rebuild re-contacts ArcGIS and re-downloads the country.

Acquisition, processing-version rebuild, and database load should be separable,
so a processing change rebuilds from the pinned raw source.

### Follow-up C — reassess `arc_transitions` entirely

AMDS publishes 43 resolvable turn restrictions nationally. The full edge-expanded
graph is 1.1 million rows built eagerly to serve a rare fallback, and the exact
profile-specific restriction automaton is already planned work that would likely
replace it.

Options to compare once the national snapshot is stable:

- eager full expansion (today);
- lazy construction of restricted-state transitions only;
- the planned restriction automaton directly;
- core snapshot first, restrictions as a separately versioned derived capability.

**The national core network should not fail to exist because an auxiliary
structure for sparse turn restrictions failed to build.**

---

## Also worth carrying forward

- **Detour latency is 1.1–3.0 s nationally** against ~180 ms on the Wellington
  pilot, on a graph ten times larger. Usable interactively; it needs attention
  before a national batch multiplies it across 375,696 links.
- **WSL localhost forwarding dropped twice** during this work, presenting as the
  API being unreachable from Windows while responding in 53 ms inside WSL.
  Environmental, but it wasted diagnosis time and is worth recognising quickly.
