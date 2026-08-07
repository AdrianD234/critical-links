#!/usr/bin/env bash
# Run the timeout suite and report pytest's own exit code, never a pipe's.
set -uo pipefail
cd "$(dirname "$0")/../../../../python"
PYTHONPATH=src ~/.venvs/nzcl/bin/python -m pytest tests/test_corridor_timeout.py \
  -q -m 'not realdata' > "/tmp/$1.txt" 2>&1
code=$?
echo "PYTEST_EXIT=$code"
cat "/tmp/$1.txt"
exit "$code"
