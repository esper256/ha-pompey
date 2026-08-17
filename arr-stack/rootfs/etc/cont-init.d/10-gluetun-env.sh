#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

ENV_FILE=/etc/gluetun.env
umask 077

VPN_TYPE="$(bashio::config 'vpn_type')"
COUNTRIES="$(bashio::config 'server_countries')"
LOG_LEVEL="$(bashio::config 'log_level')"
LAN_NETWORKS="$(bashio::config 'lan_networks')"
# Supervisor hassio network — Ingress and supervisor DNS. Never omit.
HASS_NET="172.30.32.0/23"
OUTBOUND="${HASS_NET},${LAN_NETWORKS}"

{
  printf 'VPN_SERVICE_PROVIDER=%q\n' protonvpn
  printf 'VPN_TYPE=%q\n' "${VPN_TYPE}"
  printf 'SERVER_COUNTRIES=%q\n' "${COUNTRIES}"
  printf 'LOG_LEVEL=%q\n' "${LOG_LEVEL}"
  printf 'FIREWALL_ENABLED_DISABLING_IT_SHOOTS_YOU_IN_YOUR_FOOT=%q\n' on
  printf 'FIREWALL_OUTBOUND_SUBNETS=%q\n' "${OUTBOUND}"
  printf 'HEALTH_SERVER_ADDRESS=%q\n' '127.0.0.1:9999'
  printf 'HTTP_CONTROL_SERVER_ADDRESS=%q\n' '127.0.0.1:8000'
  printf 'DNS_SERVER=%q\n' on
  printf 'DNS_UPSTREAM_RESOLVER_TYPE=%q\n' DoT
  printf 'STORAGE_SERVERS_DIRECTORY_PATH=%q\n' /data/gluetun
  printf 'PUBLICIP_FILE=%q\n' /tmp/gluetun/ip
  printf 'VPN_PORT_FORWARDING_STATUS_FILE=%q\n' /tmp/gluetun/forwarded_port
  printf 'UPDATER_PERIOD=%q\n' 24h
  printf 'TZ=%q\n' "${TZ:-UTC}"
} > "${ENV_FILE}"

if bashio::config.true 'port_forwarding'; then
  printf 'VPN_PORT_FORWARDING=%q\n' on >> "${ENV_FILE}"
  printf 'PORT_FORWARD_ONLY=%q\n' on >> "${ENV_FILE}"
else
  printf 'VPN_PORT_FORWARDING=%q\n' off >> "${ENV_FILE}"
fi

if [[ "${VPN_TYPE}" == "wireguard" ]]; then
  KEY_FILE=/data/gluetun/wg_private_key
  printf '%s' "$(bashio::config 'wireguard_private_key')" > "${KEY_FILE}"
  chmod 600 "${KEY_FILE}"
  printf 'WIREGUARD_PRIVATE_KEY_SECRETFILE=%q\n' "${KEY_FILE}" >> "${ENV_FILE}"
else
  USER_FILE=/data/gluetun/openvpn_user
  PASS_FILE=/data/gluetun/openvpn_password
  printf '%s' "$(bashio::config 'openvpn_user')" > "${USER_FILE}"
  printf '%s' "$(bashio::config 'openvpn_password')" > "${PASS_FILE}"
  chmod 600 "${USER_FILE}" "${PASS_FILE}"
  printf 'OPENVPN_USER_SECRETFILE=%q\n' "${USER_FILE}" >> "${ENV_FILE}"
  printf 'OPENVPN_PASSWORD_SECRETFILE=%q\n' "${PASS_FILE}" >> "${ENV_FILE}"
fi

chmod 600 "${ENV_FILE}"
bashio::log.info "Gluetun environment written (secrets not logged)"
