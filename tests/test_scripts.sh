#!/usr/bin/env bash
# Drive addon scripts with a supplied Supervisor options.json. No HAOS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${ROOT}/pompey/rootfs/usr/local/bin"
INIT="${ROOT}/pompey/rootfs/etc/cont-init.d"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pompey-scripts.XXXXXX")"
cleanup() { rm -rf "${WORK}"; }
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
export POMPEY_NGINX_RUN="${WORK}/run/nginx"
export NGINX_INGRESS_CONF="${WORK}/etc/nginx/http.d/ingress.conf"
export MEDIA_ROOT="${WORK}/media"
export IPTABLES_LOG="${WORK}/iptables.log"
mkdir -p "${WORK}/bin"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/iptables"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/ip6tables"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/iptables-nft"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/ip6tables-nft"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/iptables-legacy"
cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/ip6tables-legacy"
chmod +x "${WORK}/bin/iptables" "${WORK}/bin/ip6tables" \
  "${WORK}/bin/iptables-nft" "${WORK}/bin/ip6tables-nft" \
  "${WORK}/bin/iptables-legacy" "${WORK}/bin/ip6tables-legacy" \
  "${ROOT}/tests/with-bashio"
# Addon scripts use #!/command/with-contenv bashio. Invoke them with bash
# so a supplied options.json is enough; the HA interpreter is not required.
for f in "${BIN}"/*; do
  [[ -f "${f}" ]] || continue
  cmd="$(basename "${f}")"
  head="$(head -n1 "${f}")"
  if [[ "${head}" == *bashio* && "${cmd}" != "pompey-env" ]]; then
    printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "${f}" > "${WORK}/bin/${cmd}"
    chmod +x "${WORK}/bin/${cmd}"
  fi
done
export PATH="${WORK}/bin:${BIN}:${PATH}"
export INGRESS_PORT=8099

mkdir -p "${POMPEY_WG_ETC}" "$(dirname "${NGINX_INGRESS_CONF}")" "${POMPEY_VPN_TMP}"
cp "${ROOT}/pompey/rootfs/etc/nginx/http.d/ingress.conf" "${NGINX_INGRESS_CONF}"

run() {
  "${ROOT}/tests/with-bashio" "$@"
}

echo "== banner (empty HA options) =="
log="$(run "${INIT}/00-banner.sh" 2>&1)"
printf '%s\n' "${log}"
grep -q "Pompey" <<<"${log}"
test -f "${POMPEY_SECRETS}"
test -d "${MEDIA_ROOT}/Movies/Not Kid Friendly"
test -d "${MEDIA_ROOT}/Movies/Kid Friendly"
# Secrets must not appear in banner output
python3 - "${POMPEY_SECRETS}" "${log}" <<'PY'
import json, sys
secrets = json.load(open(sys.argv[1], encoding="utf-8"))
log = sys.argv[2]
for key in ("qbit_password", "sonarr_api_key", "radarr_api_key", "prowlarr_api_key"):
    assert secrets[key] not in log, key
print("secrets stay out of banner logs")
PY

echo "== banner stays up without Proton (wait screen paste) =="
empty="${WORK}/empty-options.json"
python3 - "${ROOT}/tests/fixtures/options.defaults.json" "${empty}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
json.dump(data, open(sys.argv[2], "w"))
PY
BASHIO_OPTIONS="${empty}" run "${INIT}/00-banner.sh" >/tmp/pompey-banner-empty.log 2>&1
grep -qi "paste the .conf you downloaded from Proton" /tmp/pompey-banner-empty.log
: > "${IPTABLES_LOG}"
BASHIO_OPTIONS="${empty}" run "${INIT}/10-vpn-config.sh" >/tmp/pompey-vpn-empty.log 2>&1
grep -q "Waiting for a Proton WireGuard config" /tmp/pompey-vpn-empty.log
if [[ -s "${POMPEY_WG_CONF}" ]]; then
  echo "empty Proton must not write a partial wg0.conf" >&2
  cat "${POMPEY_WG_CONF}" >&2
  exit 1
fi
if grep -q -- "-j DROP" "${IPTABLES_LOG}"; then
  echo "empty Proton must not apply the kill switch" >&2
  exit 1
fi
test "$(jq -r .need_proton "${POMPEY_READY}/status.json")" = true

echo "== write-engine-configs from options =="
run "${BIN}/write-engine-configs"
test -f "${POMPEY_CONFIG}/sonarr/config.xml"
test -f "${POMPEY_CONFIG}/radarr/config.xml"
test -f "${POMPEY_CONFIG}/prowlarr/config.xml"
test -f "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -q "BindAddress>127.0.0.1" "${POMPEY_CONFIG}/sonarr/config.xml"
grep -q "BindAddress>127.0.0.1" "${POMPEY_CONFIG}/radarr/config.xml"
grep -Fq 'BindAddress>*</BindAddress>' "${POMPEY_CONFIG}/prowlarr/config.xml"
if grep -q "BindAddress>127.0.0.1" "${POMPEY_CONFIG}/prowlarr/config.xml"; then
  echo "Prowlarr must not bind only localhost" >&2
  exit 1
fi
if grep -qi "AuthenticationMethod>None" "${POMPEY_CONFIG}/prowlarr/config.xml"; then
  echo "Prowlarr AuthenticationMethod None would leave sources open on the LAN" >&2
  exit 1
fi
grep -Fq "WebUI\\Address=127.0.0.1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "FileLogger\\Enabled=true" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "FileLogger\\Path=${POMPEY_CONFIG}/qBittorrent/logs" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\Interface=wg0" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\Interface=wg0" "${POMPEY_CONFIG}/qBittorrent/config/qBittorrent.conf"
grep -Fq "Session\\DefaultSavePath=${MEDIA_ROOT}/downloads/complete" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\DisableAutoTMMByDefault=true" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatioAct=1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxSeedingTime=2880" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatioEnabled=false" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
if grep -Fq "Session\\MaxRatioAct=3" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"; then
  echo "MaxRatioAct=3 would delete library files" >&2
  exit 1
fi
test -d "${MEDIA_ROOT}/Movies"
test -d "${MEDIA_ROOT}/Movies/Not Kid Friendly"
test -d "${MEDIA_ROOT}/Movies/Kid Friendly"
python3 - "${POMPEY_SECRETS}" "${POMPEY_CONFIG}" <<'PY'
import json, pathlib, sys
secrets = json.load(open(sys.argv[1], encoding="utf-8"))
cfg = pathlib.Path(sys.argv[2])
assert secrets["sonarr_api_key"] in (cfg / "sonarr/config.xml").read_text()
assert secrets["qbit_pbkdf2"] in (cfg / "qBittorrent/qBittorrent.conf").read_text()
assert secrets["qbit_password"] not in (cfg / "qBittorrent/qBittorrent.conf").read_text()
print("engine configs match secrets; qbit password is hashed")
PY

echo "== write-engine-configs publishes an existing localhost Prowlarr file =="
python3 - "${POMPEY_CONFIG}/prowlarr/config.xml" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(
    """<Config>
  <BindAddress>127.0.0.1</BindAddress>
  <Port>9696</Port>
  <ApiKey>keep-me</ApiKey>
  <AuthenticationMethod>None</AuthenticationMethod>
  <InstanceName>Prowlarr</InstanceName>
</Config>
"""
)
PY
run "${BIN}/write-engine-configs"
grep -Fq 'BindAddress>*</BindAddress>' "${POMPEY_CONFIG}/prowlarr/config.xml"
grep -q "keep-me" "${POMPEY_CONFIG}/prowlarr/config.xml"
if grep -qi "AuthenticationMethod>None" "${POMPEY_CONFIG}/prowlarr/config.xml"; then
  echo "upgrade must drop AuthenticationMethod None" >&2
  cat "${POMPEY_CONFIG}/prowlarr/config.xml" >&2
  exit 1
fi

echo "== write-engine-configs patches qbit save path when media folder changes =="
export MEDIA_ROOT="${WORK}/media-nas"
export MEDIA_MOVIES="Movies/Not Kid Friendly"
export MEDIA_MOVIES_KID="Movies/Kid Friendly"
export MEDIA_TV="TV/Not Kid Friendly"
export MEDIA_TV_KID="TV/Kid Friendly"
run "${BIN}/write-engine-configs"
grep -Fq "Session\\DefaultSavePath=${MEDIA_ROOT}/downloads/complete" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\DefaultSavePath=${MEDIA_ROOT}/downloads/complete" "${POMPEY_CONFIG}/qBittorrent/config/qBittorrent.conf"
test -d "${MEDIA_ROOT}/Movies/Not Kid Friendly"
test -d "${MEDIA_ROOT}/Movies/Kid Friendly"
test -d "${MEDIA_ROOT}/TV/Not Kid Friendly"
test -d "${MEDIA_ROOT}/downloads/incomplete"
export MEDIA_ROOT="${WORK}/media"
unset MEDIA_MOVIES MEDIA_MOVIES_KID MEDIA_TV MEDIA_TV_KID

echo "== vpn config from wg0.conf file + kill switch stub =="
mkdir -p "${POMPEY_CONFIG}/wireguard"
cp "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf"
: > "${IPTABLES_LOG}"
run "${INIT}/10-vpn-config.sh"
grep -q "FILEPRIVATEKEY" "${POMPEY_WG_CONF}"
grep -q "185.159.157.1:51820" "${POMPEY_WG_CONF}"
grep -q "PersistentKeepalive = 25" "${POMPEY_WG_CONF}"
grep -q "nameserver 10.2.0.1" "${POMPEY_RESOLV}"
if grep -qiE '^[[:space:]]*DNS[[:space:]]*=' "${POMPEY_WG_CONF}"; then
  echo "DNS= must not reach wg-quick (resolvconf signature mismatch on HAOS)" >&2
  cat "${POMPEY_WG_CONF}" >&2
  exit 1
fi
if ! grep -qiE '^[[:space:]]*Table[[:space:]]*=[[:space:]]*off' "${POMPEY_WG_CONF}"; then
  echo "Table=off must reach wg-quick (HAOS src_valid_mark is read-only)" >&2
  cat "${POMPEY_WG_CONF}" >&2
  exit 1
fi
python3 - "${POMPEY_WG_CONF}" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
low = text.lower()
iface, peer, table = low.find("[interface]"), low.find("[peer]"), low.find("table")
if iface < 0 or table < 0 or table < iface or (peer >= 0 and table > peer):
    print("Table=off must sit in [Interface], not after [Peer]", file=sys.stderr)
    print(text, file=sys.stderr)
    raise SystemExit(1)
PY
python3 "${ROOT}/tests/lib/wg_quick_contract.py" "${POMPEY_WG_CONF}"
grep -q "DNS = 10.2.0.1" "${POMPEY_CONFIG}/wireguard/wg0.conf"
grep -q "10.0.0.0/8" "${POMPEY_LAN_FILE}"
grep -q "185.159.157.1" "${IPTABLES_LOG}"
grep -q "51820" "${IPTABLES_LOG}"
grep -q -- "-o wg0 -j ACCEPT" "${IPTABLES_LOG}"
grep -q -- "-j DROP" "${IPTABLES_LOG}"
if grep -q "FILEPRIVATEKEY" /tmp/pompey-banner-empty.log; then
  echo "unexpected private key in banner log" >&2
  exit 1
fi
grep -q "Using WireGuard config file" <<<"$(BASHIO_OPTIONS="${BASHIO_OPTIONS}" run "${INIT}/10-vpn-config.sh" 2>&1)"

echo "== vpn config strips Proton PostUp iptables snippets =="
python3 - "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
text += "\nPostUp = iptables -I OUTPUT ! -o %i -j REJECT\nPostDown = iptables -D OUTPUT ! -o %i -j REJECT\n"
Path(sys.argv[2]).write_text(text)
PY
run "${INIT}/10-vpn-config.sh"
grep -qi "FILEPRIVATEKEY" "${POMPEY_WG_CONF}"
if grep -qiE '^[[:space:]]*(PostUp|PostDown)[[:space:]]*=' "${POMPEY_WG_CONF}"; then
  echo "PostUp/PostDown must not reach wg-quick" >&2
  cat "${POMPEY_WG_CONF}" >&2
  exit 1
fi

echo "== WireGuard failure must not halt Ingress =="
if grep -E 'basedir/bin/halt|/run/s6/.*/halt' "${ROOT}/pompey/rootfs/etc/services.d/wireguard/finish"; then
  echo "wireguard/finish must not halt the container (that kills nginx/Ingress)" >&2
  exit 1
