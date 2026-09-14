#!/usr/bin/env bash
# Command boundary checks use temporary configuration and never touch host networking.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export ROOT
python3 "${ROOT}/tests/test_shell_boundaries.py"
