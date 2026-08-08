#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../../python"
PYTHONPATH=src ~/.venvs/nzcl/bin/python ../docs/audits/v1-timeout/reproduce.py \
  > "../docs/audits/v1-timeout/$1" 2>&1
