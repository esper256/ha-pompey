#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

INGRESS_PORT="${INGRESS_PORT:-8099}"
INGRESS_CONF="${NGINX_INGRESS_CONF:-/etc/nginx/http.d/ingress.conf}"
DEBUG_INC="${NGINX_DEBUG_INC:-$(dirname "${INGRESS_CONF}")/ingress-debug.inc}"
WWW="${POMPEY_WWW:-/usr/share/pompey}"

export POMPEY_WWW="${WWW}"
mkdir -p "$(dirname "${DEBUG_INC}")"
write_debug_ingress.py "${DEBUG_INC}"
sed -i \
  -e "s/%%port%%/${INGRESS_PORT}/g" \
  -e "s|%%debug_inc%%|${DEBUG_INC}|g" \
  "${INGRESS_CONF}"

bashio::log.info "Application interfaces available through authenticated Home Assistant Ingress on port ${INGRESS_PORT}"