fi
grep -q 'pompey-wg' "${ROOT}/pompey/rootfs/etc/services.d/wireguard/run"
grep -q 'log_wg_quick' "${ROOT}/pompey/rootfs/etc/services.d/wireguard/run"
grep -q 'pompey-wg-routes' "${ROOT}/pompey/rootfs/etc/services.d/wireguard/run"
grep -q 'vpn-up' "${ROOT}/pompey/rootfs/etc/services.d/wireguard/run"
grep -q 'Ingress stays up' "${ROOT}/pompey/rootfs/etc/services.d/wireguard/run"

echo "== wg-quick helpers ignore HAOS resolvconf and read-only src_valid_mark =="
WG_HELPERS="${ROOT}/pompey/rootfs/usr/local/bin/pompey-wg"
chmod +x "${WG_HELPERS}/resolvconf" "${WG_HELPERS}/sysctl"
"${WG_HELPERS}/resolvconf" -a wg0 -m 0 -x
"${WG_HELPERS}/sysctl" -q net.ipv4.conf.all.src_valid_mark=1
failing_sysctl="${WORK}/bin/failing-sysctl"
printf '%s\n' '#!/bin/sh' 'echo "sysctl: error setting key: Read-only file system" >&2' 'exit 1' >"${failing_sysctl}"
chmod +x "${failing_sysctl}"
if ! POMPEY_REAL_SYSCTL="${failing_sysctl}" "${WG_HELPERS}/sysctl" -q net.ipv4.conf.all.src_valid_mark=1; then
  echo "src_valid_mark must succeed when /proc/sys is read-only" >&2
  exit 1
