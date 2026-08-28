#!/usr/bin/env bash
# Realistic agent run: fake wg0, official TV/movie engines, TMDB lookup.
# Not HAOS. Not Proton. Never starts a torrent client or waits on a grab.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/sbin:/sbin:${PATH}"
export PYTHONDONTWRITEBYTECODE=1
export POMPEY_FAKE_VPN=1
export POMPEY_SKIP_SEERR="${POMPEY_SKIP_SEERR:-1}"
export POMPEY_SKIP_QBIT=1

MOVIE="${POMPEY_TEST_MOVIE:-The Wild Robot}"
TMDB="${POMPEY_TEST_TMDB:-1184918}"
export MOVIE TMDB

if ! sudo -n true 2>/dev/null; then
  echo "integration needs passwordless sudo for fake wg0" >&2
  exit 1
fi

CACHE="${POMPEY_ENGINES_CACHE:-${HOME}/.cache/pompey/engines}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pompey-int.XXXXXX")"
export POMPEY_FAKE_VPN_STATE="${WORK}/fake-vpn.state"
PIDS=()

log() { printf '== %s\n' "$*"; }

kill_pid() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 0
  sudo -n kill "${pid}" >/dev/null 2>&1 || kill "${pid}" >/dev/null 2>&1 || true
  sleep 0.2
  sudo -n kill -9 "${pid}" >/dev/null 2>&1 || kill -9 "${pid}" >/dev/null 2>&1 || true
}

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    kill_pid "${pid}"
  done
  if ip netns list 2>/dev/null | awk '{ print $1 }' | grep -qx pompey-dev; then
    sudo -n ip netns pids pompey-dev 2>/dev/null | xargs -r sudo -n kill >/dev/null 2>&1 || true
    sleep 0.3
    sudo -n ip netns pids pompey-dev 2>/dev/null | xargs -r sudo -n kill -9 >/dev/null 2>&1 || true
  fi
  bash "${ROOT}/pompey/rootfs/usr/local/bin/pompey-dev-vpn" down >/dev/null 2>&1 || true
  sudo -n rm -rf "${WORK}" 2>/dev/null || rm -rf "${WORK}"
}
trap cleanup EXIT

BIN="${ROOT}/pompey/rootfs/usr/local/bin"
INIT="${ROOT}/pompey/rootfs/etc/cont-init.d"
mkdir -p "${WORK}/bin" "${CACHE}"
cp "${ROOT}/tests/with-bashio" "${WORK}/bin/with-bashio"
chmod +x "${WORK}/bin/with-bashio" "${BIN}/pompey-dev-vpn"

export BASHIO_OPTIONS="${WORK}/options.json"
export POMPEY_CONFIG="${WORK}/config"
export POMPEY_DATA="${WORK}/data/pompey"
export POMPEY_ENGINES="${CACHE}"
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
export INGRESS_PORT=8099

