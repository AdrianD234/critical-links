#!/usr/bin/env bash
# The database-backed Python suite exactly as CI's browser job runs it:
# every requires_db test must EXECUTE, and pytest's own exit code is reported.
set -uo pipefail
cd "$(dirname "$0")/../../../python"
export PYTHONPATH="$PWD/src"
export NZCL_REQUIRE_NO_SKIPS=1
~/.venvs/nzcl/bin/python -m pytest -m "not realdata" \
  --junitxml=/tmp/db-tests.xml -q > /tmp/py-suite.txt 2>&1
code=$?
echo "PYTEST_EXIT=$code"
tail -25 /tmp/py-suite.txt
exit "$code"
