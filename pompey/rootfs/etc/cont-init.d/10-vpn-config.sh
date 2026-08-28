#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

CONF_NAME="$(bashio::config 'wireguard_config')"
SRC="${POMPEY_CONFIG}/wireguard/${CONF_NAME}"
DST="${POMPEY_WG_CONF}"
DNS="$(bashio::config 'wireguard_dns')"

mkdir -p "$(dirname "${DST}")" "$(dirname "${POMPEY_LAN_FILE}")" "$(dirname "${POMPEY_RESOLV}")"

if [[ -s "${SRC}" ]]; then
  tr -d '\r' < "${SRC}" > "${DST}"
  bashio::log.info "Using WireGuard config file ${SRC}"
else
  umask 077
  cat > "${DST}" <<EOF
[Interface]
PrivateKey = $(bashio::config 'wireguard_private_key')
Address = $(bashio::config 'wireguard_address')
DNS = ${DNS}

[Peer]
PublicKey = $(bashio::config 'wireguard_peer_public_key')
AllowedIPs = 0.0.0.0/0
Endpoint = $(bashio::config 'wireguard_endpoint')
EOF
  bashio::log.info "Wrote WireGuard config from Home Assistant options (secrets not logged)"
fi

chmod 600 "${DST}"

printf '%s\n' "$(bashio::config 'lan_networks')" >"${POMPEY_LAN_FILE}"

if [[ "${POMPEY_FAKE_VPN:-}" == "1" ]]; then
  # Keep host DNS. Do not DROP OUTPUT on this VM (that would kill the agent).
  bashio::log.info "Fake VPN: skip Proton DNS rewrite and OUTPUT kill switch"
else
  # Proton DNS lives on the tunnel. Fail closed if wg0 is down.
  printf 'nameserver %s\n' "${DNS}" >"${POMPEY_RESOLV}"
  vpn-killswitch "${DST}"
  bashio::log.info "iptables kill switch applied (internet OUTPUT only via wg0 once it exists)"
fi
