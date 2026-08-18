#!/usr/bin/env bash
#
# Run a command against THIS worktree's environment, or refuse to run at all.
#
# WHY THIS EXISTS
# ---------------
# The shared development venv installs `nzcl` editable against whichever
# worktree was set up first. On this machine that is the MAIN worktree, which
# sits on a different branch. So a plain `pytest` here imports, tests and
# serves someone else's code while appearing to work perfectly: the suite is
# green, the API starts, and none of it is this branch.
#
# `PYTHONPATH=...` in front of each command fixes it, and forgetting the prefix
# once - on a subprocess, a script, a Uvicorn launch - silently reintroduces
# it. So the fix is structural rather than remembered: a dedicated venv, plus
# an assertion that refuses to proceed if `nzcl` resolved anywhere else.
#
# It also pins the database. Unique fixture snapshot ids stop two agents
# colliding on rows, but they do not isolate migrations, `DELETE` cleanups,
# shared tables, or the fixed `ci-fixture-wellington` snapshot name - all of
# which are global to a database.
#
# USAGE
#   cd python && ../scripts/outage-span-env.sh pytest -q
#   scripts/outage-span-env.sh python -m nzcl.cli ...
#   scripts/outage-span-env.sh uvicorn nzcl.api:app --port 8002
#
# Run pytest from `python/`, not from the repository root. The suite's
# `addopts = -m 'not realdata'` lives in python/pyproject.toml, and pytest only
# reads it when that directory is the rootdir. From the root the real-data
# tests are selected instead of deselected, and they fail against any database
# without a national snapshot ingested - which looks like seven broken tests
# and is really a misplaced working directory. This script resolves its own
# location, so calling it from `python/` is safe.
#
# ESCAPE HATCHES (deliberate, and noisy)
#   NZCL_OUTAGE_VENV=...      use a different interpreter
#   NZCL_ALLOW_SHARED_DB=1    permit the shared `nzcl` database
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${NZCL_OUTAGE_VENV:-$HOME/.venvs/nzcl-outage-span}"
PY="$VENV/bin/python"

if [[ ! -x "$PY" ]]; then
    cat >&2 <<EOF
No interpreter at $PY

This worktree wants its own environment so it cannot import another branch's
code. Create it with:

  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -e "$HERE/python[dev]"
EOF
    exit 2
fi

# Belt and braces: the dedicated venv already resolves `nzcl` here, and this
# makes it true even if someone points NZCL_OUTAGE_VENV at a shared one.
export PYTHONPATH="$HERE/python/src${PYTHONPATH:+:$PYTHONPATH}"

# Isolated by default. The ports match the rest of this feature's tooling:
# API 8002 and Vite 5174, so a dev server here cannot answer requests meant
# for the main worktree's 8000/5173.
export DATABASE_URL="${DATABASE_URL:-postgresql://nzcl:nzcl_local_dev@127.0.0.1:5432/nzcl_outage_span}"
export API_PORT="${API_PORT:-8002}"

"$PY" - "$HERE" <<'GUARD'
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()

try:
    import nzcl
except ImportError as exc:  # pragma: no cover - environment failure
    sys.exit(f"refusing to run: nzcl could not be imported at all ({exc})")

module = pathlib.Path(nzcl.__file__).resolve()
if root not in module.parents:
    sys.exit(
        "refusing to run: `nzcl` resolved OUTSIDE this worktree.\n"
        f"  worktree: {root}\n"
        f"  imported: {module}\n"
        "Whatever ran next would have tested or served another branch's code."
    )

url = os.environ.get("DATABASE_URL", "")
database = url.rsplit("/", 1)[-1].split("?", 1)[0]
if database == "nzcl" and os.environ.get("NZCL_ALLOW_SHARED_DB") != "1":
    sys.exit(
        "refusing to run: DATABASE_URL points at the shared `nzcl` database.\n"
        "Migrations, cleanup and the fixed `ci-fixture-wellington` snapshot are\n"
        "global to a database, so parallel work collides there regardless of\n"
        "how test snapshot ids are named. Set NZCL_ALLOW_SHARED_DB=1 to override."
    )
GUARD

export PATH="$VENV/bin:$PATH"
exec "$@"
