#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

bashio::log.level "$(bashio::config 'log_level')" >/dev/null 2>&1 || true

bashio::log.info "Pompey ${BUILD_VERSION:-0.2.2} starting"
pompey-status vpn "Starting" 5 || true

mkdir -p "${POMPEY_CONFIG}/wireguard" "${POMPEY_WG_ETC}" "${POMPEY_VPN_TMP}" "${POMPEY_NGINX_RUN}"
chmod 700 "${POMPEY_CONFIG}/wireguard" "${POMPEY_WG_ETC}"
pompey-secrets >/dev/null

WG_FILE="${POMPEY_CONFIG}/wireguard/$(bashio::config 'wireguard_config')"
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

if [[ "${POMPEY_FAKE_VPN:-}" == "1" ]]; then
  bashio::log.info "Fake VPN (agent/dev): no Proton required"
  pompey-status vpn "Fake wg0" 10 || true
  if [[ "${HAS_FILE}" != "true" && "${HAS_FIELDS}" != "true" ]]; then
    mkdir -p "${POMPEY_CONFIG}/wireguard"
    umask 077
    cat > "${WG_FILE}" <<'EOF'
[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:1
EOF
    HAS_FILE=true
    bashio::log.info "Fake VPN: wrote a stub WireGuard file (not Proton)"
  fi
fi

if [[ "${HAS_FILE}" != "true" && "${HAS_FIELDS}" != "true" ]]; then
  pompey-status vpn "Need a Proton WireGuard config" 5 "Put a Proton WireGuard file in the app config share, or fill private key, address, peer public key, and endpoint." || true
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

plex_url="$(bashio::config 'plex_url')"
if [[ -n "${plex_url}" ]]; then
  plex_host="$(python3 -c 'from urllib.parse import urlparse; import sys; print(urlparse(sys.argv[1]).hostname or "")' "${plex_url}" 2>/dev/null || true)"
  if [[ -n "${plex_host}" && ! "${plex_host}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    bashio::log.warning "Plex address uses hostname ${plex_host}. Proton DNS will not resolve LAN names — use a numeric IP (the machine that publishes port 32400)."
  fi
fi

bashio::log.info "After the tunnel is up, Pompey fetches the household UI and hidden engines. First start can take several minutes."
