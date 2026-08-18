# Two-point outage span — working constraints

Draft feature. A user places two handles on a road and the engine closes
exactly the stretch between them, then measures the replacement path.

This branch is built on `origin/main` and is deliberately **not** stacked on
PR #10 or PR #11. No code from either is used, and no file either is changing
has been edited.

---

## Constraints this branch works under

| | |
| --- | --- |
| API port | **8002** (the main worktree uses 8000) |
| Vite port | **5174** (the main worktree uses 5173) |
| Database | **`nzcl_outage_span`**, not the shared `nzcl` |
| Feature flag | `VITE_ENABLE_OUTAGE_SPAN_EDITOR=1` — off by default |
| Migrations | **No numbered migration** until PR #10's are settled |
| Platform | Desktop only. No mobile work on this branch |
| Merge | **Do not merge.** Integration only after PR #10/#11 settle |

Interaction rules that follow from the above:

- No full analysis during pointer movement. Drag updates the snapped point and
  the red preview locally; analysis runs on release or after a debounce, with
  stale requests cancelled.
- Permalink and Back/Forward must restore a span, which is why a handle is
  stored as a linear reference rather than as a click.

---

## Local environment

The shared development venv installs `nzcl` editable against whichever
worktree was provisioned first — on this machine, the **main** worktree, which
sits on a different branch. A plain `pytest` here therefore imports, tests and
serves *another branch's code*, and nothing about it looks wrong: the suite
collects, passes, and reports on code nobody is editing.

So run everything through the launcher:

```bash
cd python && ../scripts/outage-span-env.sh pytest -q
```

It pins the interpreter, the `PYTHONPATH` and the database, then asserts
`nzcl` resolved inside this worktree and refuses to run if it did not.
`python/tests/conftest.py` carries the same assertion, so pytest is guarded
even when invoked directly.

Run pytest from `python/`, not the repository root: `addopts = -m 'not
realdata'` lives in `python/pyproject.toml` and pytest only reads it when that
directory is the rootdir. From the root, the real-data tests are selected
instead of deselected and fail against any database without a national
snapshot — which looks like seven broken tests and is really a misplaced
working directory.

One-off setup, if the venv or database is missing:

```bash
python3 -m venv ~/.venvs/nzcl-outage-span
~/.venvs/nzcl-outage-span/bin/pip install -e '<worktree>/python[dev]'
createdb -h 127.0.0.1 -U nzcl -O nzcl nzcl_outage_span
psql -h 127.0.0.1 -U nzcl -d nzcl_outage_span \
  -c 'CREATE EXTENSION postgis; CREATE EXTENSION pgrouting;'
```

Unique fixture snapshot ids are **not** sufficient isolation on their own:
migrations, `DELETE` cleanups, shared tables and the fixed
`ci-fixture-wellington` snapshot name are all global to a database.

---

## Where the pieces live

| Module | Answers |
| --- | --- |
| `snap.py` | Where did the user point, on which centreline, and which links legitimately host that point |
| `vids.py` | Every identifier that exists only inside one request |
| `vsplit.py` | What exactly is closed, as a request-local graph |
| `span_corridor.py` | Which road between A and B the outage runs along |
| `outage.py` | How much further you have to go, and what may be said about it |
| `api_outage.py` | The HTTP surface, off unless enabled |

`corridor.py` is used **read-only** for continuity evidence — `Continuity`,
with its ranking hierarchy and the argument for the ordering. It is not edited
on this branch and its rules are not copied; two implementations of that
hierarchy would drift the first time either was tuned. Deliberate extraction
into a shared module can happen during integration, once PR #11 is settled.

---

## API

Three GET endpoints under `/api/v2/outage`, mounted only when
`enable_outage_span_api` is set.

```bash
curl "localhost:8002/api/v2/outage/snap?lon=174.77&lat=-41.29"
```

Returns a linear reference plus two kinds of rival, kept apart deliberately:
`equivalentHosts` are at the same coordinate on different links (a crossroads —
not a question for the user, but every one must be handed back), and
`alternatives` are somewhere else (a divided carriageway — what `ambiguous` is
computed from).

```bash
curl "localhost:8002/api/v2/outage/corridor?aLink=1810&aFraction=0.5&bLink=1811&bFraction=0.5"
```

Up to three corridors, ranked on evidence, with `ambiguous` set when the
evidence does not separate the top two. Pass `aAlt=linkId:fraction` (repeatable)
to carry the equivalent hosts through.

```bash
curl "localhost:8002/api/v2/outage/analysis?aLink=1810&aFraction=0.5&bLink=1811&bFraction=0.5&direction=both&geometry=true"
```

Closes the span and measures the way round. `corridorId` pins the corridor and
is what a permalink carries; an id no longer among the candidates is a **409**,
never a quiet substitution.

---

## Measured latency

National snapshot, 375,696 links / 731,286 arcs, median of repeated runs.

| Operation | Median |
| --- | --- |
| `snap` | **1.1 ms** |
| `corridor`, two handles ~400 m apart | **7–25 ms** |
| `analysis`, one direction | ~500 ms |
| `analysis`, both directions | ~1,000 ms |

Corridor selection was 4,200 ms before the search was bounded to a box around
the two handles.

The ~500 ms per direction is the replacement-path search itself: each
`pgr_dijkstra` call reloads the whole national edge set, which is the same cost
every other measure in this system pays. It cannot be bounded the way corridor
selection is — a replacement path legitimately goes anywhere, and one measured
here ran 237 km. A `both` request could be halved by measuring the two
directions in one edge-set load; that needs overlay support in
`routing.route_many_paths` and has not been done.

---

## Known gaps

**Turn restrictions on a split link.** A route crossing a banned manoeuvre
while an overlay is in force returns `TURN_RESTRICTION_UNSUPPORTED` rather than
being offered as legal. The expanded graph is built from `arc_transitions`,
which has no rows for pieces invented this request.

This is fail-closed and fires only on an *actual* violation of the route taken,
so an unrelated published restriction does not affect a span — asserted
directly in `test_vsplit_invariants.py`. AMDS publishes 60 restrictions
nationally, and if their mere presence were disqualifying, every outage in the
country would come back unresolved.

The exact remapping is tractable and is a **product-readiness requirement**,
not a limit of the approach: an original arc becomes an ordered chain of
pieces, transitions entering the link map to the first piece, transitions
leaving map from the last, internal piece-to-piece movements are permitted, and
restrictions naming neither split endpoint are unchanged.

**Isolation is not computed for a partial span.** `physical.py` answers "what
is cut off" on Gu, an undirected graph with one edge per *link*, which cannot
represent half of one. Posing a partial span to it would mean rounding the
outage up to whole links — the exact error this feature removes. So `isolation`
is `null` with the reason in the payload, rather than the field quietly
missing. Extending Gu to carry request-local split edges belongs in the
integration pass.

**No result caching.** See the API note above: it needs a numbered migration.

**No bound on how much road a span may close.** Two handles far apart on one
route will happily close hundreds of kilometres — a 452 km corridor was
produced during benchmarking, and it took 5 s. Whether to cap this, and at what
length, is a product decision rather than an engineering one, so nothing has
been invented here. The corridor search is bounded; the *span* is not.

**No UI.** The map interaction, drag behaviour, permalink restoration and
Back/Forward handling are not built. The backend contracts they need — linear
references, corridor pinning, `permalink` in every analysis response — are in
place and tested.
