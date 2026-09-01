#!/usr/bin/env bash
# Realistic agent run: fake wg0, official TV/movie engines, TMDB lookup,
# Prowlarr search against a fake Torznab source, then a fake qBittorrent
# WebUI that writes incomplete/ then complete/. Asserts: incomplete never
# reaches the library; a finished file does; downloads/ does not keep leftover
# videos. Does not care whether Arr completed-download handling or housekeep
# did the rename. Not HAOS. Not Proton. Never starts a torrent client or
# talks to public BitTorrent nodes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/sbin:/sbin:${PATH}"
export PYTHONDONTWRITEBYTECODE=1
export POMPEY_FAKE_VPN=1
export POMPEY_SKIP_SEERR="${POMPEY_SKIP_SEERR:-1}"
export POMPEY_SKIP_QBIT=1
export POMPEY_SKIP_RECYCLARR="${POMPEY_SKIP_RECYCLARR:-1}"
# Sparse fake video; large enough that Arr sample detection usually ignores it.
export POMPEY_FAKE_VIDEO_BYTES="${POMPEY_FAKE_VIDEO_BYTES:-134217728}"

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
mkdir -p "$(dirname "${NGINX_INGRESS_CONF}")"

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
  until ns curl -fsS -o /dev/null --max-time 3 "${url}" 2>/dev/null; do
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

python3 - "${ROOT}/tests/options.json" "${BASHIO_OPTIONS}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
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

# HTTP stubs (Torznab source + download-engine WebUI + Seerr). --hold keeps the
# fake download in incomplete/ so we can prove it never reaches the library.
log "HTTP stubs (Torznab + fake qBittorrent WebUI + Seerr); no torrent client"
ns env \
  POMPEY_FAKE_VIDEO_BYTES="${POMPEY_FAKE_VIDEO_BYTES}" \
  MEDIA_ROOT="${MEDIA_ROOT}" \
  python3 "${ROOT}/tests/lib/fake_source.py" serve \
  --work "${WORK}" \
  --media-root "${MEDIA_ROOT}" \
  --hold \
  >"${WORK}/http-stub.log" 2>&1 &
PIDS+=("$!")

wait_url http://127.0.0.1:8080/api/v2/app/version 20
wait_url http://127.0.0.1:5055/api/v1/settings/public 20
wait_url "http://127.0.0.1:9117/api?t=caps" 20
wait_url http://127.0.0.1:9696/ping 60
ns python3 "${BIN}/prowlarr-arr-proxy" >"${WORK}/prowlarr-arr.log" 2>&1 &
PIDS+=("$!")
wait_url http://127.0.0.1:9698/ping 20
wait_url http://127.0.0.1:8989/ping 60
wait_url http://127.0.0.1:7878/ping 60

export PLEX_URL="" PLEX_TOKEN=""
export INDEXER_URL=http://127.0.0.1:9117
export INDEXER_API_KEY=pompey-dev-source
export QBIT_URL=http://127.0.0.1:8080
export SONARR_URL=http://127.0.0.1:8989
export RADARR_URL=http://127.0.0.1:7878
export PROWLARR_URL=http://127.0.0.1:9696
export SEERR_URL=http://127.0.0.1:5055
export POMPEY_WAIT_TRIES=30
export POMPEY_WAIT_SLEEP=2
export MEDIA_MOVIES="Movies/Not Kid Friendly"
export MEDIA_MOVIES_KID="Movies/Kid Friendly"
export MEDIA_TV="TV/Not Kid Friendly"
export MEDIA_TV_KID="TV/Kid Friendly"
export AFTER_DOWNLOAD="stop_sharing"

stack_python() {
  ns env \
    POMPEY_SECRETS="${POMPEY_SECRETS}" \
    POMPEY_READY="${POMPEY_READY}" \
    MEDIA_ROOT="${MEDIA_ROOT}" \
    MEDIA_MOVIES="${MEDIA_MOVIES}" \
    MEDIA_MOVIES_KID="${MEDIA_MOVIES_KID}" \
    MEDIA_TV="${MEDIA_TV}" \
    MEDIA_TV_KID="${MEDIA_TV_KID}" \
    AFTER_DOWNLOAD="${AFTER_DOWNLOAD}" \
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
    python3 "$@"
}

log "wire localhost engines"
stack_python "${BIN}/wire-stack"
test -f "${POMPEY_READY}/arr-wired"
test -f "${POMPEY_READY}/wired"
test "$(jq -r .search "${POMPEY_READY}/status.json")" = true
grep -q "status.json" "${NGINX_INGRESS_CONF}"
grep -q "setup/proton" "${NGINX_INGRESS_CONF}"

PROWLARR_KEY="$(jq -r .prowlarr_api_key "${POMPEY_SECRETS}")"
PROWLARR=http://127.0.0.1:9696
indexers="$(arr "${PROWLARR}" "${PROWLARR_KEY}" GET "/api/v1/indexer")"
if ! echo "${indexers}" | jq -e 'length >= 1' >/dev/null; then
  echo "Prowlarr has no source indexer after wire-stack: ${indexers}" >&2
  tail -n 80 "${WORK}/prowlarr.log" >&2 || true
  cat "${WORK}/http-stub.log" >&2 || true
  exit 1
