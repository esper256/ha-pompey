#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# Runtime VPN configuration is owned by the supervised WireGuard service.
# shellcheck source=/dev/null
source "$(command -v pompey-env)"
if [[ ! -s "${POMPEY_WG_FILE}" ]]; then
  POMPEY_STATUS_NEED_VPN=1 pompey_status.py vpn "Paste your VPN WireGuard configuration" 8
fi
