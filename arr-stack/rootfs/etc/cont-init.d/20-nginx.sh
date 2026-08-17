#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

INGRESS_PORT="$(bashio::addon.ingress_port)"
sed -i "s/%%port%%/${INGRESS_PORT}/g" /etc/nginx/http.d/ingress.conf

mkdir -p /usr/share/arr-stack
render-launcher >/usr/share/arr-stack/index.html

bashio::log.info "Ingress nginx will listen on port ${INGRESS_PORT} (Supervisor 172.30.32.2 only)"