fi
log "Prowlarr source indexer: $(echo "${indexers}" | jq -r '.[0].name')"

SONARR_KEY="$(jq -r .sonarr_api_key "${POMPEY_SECRETS}")"
SONARR=http://127.0.0.1:8989
RADARR_KEY="$(jq -r .radarr_api_key "${POMPEY_SECRETS}")"
RADARR=http://127.0.0.1:7878

log "wait for Prowlarr to sync the source into Radarr/Sonarr"
radarr_indexers="[]"
sonarr_indexers="[]"
synced=""
for _ in $(seq 1 20); do
  radarr_indexers="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/indexer" || echo '[]')"
  sonarr_indexers="$(arr "${SONARR}" "${SONARR_KEY}" GET "/api/v3/indexer" || echo '[]')"
  if echo "${radarr_indexers}" | jq -e 'length >= 1' >/dev/null \
     && echo "${sonarr_indexers}" | jq -e 'length >= 1' >/dev/null; then
    synced=1
    break
  fi
  sleep 2
done
if [[ -z "${synced}" ]]; then
  echo "Prowlarr did not sync the source into Radarr/Sonarr" >&2
  echo "radarr indexers: ${radarr_indexers}" >&2
  echo "sonarr indexers: ${sonarr_indexers}" >&2
  echo "---- prowlarr.log (tail) ----" >&2
  tail -n 120 "${WORK}/prowlarr.log" >&2 || true
  echo "---- sonarr.log (tail) ----" >&2
  tail -n 80 "${WORK}/sonarr.log" >&2 || true
  echo "---- http-stub.log ----" >&2
  cat "${WORK}/http-stub.log" >&2 || true
  exit 1
fi
log "Radarr indexer: $(echo "${radarr_indexers}" | jq -r '.[0].name')"
log "Sonarr indexer: $(echo "${sonarr_indexers}" | jq -r '.[0].name')"

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

profile="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/qualityprofile" \
  | jq '[.[] | select(.name=="Default")][0].id // .[0].id')"
root="${MEDIA_ROOT}/Movies/Not Kid Friendly"
add="$(python3 - "${movie}" "${profile}" "${root}" <<'PY'
import json, sys
src = json.loads(sys.argv[1])
movie = {
    "title": src.get("title"),
    "tmdbId": src.get("tmdbId"),
    "year": src.get("year"),
    "titleSlug": src.get("titleSlug"),
    "images": src.get("images") or [],
    "qualityProfileId": int(sys.argv[2]),
    "rootFolderPath": sys.argv[3],
    "monitored": True,
    "minimumAvailability": "announced",
    "addOptions": {"searchForMovie": False},
}
print(json.dumps(movie))
PY
)"

log "add movie (library only — no search, no download)"
add_resp="$(ns curl -sS -w '\n%{http_code}' --max-time 60 -X POST \
  -H "X-Api-Key: ${RADARR_KEY}" -H "Content-Type: application/json" \
  -d "${add}" "${RADARR}/api/v3/movie")"
add_code="$(echo "${add_resp}" | tail -n1)"
add_body="$(echo "${add_resp}" | sed '$d')"
if [[ "${add_code}" != "200" && "${add_code}" != "201" ]]; then
  echo "add movie HTTP ${add_code}: ${add_body}" >&2
  echo "payload: ${add}" >&2
  echo "---- radarr.log (tail) ----" >&2
  tail -n 40 "${WORK}/radarr.log" >&2 || true
  exit 1
fi

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

log "Prowlarr search ${MOVIE} → fake qBittorrent add (no torrent client)"
QBIT_USER="$(jq -r .qbit_user "${POMPEY_SECRETS}")"
QBIT_PASS="$(jq -r .qbit_password "${POMPEY_SECRETS}")"
if ! ns python3 "${ROOT}/tests/lib/fake_source.py" grab \
  --prowlarr "${PROWLARR}" \
  --key "${PROWLARR_KEY}" \
  --query "${MOVIE}" \
  --adds "${WORK}/qbit-adds.jsonl" \
  --user "${QBIT_USER}" \
  --password "${QBIT_PASS}" \
  >"${WORK}/grab.log" 2>&1; then
  echo "Prowlarr search/grab failed" >&2
  cat "${WORK}/grab.log" >&2 || true
  echo "---- prowlarr.log (tail) ----" >&2
  tail -n 80 "${WORK}/prowlarr.log" >&2 || true
  echo "---- http-stub.log ----" >&2
  cat "${WORK}/http-stub.log" >&2 || true
  exit 1
fi
cat "${WORK}/grab.log"
grep -q 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "${WORK}/qbit-adds.jsonl"
log "fake qBittorrent recorded magnet add"

library_root="${MEDIA_ROOT}/Movies/Not Kid Friendly"
downloads="${MEDIA_ROOT}/downloads"

video_files() {
  local dir="$1"
  [[ -d "${dir}" ]] || return 0
  find "${dir}" -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.m4v' \) 2>/dev/null || true
}