fi
if POMPEY_REAL_SYSCTL="${failing_sysctl}" "${WG_HELPERS}/sysctl" net.ipv4.ip_forward=1 >/dev/null 2>&1; then
  echo "sysctl wrapper must still fail other keys when the real sysctl fails" >&2
  exit 1
fi
grep -q 'src_valid_mark' "${WG_HELPERS}/sysctl"

echo "== vpn kill switch uses nft/iptables when legacy filter table is missing =="
cp "${ROOT}/tests/stubs/iptables-fail" "${WORK}/bin/iptables-legacy"
cp "${ROOT}/tests/stubs/iptables-fail" "${WORK}/bin/ip6tables-legacy"
chmod +x "${WORK}/bin/iptables-legacy" "${WORK}/bin/ip6tables-legacy"
cp "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf"
: > "${IPTABLES_LOG}"
log="$(run "${INIT}/10-vpn-config.sh" 2>&1)"
printf '%s\n' "${log}"
grep -q -- "-j DROP" "${IPTABLES_LOG}"
grep -q "iptables-nft" "${IPTABLES_LOG}"
test -f "${POMPEY_READY}/vpn-applied"

echo "== vpn apply succeeds when no iptables/nft filter table exists =="
for name in iptables ip6tables iptables-nft ip6tables-nft iptables-legacy ip6tables-legacy; do
  cp "${ROOT}/tests/stubs/iptables-fail" "${WORK}/bin/${name}"
  chmod +x "${WORK}/bin/${name}"
