#!/usr/bin/env bash
# Real Arr import/API contracts; torrent operations remain HTTP fakes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1 POMPEY_REAL_ENGINES=1
python3 "${ROOT}/tests/test_arr_integration.py" -v
