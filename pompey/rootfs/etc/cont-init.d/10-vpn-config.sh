#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"

CONF_NAME="$(bashio::config 'wireguard_config')"
SRC="${POMPEY_CONFIG}/wireguard/${CONF_NAME}"
DST="${POMPEY_WG_CONF}"
DNS="$(bashio::config 'wireguard_dns')"

mkdir -p "$(dirname "${DST}")" "$(dirname "${POMPEY_LAN_FILE}")" "$(dirname "${POMPEY_RESOLV}")"

if [[ -s "${SRC}" ]]; then
  tr -d '\r' < "${SRC}" > "${DST}"
  bashio::log.info "Using WireGuard config file ${SRC}"
else
  umask 077
  cat > "${DST}" <<EOF
[Interface]
PrivateKey = $(bashio::config 'wireguard_private_key')
Address = $(bashio::config 'wireguard_address')
DNS = ${DNS}

[Peer]
PublicKey = $(bashio::config 'wireguard_peer_public_key')
AllowedIPs = 0.0.0.0/0
Endpoint = $(bashio::config 'wireguard_endpoint')
PersistentKeepalive = 25
EOF
  bashio::log.info "Wrote WireGuard config from Home Assistant options (secrets not logged)"
fi

chmod 600 "${DST}"

if ! grep -qi '^[[:space:]]*PersistentKeepalive' "${DST}"; then
  printf '\nPersistentKeepalive = 25\n' >>"${DST}"
  bashio::log.info "Added PersistentKeepalive=25 so the Proton peer stays up behind NAT"
fi

# Resolve Endpoint while Home Assistant DNS still works. After we point
# resolv.conf at Proton 10.2.0.1, a hostname cannot be looked up until wg0
# exists — and wg-quick needs that Endpoint to create wg0.
resolved="$(python3 - "${DST}" <<'PY'
import re
import socket
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
match = re.search(r"(?im)^(\s*Endpoint\s*=\s*)(\S+)\s*$", text)
if not match:
    raise SystemExit(0)
prefix, value = match.group(1), match.group(2)
if value.startswith("["):
    raise SystemExit(0)
host, sep, port = value.rpartition(":")
if not sep or not port.isdigit():
    raise SystemExit(0)
parts = host.split(".")
if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts if p):
    raise SystemExit(0)
try:
    ip = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
except OSError as exc:
    print(f"Could not resolve WireGuard Endpoint host {host}: {exc}", file=sys.stderr)
    raise SystemExit(1)
open(path, "w", encoding="utf-8").write(
    text[: match.start()] + prefix + f"{ip}:{port}" + text[match.end() :]
)
print(f"{host} -> {ip}")
PY
)" || bashio::exit.nok "WireGuard Endpoint hostname could not be resolved before the kill switch. Use an IPv4 Endpoint in the Proton file."
if [[ -n "${resolved}" ]]; then
  bashio::log.info "Resolved WireGuard Endpoint ${resolved} (needed before Proton DNS / kill switch)"
fi

printf '%s\n' "$(bashio::config 'lan_networks')" >"${POMPEY_LAN_FILE}"

if [[ "${POMPEY_FAKE_VPN:-}" == "1" ]]; then
  # Keep host DNS. Do not DROP OUTPUT on this VM (that would kill the agent).
  bashio::log.info "Fake VPN: skip Proton DNS rewrite and OUTPUT kill switch"
else
  # Proton DNS lives on the tunnel. Fail closed if wg0 is down.
  printf 'nameserver %s\n' "${DNS}" >"${POMPEY_RESOLV}"
  vpn-killswitch "${DST}"
  bashio::log.info "iptables kill switch applied (internet OUTPUT only via wg0 once it exists)"
fi
