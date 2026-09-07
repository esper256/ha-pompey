#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

INGRESS_PORT="$(bashio::addon.ingress_port)"
INGRESS_CONF="${NGINX_INGRESS_CONF:-/etc/nginx/http.d/ingress.conf}"
DEBUG_INC="${NGINX_DEBUG_INC:-$(dirname "${INGRESS_CONF}")/ingress-debug.inc}"
WWW="${POMPEY_WWW:-/usr/share/pompey}"

export POMPEY_WWW="${WWW}"
mkdir -p "$(dirname "${DEBUG_INC}")"
write-debug-ingress "${DEBUG_INC}"
sed -i \
  -e "s/%%port%%/${INGRESS_PORT}/g" \
  -e "s|%%debug_inc%%|${DEBUG_INC}|g" \
  "${INGRESS_CONF}"

if bashio::config.true debug; then
  bashio::log.info "Ingress nginx will listen on port ${INGRESS_PORT} (Supervisor 172.30.32.2 only). Debug consoles are on."
else
  bashio::log.info "Ingress nginx will listen on port ${INGRESS_PORT} (Supervisor 172.30.32.2 only)"
fi
