#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"
rm -f "${POMPEY_READY}"/{vpn-up,vpn-applied,seerr-arr,downloads-wired,roots-wired,health.json,status.json,configuration.json}
mkdir -p /run/pompey-private
chmod 700 /run/pompey-private
if [[ ! -f /run/pompey-private/bootstrap-resolv.conf ]]; then
  cp "${POMPEY_RESOLV}" /run/pompey-private/bootstrap-resolv.conf
fi
# Install the baseline drop policy before any application process starts.
python3 "$(command -v vpn_firewall.py)" | nft -f -
printf 'nameserver 127.0.0.1\n' > "${POMPEY_RESOLV}"
chmod 755 "${POMPEY_READY}"
pompey-secrets >/dev/null
bashio::log.info "Pompey ${BUILD_VERSION:-0.4.1}: starting bundled applications"
pompey_status.py start "Starting applications" 20 || true