done
cp "${ROOT}/tests/stubs/nft-fail" "${WORK}/bin/nft"
chmod +x "${WORK}/bin/nft"
cp "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf"
: > "${IPTABLES_LOG}"
rm -f "${POMPEY_READY}/vpn-applied"
log="$(run "${INIT}/10-vpn-config.sh" 2>&1)"
printf '%s\n' "${log}"
grep -q "Kill switch" <<<"${log}"
grep -q "FILEPRIVATEKEY" "${POMPEY_WG_CONF}"
test -f "${POMPEY_READY}/vpn-applied"
if grep -q -- "-j DROP" "${IPTABLES_LOG}"; then
  echo "no working filter table must not apply OUTPUT DROP" >&2
  cat "${IPTABLES_LOG}" >&2
  exit 1
fi
# Restore working stubs for later tests
for name in iptables ip6tables iptables-nft ip6tables-nft iptables-legacy ip6tables-legacy; do
  cp "${ROOT}/tests/stubs/iptables" "${WORK}/bin/${name}"
  chmod +x "${WORK}/bin/${name}"
done
rm -f "${WORK}/bin/nft"

echo "== engines and NAT-PMP wait until wg0 has a handshake =="
grep -q 'vpn-up' "${ROOT}/pompey/rootfs/etc/services.d/engines/run"
grep -q 'vpn-up' "${ROOT}/pompey/rootfs/etc/services.d/natpmp/run"
grep -q 'iptables-nft' "${ROOT}/pompey/rootfs/usr/local/bin/vpn-killswitch"