# Addon scripts use #!/command/with-contenv bashio.
for f in "${BIN}"/*; do
  [[ -f "${f}" ]] || continue
  cmd="$(basename "${f}")"
  head="$(head -n1 "${f}")"
  if [[ "${head}" == *bashio* && "${cmd}" != "pompey-env" ]]; then
    printf '#!/usr/bin/env bash\nexec bash %q "$@"\n' "${f}" > "${WORK}/bin/${cmd}"
    chmod +x "${WORK}/bin/${cmd}"
  elif [[ "${cmd}" == "pompey-dev-vpn" || "${cmd}" == "wire-stack" || "${cmd}" == "pompey-status" ]]; then
    ln -sfn "${f}" "${WORK}/bin/${cmd}"
  fi
done
export PATH="${WORK}/bin:${BIN}:${PATH}"

run() { "${ROOT}/tests/with-bashio" "$@"; }
ns() {
  sudo -n ip netns exec pompey-dev runuser -u ubuntu -- env \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    HOME="${HOME}" \
    LANG="${LANG:-C.UTF-8}" \
    "$@"
}

wait_url() {
  local url="$1" n="${2:-60}" i=0
  until ns curl -fsS -o /dev/null --max-time 3 "${url}"; do
    i=$((i + 1))
    if [[ "${i}" -ge "${n}" ]]; then
      echo "timeout waiting for ${url}" >&2
      return 1
    fi
    sleep 2
  done
}

arr() {
  local base="$1" key="$2" method="$3" path="$4"
  shift 4
  local extra=()
  if [[ "${method}" != "GET" ]]; then
    extra=(-H "Content-Type: application/json" -d "${1:-{}}")
  fi
  ns curl -fsS --max-time 60 -X "${method}" -H "X-Api-Key: ${key}" "${extra[@]}" "${base}${path}"
}

log "fake wg0"
bash "${BIN}/pompey-dev-vpn" up
ns ip -o addr show wg0 | grep -q '10.2.0.2'
run wait-for-vpn 5

python3 - "${ROOT}/tests/options.json" "${BASHIO_OPTIONS}" "${MEDIA_ROOT}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
data["indexer_url"] = ""
data["indexer_api_key"] = ""
data["media_root"] = sys.argv[3]
data["plex_token"] = ""
data["port_forwarding"] = False
json.dump(data, open(sys.argv[2], "w"), indent=2)
PY

log "banner + vpn config (no kill switch)"
run "${INIT}/00-banner.sh"
run "${INIT}/10-vpn-config.sh"
run wait-for-vpn 5

log "fetch engines into ${CACHE} (no torrent client)"
run "${BIN}/fetch-engines"
test -x "${POMPEY_ENGINES}/Radarr/Radarr"
test -x "${POMPEY_ENGINES}/Prowlarr/Prowlarr"
test -x "${POMPEY_ENGINES}/Sonarr/Sonarr"

log "write configs + start TV/movie engines"
run "${BIN}/write-engine-configs"
grep -Fq 'Session\Interface=wg0' "${POMPEY_CONFIG}/qBittorrent/qBittorrent.conf"
grep -Fq 'Session\Interface=wg0' "${POMPEY_CONFIG}/qBittorrent/config/qBittorrent.conf"
touch "${POMPEY_READY}/engines-ready"

ns "${POMPEY_ENGINES}/Prowlarr/Prowlarr" -nobrowser -data="${POMPEY_CONFIG}/prowlarr" \
  >"${WORK}/prowlarr.log" 2>&1 &
PIDS+=("$!")
ns "${POMPEY_ENGINES}/Sonarr/Sonarr" -nobrowser -data="${POMPEY_CONFIG}/sonarr" \
  >"${WORK}/sonarr.log" 2>&1 &
PIDS+=("$!")
ns "${POMPEY_ENGINES}/Radarr/Radarr" -nobrowser -data="${POMPEY_CONFIG}/radarr" \
  >"${WORK}/radarr.log" 2>&1 &
PIDS+=("$!")

# wire-stack waits on the download-engine WebUI. Answer HTTP only — do not run it.
log "HTTP stubs (download-engine WebUI + Seerr); no torrent client"
ns python3 - "${WORK}" >"${WORK}/http-stub.log" 2>&1 <<'PY' &
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, sys, threading
from urllib.parse import urlparse

work = sys.argv[1]


class Qbit(BaseHTTPRequestHandler):
    def log_message(self, *a, **k):
        return

    def _send(self, code, body, ctype, cookie=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/version") or path.endswith("/webapiVersion"):
            return self._send(200, "5.0.4", "text/plain")
        if path.endswith("/preferences"):
            return self._send(
                200,
                json.dumps({"current_network_interface": "wg0", "listen_port": 0}),
                "application/json",
            )
        if path.endswith("/categories"):
            return self._send(200, "{}", "application/json")
        return self._send(200, "[]", "application/json")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        path = urlparse(self.path).path
        cookie = "SID=pompey-dev; path=/" if path.endswith("/login") else None
        return self._send(200, "Ok.", "text/plain", cookie=cookie)


class Seerr(BaseHTTPRequestHandler):
    def log_message(self, *a, **k):
        return

    def do_GET(self):
        body = json.dumps({"initialized": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


qbit = ThreadingHTTPServer(("127.0.0.1", 8080), Qbit)
seerr = ThreadingHTTPServer(("127.0.0.1", 5055), Seerr)
threading.Thread(target=qbit.serve_forever, daemon=True).start()
threading.Thread(target=seerr.serve_forever, daemon=True).start()
open(os.path.join(work, "http-stub.pid"), "w").write(str(os.getpid()))
threading.Event().wait()
PY
PIDS+=("$!")

wait_url http://127.0.0.1:8080/api/v2/app/version 20
wait_url http://127.0.0.1:5055/api/v1/settings/public 20
wait_url http://127.0.0.1:9696/ping 60
wait_url http://127.0.0.1:8989/ping 60
wait_url http://127.0.0.1:7878/ping 60

export PLEX_URL="" PLEX_TOKEN=""
export INDEXER_URL=""
export INDEXER_API_KEY=""
export QBIT_URL=http://127.0.0.1:8080
export SONARR_URL=http://127.0.0.1:8989
export RADARR_URL=http://127.0.0.1:7878
export PROWLARR_URL=http://127.0.0.1:9696
export SEERR_URL=http://127.0.0.1:5055
export POMPEY_WAIT_TRIES=30
export POMPEY_WAIT_SLEEP=2

log "wire localhost engines"
ns env \
  POMPEY_SECRETS="${POMPEY_SECRETS}" \
  POMPEY_READY="${POMPEY_READY}" \
  MEDIA_ROOT="${MEDIA_ROOT}" \
  PLEX_URL="${PLEX_URL}" \
  PLEX_TOKEN="${PLEX_TOKEN}" \
  INDEXER_URL="${INDEXER_URL}" \
  INDEXER_API_KEY="${INDEXER_API_KEY}" \
  QBIT_URL="${QBIT_URL}" \
  SONARR_URL="${SONARR_URL}" \
  RADARR_URL="${RADARR_URL}" \
  PROWLARR_URL="${PROWLARR_URL}" \
  SEERR_URL="${SEERR_URL}" \
  NGINX_INGRESS_CONF="${NGINX_INGRESS_CONF}" \
  INGRESS_PORT="${INGRESS_PORT}" \
  POMPEY_WAIT_TRIES="${POMPEY_WAIT_TRIES}" \
  POMPEY_WAIT_SLEEP="${POMPEY_WAIT_SLEEP}" \
  python3 "${BIN}/wire-stack"
test -f "${POMPEY_READY}/arr-wired"

RADARR_KEY="$(jq -r .radarr_api_key "${POMPEY_SECRETS}")"
RADARR=http://127.0.0.1:7878

log "lookup ${MOVIE}"
lookup="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/movie/lookup?term=$(python3 -c 'import urllib.parse,os; print(urllib.parse.quote(os.environ["MOVIE"]))')" )"
export MOVIE TMDB
movie="$(python3 - "${lookup}" <<'PY'
import json, os, sys
rows = json.loads(sys.argv[1])
tmdb = int(os.environ["TMDB"])
want = os.environ["MOVIE"].lower()
pick = None
for row in rows:
    if int(row.get("tmdbId") or 0) == tmdb:
        pick = row
        break
    if want in (row.get("title") or "").lower():
        pick = row
        break
if not pick:
    raise SystemExit(f"lookup missed {os.environ['MOVIE']}: {[r.get('title') for r in rows][:8]}")
print(json.dumps(pick))
PY
)"
title="$(echo "${movie}" | jq -r .title)"
year="$(echo "${movie}" | jq -r .year)"
log "matched ${title} (${year}) tmdb=$(echo "${movie}" | jq -r .tmdbId)"

profile="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/qualityprofile" | jq '.[0].id')"
root="${MEDIA_ROOT}/Movies"
add="$(python3 - "${movie}" "${profile}" "${root}" <<'PY'
import json, sys
movie = json.loads(sys.argv[1])
movie["qualityProfileId"] = int(sys.argv[2])
movie["rootFolderPath"] = sys.argv[3]
movie["monitored"] = True
movie["minimumAvailability"] = "announced"
movie["addOptions"] = {"searchForMovie": False, "monitor": "movieOnly"}
print(json.dumps(movie))
PY
)"

log "add movie (library only — no search, no download)"
arr "${RADARR}" "${RADARR_KEY}" POST "/api/v3/movie" "${add}" >/dev/null

found=""
movies="[]"
for _ in $(seq 1 15); do
  movies="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/movie")"
  found="$(echo "${movies}" | jq -r --argjson t "${TMDB}" '[.[] | select(.tmdbId==$t)][0].title // empty')"
  if [[ -n "${found}" ]]; then
    break
  fi
  sleep 1
done

echo "${movies:-}" | jq --argjson t "${TMDB}" '[.[] | select(.tmdbId==$t)][0] | {title, tmdbId, hasFile, status, path}' || true

if [[ -z "${found}" ]]; then
  echo "movie lookup/add failed" >&2
  echo "---- radarr.log (tail) ----" >&2
  tail -n 40 "${WORK}/radarr.log" >&2 || true
  echo "---- http-stub.log ----" >&2
  cat "${WORK}/http-stub.log" >&2 || true
  exit 1
fi

if ns sh -c "ss -tuanp 2>/dev/null | grep -q '10.2.0.2'" || ns sh -c "ss -tuan 2>/dev/null | grep -q '10.2.0.2'"; then
  log "saw sockets bound to 10.2.0.2 (wg0)"
else
  log "lookup already went through the fake wg0 netns"
fi

log "integration ok: ${title} is in Radarr on fake wg0 (no torrent client)"
