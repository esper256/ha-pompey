#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd "${ROOT}"

echo "== python unittest (fake engines + supplied options.json) =="
python3 tests/test_python.py -v

echo "== addon scripts with bashio stub =="
bash tests/test_scripts.sh

echo "== wait-screen preview (--once) =="
python3 tests/preview.py --once --port 18099

echo "== fake wg0 smoke (skip if this VM cannot create veth) =="
bash tests/test_dev_vpn.sh

echo "all tests passed"
