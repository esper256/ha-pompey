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
chmod +x "${WORK}/bin/iptables" "${WORK}/bin/ip6tables" \
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

echo "== banner (fields in options.json) =="
log="$(run "${INIT}/00-banner.sh" 2>&1)"
printf '%s\n' "${log}"
grep -q "Pompey" <<<"${log}"
test -f "${POMPEY_SECRETS}"
test -d "${MEDIA_ROOT}/Kid Friendly Movies"
# Secrets must not appear in banner output
python3 - "${POMPEY_SECRETS}" "${log}" <<'PY'
import json, sys
secrets = json.load(open(sys.argv[1], encoding="utf-8"))
log = sys.argv[2]
for key in ("qbit_password", "sonarr_api_key", "radarr_api_key", "prowlarr_api_key"):
    assert secrets[key] not in log, key
print("secrets stay out of banner logs")
PY

echo "== banner refuses empty VPN =="
empty="${WORK}/empty-options.json"
python3 - "${ROOT}/tests/fixtures/options.defaults.json" "${empty}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
json.dump(data, open(sys.argv[2], "w"))
PY
if BASHIO_OPTIONS="${empty}" run "${INIT}/00-banner.sh" >/tmp/pompey-banner-empty.log 2>&1; then
  echo "expected banner to fail without WireGuard" >&2
  cat /tmp/pompey-banner-empty.log >&2
  exit 1
fi
grep -q "Need a Proton WireGuard config" /tmp/pompey-banner-empty.log

echo "== banner warns when Plex/source are empty =="
warn="${WORK}/warn-options.json"
python3 - "${ROOT}/tests/options.json" "${warn}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
data["plex_token"] = ""
data["indexer_url"] = ""
data["indexer_api_key"] = ""
json.dump(data, open(sys.argv[2], "w"))
PY
BASHIO_OPTIONS="${warn}" run "${INIT}/00-banner.sh" >/tmp/pompey-banner-warn.log 2>&1
grep -q "Plex token is empty" /tmp/pompey-banner-warn.log
grep -q "No source URL yet" /tmp/pompey-banner-warn.log

echo "== write-engine-configs from options =="
run "${BIN}/write-engine-configs"
test -f "${POMPEY_CONFIG}/sonarr/config.xml"
test -f "${POMPEY_CONFIG}/radarr/config.xml"
test -f "${POMPEY_CONFIG}/prowlarr/config.xml"
test -f "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -q "127.0.0.1" "${POMPEY_CONFIG}/sonarr/config.xml"
grep -q "BindAddress>127.0.0.1" "${POMPEY_CONFIG}/radarr/config.xml"
grep -Fq "WebUI\\Address=127.0.0.1" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq "Session\\Interface=wg0" "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
python3 - "${POMPEY_SECRETS}" "${POMPEY_CONFIG}" <<'PY'
import json, pathlib, sys
secrets = json.load(open(sys.argv[1], encoding="utf-8"))
cfg = pathlib.Path(sys.argv[2])
assert secrets["sonarr_api_key"] in (cfg / "sonarr/config.xml").read_text()
assert secrets["qbit_pbkdf2"] in (cfg / "qBittorrent/qBittorrent.conf").read_text()
assert secrets["qbit_password"] not in (cfg / "qBittorrent/qBittorrent.conf").read_text()
print("engine configs match secrets; qbit password is hashed")
PY

echo "== vpn config from options fields + kill switch stub =="
run "${INIT}/10-vpn-config.sh"
grep -q "TESTPRIVATEKEY" "${POMPEY_WG_CONF}"
grep -q "185.159.157.1:51820" "${POMPEY_WG_CONF}"
grep -q "nameserver 10.2.0.1" "${POMPEY_RESOLV}"
grep -q "10.0.0.0/8" "${POMPEY_LAN_FILE}"
grep -q "185.159.157.1" "${IPTABLES_LOG}"
grep -q "51820" "${IPTABLES_LOG}"
grep -q -- "-o wg0 -j ACCEPT" "${IPTABLES_LOG}"
grep -q -- "-j DROP" "${IPTABLES_LOG}"
# Private key must not be in script logs; it is in the wg conf file by design.
if grep -q "TESTPRIVATEKEY" /tmp/pompey-banner-empty.log; then
  echo "unexpected" >&2
  exit 1
fi

echo "== vpn config from wg0.conf file =="
cp "${ROOT}/tests/fixtures/wg0.conf" "${POMPEY_CONFIG}/wireguard/wg0.conf"
: > "${IPTABLES_LOG}"
run "${INIT}/10-vpn-config.sh"
grep -q "FILEPRIVATEKEY" "${POMPEY_WG_CONF}"
grep -q "Using WireGuard config file" <<<"$(BASHIO_OPTIONS="${BASHIO_OPTIONS}" run "${INIT}/10-vpn-config.sh" 2>&1)"

echo "== nginx ingress port from stub =="
run "${INIT}/20-nginx.sh"
grep -q "listen 8099" "${NGINX_INGRESS_CONF}"
grep -qv "%%port%%" "${NGINX_INGRESS_CONF}"

echo "== fetch URL construction (range GET, not a full download) =="
python3 - <<'PY'
import os, subprocess, sys
arch = os.uname().machine
servarr = "x64" if arch in {"x86_64", "amd64"} else "arm64"
qbit = "x86_64" if arch in {"x86_64", "amd64"} else "aarch64"
urls = [
    f"https://prowlarr.servarr.com/v1/update/master/updatefile?os=linuxmusl&runtime=netcore&arch={servarr}",
    f"https://services.sonarr.tv/v1/download/main/latest?version=4&os=linuxmusl&arch={servarr}",
    f"https://radarr.servarr.com/v1/update/master/updatefile?os=linuxmusl&runtime=netcore&arch={servarr}",
    f"https://github.com/userdocs/qbittorrent-nox-static/releases/latest/download/{qbit}-qbittorrent-nox",
]
for url in urls:
    out = subprocess.check_output(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-r", "0-256", "-L", "--max-time", "40", url],
        text=True,
    )
    if out not in {"200", "206"}:
        print("bad", out, url, file=sys.stderr)
        sys.exit(1)
    print(out, url.split("?")[0])
print("fetch URLs reachable")
PY

echo "script tests ok (${WORK})"
