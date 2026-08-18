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
| `span_corridor.py` | *(next)* Which road between A and B the outage runs along |

`corridor.py` is used **read-only** for continuity evidence. It is not edited
on this branch, and its ranking rules are not copied — two implementations of
that hierarchy would drift. Deliberate extraction into a shared module can
happen during integration, once PR #11 is settled.

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