nonhidden_files() {
  local dir="$1"
  [[ -d "${dir}" ]] || return 0
  find "${dir}" -type f ! -name '.*' 2>/dev/null || true
}

dump_import_debug() {
  echo "---- housekeep.log ----" >&2
  cat "${WORK}/housekeep.log" >&2 || true
  echo "---- downloads/ ----" >&2
  find "${downloads}" -print >&2 || true
  echo "---- library ----" >&2
  find "${library_root}" -print >&2 || true
  echo "---- radarr.log (tail) ----" >&2
  tail -n 120 "${WORK}/radarr.log" >&2 || true
}

run_housekeep() {
  if ! stack_python "${BIN}/wire-stack" housekeep >>"${WORK}/housekeep.log" 2>&1; then
    echo "wire-stack housekeep failed" >&2
    tail -n 80 "${WORK}/housekeep.log" >&2 || true
    echo "---- radarr.log (tail) ----" >&2
    tail -n 80 "${WORK}/radarr.log" >&2 || true
    exit 1
  fi
}

incomplete_video=""
for _ in $(seq 1 15); do
  incomplete_video="$(video_files "${downloads}/incomplete" | head -n1 || true)"
  if [[ -n "${incomplete_video}" ]]; then
    break
  fi
  sleep 1
done
if [[ -z "${incomplete_video}" ]]; then
  echo "fake qBittorrent never wrote a video under downloads/incomplete" >&2
  find "${downloads}" -print >&2 || true
  cat "${WORK}/http-stub.log" >&2 || true
  exit 1
fi
log "held in incomplete/: ${incomplete_video#"${MEDIA_ROOT}/"}"

# Housekeep (and Arr CDH) run while the file is still downloading.
run_housekeep
run_housekeep
if [[ -n "$(video_files "${library_root}")" ]]; then
  echo "incomplete download leaked into the library" >&2
  dump_import_debug
  exit 1
fi
if [[ -n "$(video_files "${downloads}/complete")" ]]; then
  echo "incomplete download showed up in complete/ before finish" >&2
  dump_import_debug
  exit 1
fi
if [[ ! -f "${incomplete_video}" ]]; then
  echo "incomplete video disappeared before finish: ${incomplete_video}" >&2
  dump_import_debug
  exit 1
fi
log "incomplete file stayed out of the library"

log "mark download finished"
if ! ns curl -fsS -X POST --max-time 10 "http://127.0.0.1:8080/pompey/finish" >/dev/null; then
  echo "fake qBittorrent /pompey/finish failed" >&2
  cat "${WORK}/http-stub.log" >&2 || true
  exit 1
fi

settled=""
library_video=""
for _ in $(seq 1 30); do
  run_housekeep
  library_video="$(video_files "${library_root}" | head -n1 || true)"
  leftover_videos="$(video_files "${downloads}")"
  leftover_complete="$(nonhidden_files "${downloads}/complete")"
  leftover_incomplete="$(nonhidden_files "${downloads}/incomplete")"
  if [[ -n "${library_video}" \
     && -z "${leftover_videos}" \
     && -z "${leftover_complete}" \
     && -z "${leftover_incomplete}" ]]; then
    settled=1
    break
  fi
  sleep 3
done

if [[ -z "${library_video}" ]]; then
  echo "finished download never reached ${library_root}" >&2
  dump_import_debug
  movie_row="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/movie" \
    | jq --argjson t "${TMDB}" '[.[] | select(.tmdbId==$t)][0]')"
  echo "radarr movie: ${movie_row}" >&2
  exit 1
fi
if [[ -z "${settled}" ]]; then
  echo "library has the title but downloads/ still has leftover files" >&2
  dump_import_debug
  exit 1
fi
log "library has ${library_video#"${MEDIA_ROOT}/"}; downloads/ is empty of leftovers"

if [[ ! -f "${library_video}" ]]; then
  echo "library video disappeared: ${library_video}" >&2
  exit 1
fi

movie_row="$(arr "${RADARR}" "${RADARR_KEY}" GET "/api/v3/movie" \
  | jq --argjson t "${TMDB}" '[.[] | select(.tmdbId==$t)][0] | {title, tmdbId, hasFile, path}')"
echo "${movie_row}"
movie_path="$(echo "${movie_row}" | jq -r .path)"
case "${movie_path}" in
  */Movies/Not\ Kid\ Friendly/*) ;;
  *)
    echo "movie path is not under Movies/Not Kid Friendly: ${movie_path}" >&2
    exit 1
    ;;
esac

if ns sh -c "ss -tuanp 2>/dev/null | grep -q '10.2.0.2'" || ns sh -c "ss -tuan 2>/dev/null | grep -q '10.2.0.2'"; then
  log "saw sockets bound to 10.2.0.2 (wg0)"
else
  log "lookup already went through the fake wg0 netns"
fi

log "integration ok: incomplete stayed out of the library; ${title} landed in Movies/Not Kid Friendly; downloads/ did not keep leftover files (no torrent client)"
