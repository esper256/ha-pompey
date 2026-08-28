#!/usr/bin/env bash
# Local WireGuard client + server. No Proton. No BitTorrent.
#
# Always: apply-vpn-config on a Proton-shaped .conf, then prove the runtime
# file is something `wg addconf` will accept (catches Table=off after [Peer]).
# When this VM has sudo + kernel WireGuard: bring wg0 up in a netns against a
# generated peer, with the same PATH helpers HAOS needs (read-only sysctl,
# broken resolvconf), and check a handshake.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${ROOT}/pompey/rootfs/usr/local/bin"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pompey-wg-quick.XXXXXX")"
NS_SRV="pompey-wgs"
NS_CLI="pompey-wgc"
cleanup() {
  sudo -n ip netns exec "${NS_CLI}" wg-quick down "${POMPEY_WG_CONF:-/dev/null}" >/dev/null 2>&1 || true
  sudo -n ip netns del "${NS_SRV}" >/dev/null 2>&1 || true
  sudo -n ip netns del "${NS_CLI}" >/dev/null 2>&1 || true
  rm -rf "${WORK}"
}
trap cleanup EXIT

export BASHIO_OPTIONS="${ROOT}/tests/options.json"
export POMPEY_CONFIG="${WORK}/config"
export POMPEY_DATA="${WORK}/data/pompey"
export POMPEY_ENGINES="${WORK}/data/engines"
export POMPEY_SECRETS="${POMPEY_DATA}/secrets.json"
export POMPEY_READY="${WORK}/tmp/pompey"
export POMPEY_WG_ETC="${WORK}/etc/wireguard"
export POMPEY_WG_CONF="${POMPEY_WG_ETC}/wg0.conf"
export POMPEY_VPN_TMP="${WORK}/tmp/vpn"
export POMPEY_LAN_FILE="${WORK}/etc/pompey-lan-networks"
export POMPEY_RESOLV="${WORK}/etc/resolv.conf"
export MEDIA_ROOT="${WORK}/media"
export IPTABLES_LOG="${WORK}/iptables.log"
mkdir -p "${WORK}/bin" "${POMPEY_WG_ETC}" "${POMPEY_CONFIG}/wireguard" "${MEDIA_ROOT}"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/iptables"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/ip6tables"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/iptables-nft"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/ip6tables-nft"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/iptables-legacy"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/ip6tables-legacy"
chmod +x "${WORK}/bin/"* "${ROOT}/tests/with-bashio"
for f in "${BIN}"/*; do
  [[ -f "${f}" ]] || continue
  cmd="$(basename "${f}")"
  head="$(head -n1 "${f}")"
  if [[ "${head}" == *bashio* && "${cmd}" != "pompey-env" ]]; then
    printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "${f}" >"${WORK}/bin/${cmd}"
    chmod +x "${WORK}/bin/${cmd}"
  fi
done
export PATH="${WORK}/bin:${BIN}:${PATH}"

run() {
  "${ROOT}/tests/with-bashio" "$@"
}

echo "== runtime conf must be something wg addconf will accept =="
cp "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf"
{
  printf '%s\n' "PostUp = iptables -I OUTPUT ! -o %i -j REJECT"
  printf '%s\n' "AllowedIPs = 0.0.0.0/0, ::/0"
} >>"${POMPEY_CONFIG}/wireguard/wg0.conf"
run "${BIN}/apply-vpn-config"
python3 "${ROOT}/tests/lib/wg_quick_contract.py" "${POMPEY_WG_CONF}"
if command -v wg-quick >/dev/null 2>&1; then
  wg-quick strip "${POMPEY_WG_CONF}" >"${WORK}/stripped.conf"
  python3 "${ROOT}/tests/lib/wg_quick_contract.py" "${WORK}/stripped.conf"
fi

can_live=1
if ! command -v wg >/dev/null 2>&1 || ! command -v wg-quick >/dev/null 2>&1; then
  echo "skip live handshake: install wireguard-tools to exercise wg-quick without Proton"
  can_live=0
fi
if [[ "${can_live}" -eq 1 ]] && ! sudo -n true 2>/dev/null; then
  echo "skip live handshake: no passwordless sudo"
  can_live=0
fi
if [[ "${can_live}" -eq 1 ]]; then
  if sudo -n ip link add pompey-wgt type wireguard 2>/dev/null; then
    sudo -n ip link del pompey-wgt >/dev/null 2>&1 || true
  else
    echo "skip live handshake: no kernel WireGuard / NET_ADMIN"
    can_live=0
  fi
fi

if [[ "${can_live}" -ne 1 ]]; then
  echo "wg-quick contract ok (no live handshake on this VM)"
  exit 0
fi

echo "== generated WireGuard server + client handshake (not Proton) =="
umask 077
srv_priv="$(wg genkey)"
cli_priv="$(wg genkey)"
srv_pub="$(wg pubkey <<<"${srv_priv}")"
cli_pub="$(wg pubkey <<<"${cli_priv}")"

sudo -n ip netns add "${NS_SRV}"
sudo -n ip netns add "${NS_CLI}"
sudo -n ip link add pwgs type veth peer name pwgc
sudo -n ip link set pwgs netns "${NS_SRV}"
sudo -n ip link set pwgc netns "${NS_CLI}"
sudo -n ip netns exec "${NS_SRV}" ip addr add 192.0.2.1/24 dev pwgs
sudo -n ip netns exec "${NS_SRV}" ip link set pwgs up
sudo -n ip netns exec "${NS_SRV}" ip link set lo up
sudo -n ip netns exec "${NS_CLI}" ip addr add 192.0.2.2/24 dev pwgc
sudo -n ip netns exec "${NS_CLI}" ip link set pwgc up
sudo -n ip netns exec "${NS_CLI}" ip link set lo up
sudo -n ip netns exec "${NS_CLI}" ip route add default via 192.0.2.1

printf '%s\n' "${srv_priv}" >"${WORK}/srv.priv"
chmod 600 "${WORK}/srv.priv"

sudo -n ip netns exec "${NS_SRV}" ip link add wg-srv type wireguard
sudo -n ip netns exec "${NS_SRV}" wg set wg-srv listen-port 51820 \
  private-key "${WORK}/srv.priv" \
  peer "${cli_pub}" allowed-ips 10.2.0.2/32
sudo -n ip netns exec "${NS_SRV}" ip addr add 10.2.0.1/32 dev wg-srv
sudo -n ip netns exec "${NS_SRV}" ip link set wg-srv up

cat >"${POMPEY_CONFIG}/wireguard/wg0.conf" <<EOF
[Interface]
PrivateKey = ${cli_priv}
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = ${srv_pub}
AllowedIPs = 0.0.0.0/0
Endpoint = 192.0.2.1:51820
PostUp = iptables -I OUTPUT ! -o %i -j REJECT
EOF
run "${BIN}/apply-vpn-config"
python3 "${ROOT}/tests/lib/wg_quick_contract.py" "${POMPEY_WG_CONF}"

HELPERS="${ROOT}/pompey/rootfs/usr/local/bin/pompey-wg"
log="$(sudo -n ip netns exec "${NS_CLI}" \
  env PATH="${HELPERS}:${PATH}" \
  wg-quick up "${POMPEY_WG_CONF}" 2>&1)" || {
  printf '%s\n' "${log}" >&2
  echo "wg-quick up failed in netns (do not send keys)" >&2
  exit 1
}
printf '%s\n' "${log}"
if grep -qiE 'unrecognized|signature mismatch|Read-only file system' <<<"${log}"; then
  echo "wg-quick still hit an HAOS-class failure" >&2
  exit 1
fi
sudo -n ip netns exec "${NS_CLI}" ip link show wg0 | grep -q '[,<]UP[,>]'

# /32 addresses need an explicit route on both sides. Table=off adds none.
sudo -n ip netns exec "${NS_CLI}" ip route replace 10.2.0.1/32 dev wg0
sudo -n ip netns exec "${NS_SRV}" ip route replace 10.2.0.2/32 dev wg-srv
sudo -n ip netns exec "${NS_CLI}" sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null 2>&1 || true
sudo -n ip netns exec "${NS_SRV}" sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null 2>&1 || true

if ! sudo -n ip netns exec "${NS_CLI}" ping -c 1 -W 3 10.2.0.1 >/dev/null; then
  echo "no ping to generated WireGuard server (handshake or routing)" >&2
  sudo -n ip netns exec "${NS_CLI}" wg show wg0 >&2 || true
  exit 1
fi
hs="$(sudo -n ip netns exec "${NS_CLI}" wg show wg0 latest-handshakes | awk '{ print $2 }')"
if [[ -z "${hs}" || "${hs}" == "0" ]]; then
  echo "WireGuard handshake did not complete against the generated server" >&2
  exit 1
fi
echo "generated WireGuard handshake ok (not Proton)"
