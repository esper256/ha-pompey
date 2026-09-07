#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

INGRESS_PORT="$(bashio::addon.ingress_port)"
INGRESS_CONF="${NGINX_INGRESS_CONF:-/etc/nginx/http.d/ingress.conf}"
DEBUG_INC="${NGINX_DEBUG_INC:-$(dirname "${INGRESS_CONF}")/ingress-debug.inc}"
MODULES_CONF="${NGINX_MODULES_CONF:-/etc/nginx/modules-enabled.conf}"
WWW="${POMPEY_WWW:-/usr/share/pompey}"
SUB_SO="${NGINX_SUB_MODULE:-/usr/lib/nginx/modules/ngx_http_sub_module.so}"

export POMPEY_WWW="${WWW}"
write-debug-ingress "${DEBUG_INC}"
sed -i \
  -e "s/%%port%%/${INGRESS_PORT}/g" \
  -e "s|%%debug_inc%%|${DEBUG_INC}|g" \
  "${INGRESS_CONF}"

mkdir -p "$(dirname "${MODULES_CONF}")" "$(dirname "${DEBUG_INC}")"
if [[ -f "${SUB_SO}" ]]; then
  printf 'load_module %s;\n' "${SUB_SO}" >"${MODULES_CONF}"
else
  printf '# http_sub is compiled in, or debug rewrite is unused.\n' >"${MODULES_CONF}"
fi

if bashio::config.true debug; then
  bashio::log.info "Ingress nginx will listen on port ${INGRESS_PORT} (Supervisor 172.30.32.2 only). Debug consoles are on."
else
  bashio::log.info "Ingress nginx will listen on port ${INGRESS_PORT} (Supervisor 172.30.32.2 only)"
fi