echo "== vpn config resolves Endpoint hostname before kill switch =="
python3 - "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
text = text.replace("185.159.157.1:51820", "localhost:51820")
Path(sys.argv[2]).write_text(text)
PY
: > "${IPTABLES_LOG}"
run "${INIT}/10-vpn-config.sh"
grep -q "127.0.0.1:51820" "${POMPEY_WG_CONF}"
grep -qv "Endpoint = localhost" "${POMPEY_WG_CONF}"
grep -q "127.0.0.1" "${IPTABLES_LOG}"
grep -q "51820" "${IPTABLES_LOG}"

echo "== nginx ingress port from stub =="
run "${INIT}/20-nginx.sh"
grep -q "listen 8099" "${NGINX_INGRESS_CONF}"
grep -qv "%%port%%" "${NGINX_INGRESS_CONF}"
grep -q "status.json" "${NGINX_INGRESS_CONF}"
grep -q "setup/proton" "${NGINX_INGRESS_CONF}"
grep -q "access_log off" "${NGINX_INGRESS_CONF}"
grep -q "access_log off" "${ROOT}/pompey/rootfs/etc/nginx/http.d/ingress.conf"
grep -q '/status.json' "${ROOT}/pompey/rootfs/etc/nginx/nginx.conf"
grep -q 'if=$pompey_accesslog' "${ROOT}/pompey/rootfs/etc/nginx/nginx.conf"
grep -q 'vpn-up' "${ROOT}/pompey/rootfs/etc/services.d/engines/run"
grep -q 'vpn-up' "${ROOT}/pompey/rootfs/etc/services.d/natpmp/run"
grep -q -- '--quiet' "${ROOT}/pompey/rootfs/usr/local/bin/wait-for-vpn"
grep -q '%H:%M:%S' "${ROOT}/pompey/rootfs/usr/local/bin/pompey-setup"
grep -q '%H:%M:%S' "${ROOT}/pompey/rootfs/usr/local/bin/wire-stack"
test -f "${ROOT}/pompey/rootfs/etc/services.d/setup/run"
test ! -e "${ROOT}/pompey/rootfs/usr/local/bin/pompey-ingress"
test ! -e "${ROOT}/pompey/rootfs/etc/services.d/ingress-proxy"
test ! -e "${ROOT}/tests/preview_seerr_ingress.py"
grep -Fq 'rm -f "${POMPEY_CONFIG}/seerr/DOCKER"' "${ROOT}/pompey/rootfs/etc/services.d/seerr/run"
grep -Fq 'rm -f "${POMPEY_CONFIG}/seerr/DOCKER"' "${ROOT}/pompey/rootfs/usr/local/bin/fetch-engines"
if grep -Fq 'touch "${POMPEY_CONFIG}/seerr/DOCKER"' \
    "${ROOT}/pompey/rootfs/etc/services.d/seerr/run" \
    "${ROOT}/pompey/rootfs/usr/local/bin/fetch-engines"; then
  echo "do not plant Seerr's DOCKER sentinel" >&2
  exit 1
