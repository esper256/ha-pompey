#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd "${ROOT}"

echo "== tests must not involve BitTorrent =="
# Patterns live only in this file. --exclude keeps this guard from matching itself.
if hits="$(grep -RInE --include='*.py' --include='*.sh' --include='*.json' --include='*.md' \
  --exclude='run.sh' \
  '\.torrent\b|application/x-bittorrent|make_torrent|magnet:|torrents/info|announce-list|webseed|MoviesSearch|confirm-legal-notice|searchForMovie.: True' \
  tests)"; then
  printf '%s\n' "${hits}" >&2
  echo "tests must not wait on or speak the BitTorrent protocol" >&2
  exit 1
fi
if [[ -e tests/dev/torznab.py ]]; then
  echo "tests/dev/torznab.py is a torrent fixture; it must not exist" >&2
  exit 1
fi

echo "== Home Assistant config.yaml (Supervisor SCHEMA_APP_CONFIG) =="
python3 tests/test_ha_config.py -v

echo "== python unittest (fake engines + supplied options.json) =="
python3 tests/test_python.py -v

echo "== addon scripts with bashio stub =="
bash tests/test_scripts.sh

echo "== wait-screen preview (--once) =="
python3 tests/preview.py --once --port 18099

echo "== fake wg0 smoke (skip if this VM cannot create veth) =="
bash tests/test_dev_vpn.sh

echo "all tests passed"
