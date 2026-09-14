#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

# A container restart can retain /tmp. Never release services using yesterday's
# VPN or engine-readiness markers.
rm -f "${POMPEY_READY}/vpn-up" "${POMPEY_READY}/vpn-applied" \
  "${POMPEY_READY}/engines-ready" "${POMPEY_READY}/wired" \
  "${POMPEY_READY}/arr-wired" "${POMPEY_READY}/seerr-arr" \
  "${POMPEY_READY}/recyclarr" "${POMPEY_READY}/health.json" "${POMPEY_READY}/status.json"
if [[ ! -f "${POMPEY_READY}/bootstrap-resolv.conf" ]]; then
  cp "${POMPEY_RESOLV}" "${POMPEY_READY}/bootstrap-resolv.conf"
fi

bashio::log.info "Pompey ${BUILD_VERSION:-0.3.0} starting"
pompey_status.py vpn "Starting" 5 || true

mkdir -p "${POMPEY_CONFIG}/wireguard" "${POMPEY_WG_ETC}" "${POMPEY_VPN_TMP}" "${POMPEY_NGINX_RUN}"
chmod 700 "${POMPEY_CONFIG}/wireguard" "${POMPEY_WG_ETC}"
pompey-secrets >/dev/null

if [[ "${POMPEY_FAKE_VPN:-}" == "1" ]]; then
  bashio::log.info "Fake VPN (agent/dev): no VPN required"
  pompey_status.py vpn "Fake wg0" 10 || true
  if [[ ! -s "${POMPEY_WG_FILE}" ]]; then
    umask 077
    cat > "${POMPEY_WG_FILE}" <<'EOF'
[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:1
EOF
    bashio::log.info "Fake VPN: wrote a stub WireGuard file (not VPN)"
  fi
fi

if [[ ! -s "${POMPEY_WG_FILE}" ]]; then
  POMPEY_STATUS_NEED_VPN=1 pompey_status.py vpn "Add your VPN configuration" 8 || true
  bashio::log.info "No VPN config yet. Start the app and paste the .conf you downloaded from VPN onto the wait screen."
else
  bashio::log.info "VPN WireGuard file is present"
fi

bashio::log.info "After the tunnel is up, Pompey fetches the household UI and hidden engines from this release’s verified bundle. First start can take several minutes."
