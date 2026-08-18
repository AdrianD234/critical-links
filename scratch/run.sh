#!/usr/bin/env bash
set -euo pipefail
cd "/mnt/c/Users/Adrian Desilvestro/Repos/Critical links-topology/python"
export PYTHONPATH="$PWD/src"
exec "$HOME/.venvs/nzcl/bin/python" "$@"
