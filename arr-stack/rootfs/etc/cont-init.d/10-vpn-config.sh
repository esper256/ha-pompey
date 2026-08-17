#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

CONF_NAME="$(bashio::config 'wireguard_config')"
SRC="/config/wireguard/${CONF_NAME}"
DST=/etc/wireguard/wg0.conf
DNS="$(bashio::config 'wireguard_dns')"

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

# Proton DNS lives on the tunnel. Fail closed if wg0 is down.
printf 'nameserver %s\n' "${DNS}" >/etc/resolv.conf

printf '%s\n' "$(bashio::config 'lan_networks')" >/etc/arr-stack-lan-networks
vpn-killswitch "${DST}"
bashio::log.info "iptables kill switch applied (internet OUTPUT only via wg0 once it exists)"
