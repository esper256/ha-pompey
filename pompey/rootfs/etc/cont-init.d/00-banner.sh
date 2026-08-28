#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source /usr/local/bin/pompey-env

bashio::log.info "Pompey ${BUILD_VERSION:-0.2.0} starting"

mkdir -p /config/wireguard /etc/wireguard /tmp/vpn /run/nginx
chmod 700 /config/wireguard /etc/wireguard
pompey-secrets >/dev/null

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

plex_token="$(bashio::config 'plex_token')"
if [[ -z "${plex_token}" ]]; then
  bashio::log.warning "Plex token is empty. Search can still start; you will finish Plex from that screen."
fi

indexer_url="$(bashio::config 'indexer_url')"
if [[ -z "${indexer_url}" ]]; then
  bashio::log.warning "No source URL yet. Add a source (URL plus key) so titles can be found."
fi

bashio::log.info "After the tunnel is up, Pompey fetches the household UI and hidden engines. First start can take several minutes."