fi
grep -q 'POMPEY_CONFIG' "${ROOT}/pompey/rootfs/etc/services.d/radarr/run"
grep -q 'HOST=0.0.0.0' "${ROOT}/pompey/rootfs/etc/services.d/seerr/run"
grep -q '9696/tcp: 9696' "${ROOT}/pompey/config.yaml"

echo "== status.json writer =="
python3 "${BIN}/pompey-status" vpn "Waiting for Proton handshake" 15
python3 "${BIN}/pompey-status" fetch "Downloading hidden engines" 35
test "$(jq -r .step "${POMPEY_READY}/status.json")" = fetch
test "$(jq -r '.steps[] | select(.id=="vpn") | .state' "${POMPEY_READY}/status.json")" = done
python3 "${BIN}/pompey-status" vpn "Starting" 5
test "$(jq -r '.steps[] | select(.id=="fetch") | .state' "${POMPEY_READY}/status.json")" = pending
test "$(jq -r .step "${POMPEY_READY}/status.json")" = vpn
python3 "${BIN}/pompey-status" ready "Ready" 100
test "$(jq -r .search "${POMPEY_READY}/status.json")" = true
test "$(jq -r .search_port "${POMPEY_READY}/status.json")" = 5055
test "$(jq -r .sources_port "${POMPEY_READY}/status.json")" = 9696
rm -rf "${POMPEY_READY}"
python3 "${BIN}/pompey-status" vpn "Starting" 5
test -f "${POMPEY_READY}/status.json"

echo "== fetch URL construction (range GET, not a full download) =="
urls="$(run "${BIN}/fetch-engines" --print-urls)"
printf '%s\n' "${urls}"
grep -q -- '--no-same-owner' "${BIN}/fetch-engines"
grep -q -- '--no-same-permissions' "${BIN}/fetch-engines"
grep -q '.partial-' "${BIN}/fetch-engines"
while IFS= read -r url; do
  [[ -n "${url}" ]] || continue
  out=""
  for attempt in 1 2 3 4; do
    out="$(curl -sS -o /dev/null -w '%{http_code}' -r 0-256 -L \
      --retry 2 --retry-delay 2 --retry-all-errors --max-time 40 "${url}" || true)"
    if [[ "${out}" == "200" || "${out}" == "206" ]]; then
      break
    fi
    # Cloudflare/Servarr origin failures are not a bad URL. Do not retry 40s×4.
    case "${out}" in
      ""|000|502|503|520|521|522|523|524) break ;;
    esac
    sleep "${attempt}"
  done
  if [[ "${out}" == "200" || "${out}" == "206" ]]; then
    echo "${out} ${url%%\?*}"
    continue
  fi
  # Servarr/GitHub range-GET 5xx/timeout is upstream, not a bad URL we built.
  case "${out}" in
    ""|000|502|503|520|521|522|523|524)
      echo "skip live fetch ${out:-timeout} ${url%%\?*}" >&2
      continue
      ;;
  esac
  echo "bad ${out} ${url}" >&2
  exit 1
