#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

bashio::log.info "Pompey ${BUILD_VERSION:-0.2.31} starting"
pompey-status vpn "Starting" 5 || true

mkdir -p "${POMPEY_CONFIG}/wireguard" "${POMPEY_WG_ETC}" "${POMPEY_VPN_TMP}" "${POMPEY_NGINX_RUN}"
chmod 700 "${POMPEY_CONFIG}/wireguard" "${POMPEY_WG_ETC}"
pompey-secrets >/dev/null

if [[ "${POMPEY_FAKE_VPN:-}" == "1" ]]; then
  bashio::log.info "Fake VPN (agent/dev): no Proton required"
  pompey-status vpn "Fake wg0" 10 || true
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
    bashio::log.info "Fake VPN: wrote a stub WireGuard file (not Proton)"
  fi
fi

if [[ ! -s "${POMPEY_WG_FILE}" ]]; then
  POMPEY_STATUS_NEED_PROTON=1 pompey-status vpn "Paste the Proton WireGuard file you downloaded" 8 || true
  bashio::log.info "No Proton config yet. Start the app and paste the .conf you downloaded from Proton onto the wait screen."
else
  bashio::log.info "Proton WireGuard file is present"
fi

bashio::log.info "After the tunnel is up, Pompey fetches the household UI and hidden engines. First start can take several minutes."
