#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

apply-vpn-config && rc=0 || rc=$?
if [[ "${rc}" -eq 0 ]]; then
  bashio::log.info "Using WireGuard config file ${POMPEY_WG_FILE}"
  exit 0
fi
if [[ "${rc}" -eq 2 ]]; then
  POMPEY_STATUS_NEED_PROTON=1 pompey-status vpn "Paste the Proton WireGuard file you downloaded" 8 || true
  bashio::log.info "Waiting for a Proton WireGuard config on the wait screen"
  exit 0
fi
POMPEY_STATUS_NEED_PROTON=1 pompey-status vpn "Paste the Proton WireGuard file you downloaded" 8 "That Proton file is missing Endpoint or keys. Paste the whole .conf you downloaded." || true
bashio::log.warning "Proton WireGuard file is present but not usable; paste a complete .conf on the wait screen"
exit 0
