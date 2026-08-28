# Stub of Home Assistant's bashio, driven by Supervisor's /data/options.json
# (or BASHIO_OPTIONS). Enough of the API for Pompey's scripts.
# shellcheck shell=bash

bashio::config() {
  local key="$1"
  local file="${BASHIO_OPTIONS:-/data/options.json}"
  if [[ ! -f "${file}" ]]; then
    printf ''
    return 0
  fi
  jq -r --arg k "${key}" '.[$k] // empty | if type=="boolean" then (if . then "true" else "false" end) else tostring end' "${file}"
}

bashio::config.has_value() {
  local val
  val="$(bashio::config "$1")"
  [[ -n "${val}" && "${val}" != "null" ]]
}

bashio::config.true() {
  local val
  val="$(bashio::config "$1")"
  [[ "${val}" == "true" || "${val}" == "1" || "${val}" == "yes" ]]
}

bashio::log.info() { printf 'INFO: %s\n' "$*"; }
bashio::log.warning() { printf 'WARNING: %s\n' "$*" >&2; }
bashio::log.error() { printf 'ERROR: %s\n' "$*" >&2; }

bashio::exit.nok() {
  bashio::log.error "$*"
  exit 1
}

bashio::addon.ingress_port() {
  printf '%s' "${INGRESS_PORT:-8099}"
}