done <<<"${urls}"
musl_urls="$(POMPEY_SERVARR_OS=linuxmusl run "${BIN}/fetch-engines" --print-urls)"
grep -q 'os=linuxmusl' <<<"${musl_urls}"
grep -q 'os=linux&' <<<"${urls}" || grep -q 'os=linux$' <<<"${urls}" || grep -q 'os=linux&runtime' <<<"${urls}"
echo "fetch URLs reachable (glibc here, musl URLs listed for HAOS)"

echo "== fake VPN skips kill switch and wait-for-vpn checks iface =="
: > "${IPTABLES_LOG}"
POMPEY_FAKE_VPN=1 run "${INIT}/10-vpn-config.sh"
if grep -q -- "-j DROP" "${IPTABLES_LOG}"; then
  echo "fake VPN must not apply OUTPUT DROP" >&2
  cat "${IPTABLES_LOG}" >&2
  exit 1
fi
mkdir -p "${WORK}/bin"
cat > "${WORK}/bin/ip" <<'EOF'
#!/usr/bin/env bash
echo '2: wg0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP'
exit 0
EOF
chmod +x "${WORK}/bin/ip"
POMPEY_FAKE_VPN=1 run "${BIN}/wait-for-vpn" 1

echo "== banner allows empty Proton when POMPEY_FAKE_VPN=1 =="
BASHIO_OPTIONS="${empty}" POMPEY_FAKE_VPN=1 run "${INIT}/00-banner.sh" >/tmp/pompey-banner-fake.log 2>&1
grep -q "Fake VPN" /tmp/pompey-banner-fake.log

echo "== every s6 service logs start and stop with a name tag =="
svc="${ROOT}/pompey/rootfs/etc/services.d"
missing=0
for runf in "${svc}"/*/run; do
  if ! grep -q 'pompey-log start' "${runf}"; then
    echo "missing pompey-log start in ${runf}" >&2
    missing=1
  fi
done
for fin in "${svc}"/*/finish; do
  if ! grep -q 'pompey-log finish' "${fin}"; then
    echo "missing pompey-log finish in ${fin}" >&2
    missing=1
  fi
done
if [[ "${missing}" -ne 0 ]]; then
  exit 1
fi
for named in radarr sonarr prowlarr prowlarr-arr seerr nginx; do
  if ! grep -q 'pompey-log prefix' "${svc}/${named}/run"; then
    echo "missing pompey-log prefix in ${svc}/${named}/run" >&2
    exit 1
  fi
done
grep -q 'pompey-log tail' "${svc}/qbittorrent/run"
grep -q 'qBittorrent/logs' "${svc}/qbittorrent/run"
test -x "${BIN}/pompey-log"
test -x "${BIN}/pompey-log-emit"
test -x "${BIN}/prowlarr-arr-proxy"
grep -q '127.0.0.1:9698/ping' "${svc}/wire/run"
grep -q 'wire-stack housekeep' "${svc}/wire/run"
if grep -q 'deleteFiles.: .true' "${BIN}/wire-stack"; then
  echo "qbit must never delete torrent files (library may be hardlinked)" >&2
  exit 1
fi
if grep -RIn 'pompey-svc' "${ROOT}/pompey/rootfs"; then
  echo "pompey-svc was renamed to pompey-log" >&2
  exit 1
fi

