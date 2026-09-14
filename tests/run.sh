#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd "${ROOT}"

# Protocol traffic is prohibited. Source and downloader HTTP fixtures are
# permitted; only engine_manager stages qBittorrent, and no test starts it.
python3 tests/test_no_torrent_process.py

echo "== Home Assistant config.yaml (Supervisor SCHEMA_APP_CONFIG) =="
python3 tests/test_ha_config.py -v

echo "== python unittest (fake engines + supplied options.json) =="
python3 tests/test_python.py -v
python3 tests/test_contracts.py -v
python3 tests/test_firewall.py -v
python3 tests/test_fake_source.py -v
python3 tests/test_anime_fixtures.py -v

echo "== real Seerr (crane unpack + musl chroot; Arr/qbit stay fake) =="
if [[ "${POMPEY_REAL_SEERR:-0}" == "1" ]]; then
  python3 tests/test_seerr_real.py -v
else
  echo "skip: set POMPEY_REAL_SEERR=1 for the pinned Seerr image API contract"
fi

echo "== addon scripts with bashio stub =="
bash tests/test_scripts.sh

echo "== wg-quick contract + local handshake (no VPN) =="
bash tests/test_wg_quick.sh

echo "== pinned native artifact checks (glibc and musl) =="
bash tests/test_engine_unpack.sh
python3 tests/test_arr_integration.py -v

echo "== wait-screen preview (--once) =="
python3 tests/preview.py --once --port 18099
python3 tests/preview.py --once --debug --port 18100

echo "all tests passed"
