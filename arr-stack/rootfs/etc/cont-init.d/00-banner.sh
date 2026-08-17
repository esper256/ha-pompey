#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

bashio::log.info "Arr Stack ${BUILD_VERSION:-0.1.0} starting"

VPN_TYPE="$(bashio::config 'vpn_type')"
HAS_WG=false
HAS_OVPN=false

if bashio::config.has_value 'wireguard_private_key'; then
  HAS_WG=true
fi
if bashio::config.has_value 'openvpn_user' && bashio::config.has_value 'openvpn_password'; then
  HAS_OVPN=true
fi

if [[ "${VPN_TYPE}" == "wireguard" && "${HAS_WG}" != "true" ]]; then
  bashio::exit.nok "vpn_type is wireguard but wireguard_private_key is empty. Paste PrivateKey from a Proton WireGuard config."
fi
if [[ "${VPN_TYPE}" == "openvpn" && "${HAS_OVPN}" != "true" ]]; then
  bashio::exit.nok "vpn_type is openvpn but openvpn_user/openvpn_password are empty."
fi

mkdir -p /config /data/gluetun /tmp/gluetun /run/nginx
chmod 700 /data/gluetun