echo "== pompey-log start/finish/prefix/line =="
out="$(run pompey-log start "qBittorrent" 2>&1)"
grep -q "Starting qBittorrent" <<<"${out}"
out="$(run pompey-log finish "qBittorrent" 0 0 2>&1)"
grep -q "qBittorrent stopped" <<<"${out}"
out="$(run pompey-log finish "Radarr" 256 15 2>&1)"
grep -q "Radarr stopped" <<<"${out}"
out="$(run pompey-log finish "Sonarr" 1 0 2>&1)"
grep -q "Sonarr exited (1)" <<<"${out}"
out="$(run pompey-log finish "nginx" 256 9 2>&1)"
grep -q "killed (signal 9)" <<<"${out}"
out="$(printf '%s\n' 'WebUI started' '|Error| disk full' '|Warn| slow disk' | run pompey-log prefix "Radarr" 2>&1)"
grep -q "\[Radarr\] WebUI started" <<<"${out}"
grep -q "\[Radarr\] |Error| disk full" <<<"${out}"
grep -qi "ERROR" <<<"${out}"
grep -q "\[Radarr\] |Warn| slow disk" <<<"${out}"
grep -qi "WARNING" <<<"${out}"
out="$(run pompey-log line "qBittorrent" "(I) listen port 41234" 2>&1)"
grep -q "\[qBittorrent\] (I) listen port 41234" <<<"${out}"
out="$(run pompey-log line "qBittorrent" "(C) Permission denied writing to disk" 2>&1)"
grep -qi "ERROR" <<<"${out}"
run "${svc}/qbittorrent/finish" 1 0 >/tmp/pompey-qbit-finish.log 2>&1
grep -q "qBittorrent exited (1)" /tmp/pompey-qbit-finish.log

echo "== pompey-log tail follows a file =="
tlog="${WORK}/engine.log"
: > "${tlog}"
run pompey-log tail "Sonarr" "${tlog}" >"${WORK}/tailed.log" 2>&1 &
tail_pid=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  grep -q "file log" "${WORK}/tailed.log" 2>/dev/null && break
  sleep 0.2
done
printf '%s\n' 'scan started' '|Error| unable to access folder' >>"${tlog}"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  grep -q "\[Sonarr\] scan started" "${WORK}/tailed.log" 2>/dev/null && break
  sleep 0.2
done
kill "${tail_pid}" 2>/dev/null || true
wait "${tail_pid}" 2>/dev/null || true
grep -q "\[Sonarr\] scan started" "${WORK}/tailed.log"
grep -q "\[Sonarr\] |Error| unable to access folder" "${WORK}/tailed.log"

echo "== write-engine-configs adds FileLogger to an existing qbit conf =="
python3 - "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(
    "[BitTorrent]\n"
    "Session\\DefaultSavePath=/old/complete\n"
    "Session\\Interface=wg0\n"
)
PY
run "${BIN}/write-engine-configs"
grep -Fq "FileLogger\\Enabled=true" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "FileLogger\\Path=${POMPEY_CONFIG}/qBittorrent/logs" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\DefaultSavePath=${MEDIA_ROOT}/downloads/complete" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatioAct=1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxSeedingTime=2880" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"

echo "== write-engine-configs after_download from Home Assistant options =="
share_opts="${WORK}/share-options.json"
python3 - "${ROOT}/tests/options.json" "${share_opts}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
data["after_download"] = "share_to_ratio"
json.dump(data, open(sys.argv[2], "w"))
PY
unset AFTER_DOWNLOAD || true
BASHIO_OPTIONS="${share_opts}" run "${BIN}/write-engine-configs"
grep -Fq "Session\\MaxRatioEnabled=true" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatio=1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxSeedingTimeEnabled=false" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatioAct=1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
if grep -Fq "Session\\MaxRatioAct=3" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"; then
  echo "MaxRatioAct=3 would delete library files" >&2
  exit 1
fi
python3 - "${ROOT}/tests/options.json" "${share_opts}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
data["after_download"] = "share_one_day"
json.dump(data, open(sys.argv[2], "w"))
PY
unset AFTER_DOWNLOAD || true
BASHIO_OPTIONS="${share_opts}" run "${BIN}/write-engine-configs"
grep -Fq "Session\\MaxSeedingTime=1440" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxSeedingTimeEnabled=true" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatioAct=1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
unset AFTER_DOWNLOAD || true
BASHIO_OPTIONS="${ROOT}/tests/options.json" run "${BIN}/write-engine-configs"
grep -Fq "Session\\MaxSeedingTime=2880" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\MaxRatioEnabled=false" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"

echo "script tests ok (${WORK})"
