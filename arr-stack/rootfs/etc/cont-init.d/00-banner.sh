#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

bashio::log.info "Arr Stack ${BUILD_VERSION:-0.1.1} starting"

mkdir -p /config/wireguard /etc/wireguard /tmp/vpn /run/nginx
chmod 700 /config/wireguard /etc/wireguard

WG_FILE="/config/wireguard/$(bashio::config 'wireguard_config')"
HAS_FILE=false
HAS_FIELDS=false

if [[ -s "${WG_FILE}" ]]; then
  HAS_FILE=true
fi
if bashio::config.has_value 'wireguard_private_key' \
  && bashio::config.has_value 'wireguard_address' \
  && bashio::config.has_value 'wireguard_peer_public_key' \
  && bashio::config.has_value 'wireguard_endpoint'; then
  HAS_FIELDS=true
fi

if [[ "${HAS_FILE}" != "true" && "${HAS_FIELDS}" != "true" ]]; then
  bashio::exit.nok "Need a Proton WireGuard config: put it at ${WG_FILE} or fill private key, address, peer public key, and endpoint."
fi
