#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

INGRESS_PORT="$(bashio::addon.ingress_port)"
INGRESS_CONF="${NGINX_INGRESS_CONF:-/etc/nginx/http.d/ingress.conf}"
sed -i "s/%%port%%/${INGRESS_PORT}/g" "${INGRESS_CONF}"

bashio::log.info "Ingress nginx will listen on port ${INGRESS_PORT} (Supervisor 172.30.32.2 only)"
