#!/usr/bin/env bash
# Fast smoke test: fake wg0 veth NATs out the default adapter.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/sbin:/sbin:${PATH}"
VPN="${ROOT}/pompey/rootfs/usr/local/bin/pompey-dev-vpn"
export POMPEY_FAKE_VPN_STATE="${TMPDIR:-/tmp}/pompey-dev-vpn-smoke.state"

if ! sudo -n true 2>/dev/null; then
  echo "skip: no passwordless sudo for fake wg0"
  exit 0
fi
if ! command -v ip >/dev/null; then
  echo "skip: iproute2 not installed"
  exit 0
fi
if ! sudo -n ip link add pveth0 type veth peer name pveth1 2>/dev/null; then
  echo "skip: cannot create veth (no NET_ADMIN)"
  exit 0
fi
sudo -n ip link del pveth0 2>/dev/null || true

cleanup() { bash "${VPN}" down >/dev/null 2>&1 || true; }
trap cleanup EXIT

bash "${VPN}" down >/dev/null 2>&1 || true
bash "${VPN}" up
bash "${VPN}" exec ip -o link show wg0 | grep -q '[,<]UP[,>]'
bash "${VPN}" exec ip -o addr show wg0 | grep -q '10.2.0.2'
if ip link show wg0 >/dev/null 2>&1; then
  echo "wg0 should live in the netns, not on the host" >&2
  exit 1
fi

# Host default route must stay on the real adapter.
def="$(ip route show default | awk '{ for (i=1;i<=NF;i++) if ($i=="dev") { print $(i+1); exit } }')"
[[ "${def}" != "wg0" ]]

# All traffic in the netns leaves via wg0, then NATs out eth0.
# Prefer a name so DNS in the netns is covered; fall back to a literal IP.
code="$(bash "${VPN}" exec curl -4 -sS -o /dev/null -w '%{http_code}' --max-time 20 https://example.com || true)"
if [[ "${code}" != "200" ]]; then
  code="$(bash "${VPN}" exec curl -4 -sS -o /dev/null -w '%{http_code}' --max-time 15 http://1.1.1.1 || true)"
fi
if [[ "${code}" != "200" && "${code}" != "301" && "${code}" != "302" ]]; then
  echo "curl via fake wg0 netns -> HTTP ${code}" >&2
  exit 1
fi

bash "${VPN}" down
if ip netns list 2>/dev/null | awk '{ print $1 }' | grep -qx pompey-dev; then
  echo "netns still present after down" >&2
  exit 1
fi
trap - EXIT
echo "fake wg0 smoke ok"
