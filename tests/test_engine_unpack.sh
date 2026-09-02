#!/usr/bin/env bash
# Engine fetch must unpack Linux Servarr tarballs under /data, not /tmp,
# and must refuse Windows zip/PE. Uses a Prowlarr-shaped fixture plus the
# real linux-musl Prowlarr tar.gz (cached). Never starts a torrent client.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${ROOT}/pompey/rootfs/usr/local/bin"
LIB="${ROOT}/tests/lib/engine_artifacts.py"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pompey-engines.XXXXXX")"
cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "${WORK}"
}
trap cleanup EXIT

export BASHIO_OPTIONS="${ROOT}/tests/options.json"
export POMPEY_CONFIG="${WORK}/config"
export POMPEY_DATA="${WORK}/data/pompey"
export POMPEY_ENGINES="${WORK}/data/engines"
export POMPEY_SECRETS="${POMPEY_DATA}/secrets.json"
export POMPEY_READY="${WORK}/tmp/pompey"
export MEDIA_ROOT="${WORK}/media"
export POMPEY_SKIP_QBIT=1
export POMPEY_SKIP_SEERR=1
export POMPEY_SKIP_SONARR=1
export POMPEY_SKIP_RADARR=1
export POMPEY_SKIP_RECYCLARR=1
export POMPEY_SERVARR_OS=linuxmusl

mkdir -p "${WORK}/bin" "${POMPEY_ENGINES}" "${POMPEY_CONFIG}" "${POMPEY_READY}" \
  "${WORK}/www" "${MEDIA_ROOT}"
cp "${ROOT}/tests/stubs/tar-haos" "${WORK}/bin/tar"
chmod +x "${WORK}/bin/tar" "${ROOT}/tests/with-bashio"
# Real tar for fixture creation lives at /usr/bin/tar; wrapper execs that.
export POMPEY_REAL_TAR="$(command -v tar)"
# command -v tar after PATH change would recurse. Pin before PATH prepend.
if [[ "${POMPEY_REAL_TAR}" != /* ]]; then
  POMPEY_REAL_TAR="/usr/bin/tar"
fi
export PATH="${WORK}/bin:${BIN}:${PATH}"

run() {
  "${ROOT}/tests/with-bashio" "$@"
}

echo "== fetch-engines / AppArmor contract =="
grep -q -- '--no-same-owner' "${BIN}/fetch-engines"
grep -q -- '--no-same-permissions' "${BIN}/fetch-engines"
grep -q '.partial-' "${BIN}/fetch-engines"
grep -q 'TMPDIR=' "${BIN}/fetch-engines"
grep -q 'reject_windows_archive' "${BIN}/fetch-engines"
grep -q 'assert_elf_launcher' "${BIN}/fetch-engines"
grep -q 'POMPEY_SKIP_PROWLARR' "${BIN}/fetch-engines"
grep -q 'POMPEY_SKIP_SONARR' "${BIN}/fetch-engines"
grep -q 'POMPEY_SKIP_RADARR' "${BIN}/fetch-engines"
grep -q 'POMPEY_SKIP_RECYCLARR' "${BIN}/fetch-engines"
grep -q 'identity_current' "${BIN}/fetch-engines"
grep -q 'engines-checked' "${BIN}/fetch-engines"
grep -q 'POMPEY_HOLD_QBIT' "${BIN}/fetch-engines"
grep -q 'note_changed' "${BIN}/fetch-engines"
if grep -q 'already present' "${BIN}/fetch-engines"; then
  echo "fetch-engines still skips on presence instead of identity" >&2
  exit 1
fi
# Staging is under POMPEY_ENGINES (/data/engines), not mktemp in /tmp.
if grep -nE 'tmp="\$\(mktemp -d\)"' "${BIN}/fetch-engines"; then
  echo "fetch-engines still extracts via mktemp (HAOS /tmp denies chmod)" >&2
  exit 1
fi
grep -q 'capability fowner' "${ROOT}/pompey/apparmor.txt"
grep -qF '/tmp/** rwlix' "${ROOT}/pompey/apparmor.txt"
if grep -qF '/tmp/** rwk' "${ROOT}/pompey/apparmor.txt"; then
  echo "apparmor /tmp still rwk (no chmod); GNU tar will fail there" >&2
  exit 1
fi

echo "== fixture artifacts =="
python3 "${LIB}" make-linux "${WORK}/www/Prowlarr.master.linux-musl-core-x64.tar.gz" Prowlarr
python3 "${LIB}" make-linux "${WORK}/www/Sonarr.main.linux-musl-core-x64.tar.gz" Sonarr
python3 "${LIB}" make-linux "${WORK}/www/Radarr.master.linux-musl-core-x64.tar.gz" Radarr
python3 "${LIB}" make-windows-zip "${WORK}/www/Prowlarr.master.windows-core-x64.zip" Prowlarr
python3 "${LIB}" make-pe-tar "${WORK}/www/Prowlarr.master.linux-musl-core-x64-pehost.tar.gz" Prowlarr
test "$(python3 "${LIB}" magic "${WORK}/www/Prowlarr.master.linux-musl-core-x64.tar.gz" | cut -c1-4)" = "1f8b"
test "$(python3 "${LIB}" magic "${WORK}/www/Prowlarr.master.windows-core-x64.zip" | cut -c1-4)" = "504b"

python3 "${LIB}" serve "${WORK}/www" >"${WORK}/server.out" 2>"${WORK}/server.err" &
SERVER_PID=$!
for _ in $(seq 1 50); do
  if grep -q '^PORT=' "${WORK}/server.out" 2>/dev/null; then
    break
  fi
  sleep 0.05
done
port="$(awk -F= '/^PORT=/{print $2; exit}' "${WORK}/server.out")"
test -n "${port}"
BASE="http://127.0.0.1:${port}"

echo "== unpack Prowlarr-shaped linux-musl fixture =="
rm -rf "${POMPEY_ENGINES}/Prowlarr" "${POMPEY_ENGINES}/.partial-Prowlarr"
set +e
log="$(
  POMPEY_PROWLARR_URL="${BASE}/Prowlarr.master.linux-musl-core-x64.tar.gz" \
    run "${BIN}/fetch-engines" 2>&1
)"
rc=$?
set -e
printf '%s\n' "${log}"
if [[ "${rc}" -ne 0 ]]; then
  echo "fixture unpack failed" >&2
  exit 1
fi
grep -q 'linux-musl-core-x64' <<<"${log}"
grep -q 'Prowlarr ready (ELF)' <<<"${log}"
test -x "${POMPEY_ENGINES}/Prowlarr/Prowlarr"
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Prowlarr/Prowlarr"
test -f "${POMPEY_ENGINES}/Prowlarr/System.Xml.ReaderWriter.dll"
# .dll sidecars are PE even on Linux; that must not be treated as Windows.
python3 - "${POMPEY_ENGINES}/Prowlarr/System.Xml.ReaderWriter.dll" <<'PY'
from pathlib import Path
import sys
data = Path(sys.argv[1]).read_bytes()
assert data[:2] == b"MZ", data[:8]
print("linux .NET dll is MZ (expected)")
PY
if [[ -d "${POMPEY_ENGINES}/.partial-Prowlarr" ]]; then
  echo "partial staging left behind" >&2
  exit 1
fi
test -f "${POMPEY_READY}/engines-checked"
test -f "${POMPEY_ENGINES}/.stamps/Prowlarr"
grep -qx 'Prowlarr' "${POMPEY_READY}/engines-changed"

echo "== second fetch of the same artifact is skipped =="
touch "${POMPEY_READY}/wired"
log="$(
  POMPEY_PROWLARR_URL="${BASE}/Prowlarr.master.linux-musl-core-x64.tar.gz" \
    run "${BIN}/fetch-engines" 2>&1
)"
printf '%s\n' "${log}"
grep -q 'Prowlarr current' <<<"${log}"
if grep -q 'Prowlarr ready (ELF)' <<<"${log}"; then
  echo "current Prowlarr was re-unpacked" >&2
  exit 1
fi
if grep -qx 'Prowlarr' "${POMPEY_READY}/engines-changed"; then
  echo "current Prowlarr was listed as replaced" >&2
  exit 1
fi

echo "== different filename replaces the on-disk copy =="
python3 "${LIB}" make-linux "${WORK}/www/Prowlarr.master.linux-musl-core-x64.2.tar.gz" Prowlarr
old_stamp="$(cat "${POMPEY_ENGINES}/.stamps/Prowlarr")"
log="$(
  POMPEY_PROWLARR_URL="${BASE}/Prowlarr.master.linux-musl-core-x64.2.tar.gz" \
    run "${BIN}/fetch-engines" 2>&1
)"
printf '%s\n' "${log}"
grep -q 'Prowlarr ready (ELF)' <<<"${log}"
grep -qx 'Prowlarr' "${POMPEY_READY}/engines-changed"
test "$(cat "${POMPEY_ENGINES}/.stamps/Prowlarr")" != "${old_stamp}"
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Prowlarr/Prowlarr"

echo "== failed update keeps the installed copy =="
log="$(
  POMPEY_PROWLARR_URL="${BASE}/missing-prowlarr.tar.gz" \
    run "${BIN}/fetch-engines" 2>&1
)" || {
  printf '%s\n' "${log}"
  echo "missing artifact must not fail fetch when a copy is installed" >&2
  exit 1
}
printf '%s\n' "${log}"
grep -qi 'keeping the installed copy' <<<"${log}"
test -x "${POMPEY_ENGINES}/Prowlarr/Prowlarr"
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Prowlarr/Prowlarr"

echo "== Windows zip on update keeps the Linux copy =="
log="$(
  POMPEY_PROWLARR_URL="${BASE}/Prowlarr.master.windows-core-x64.zip" \
    run "${BIN}/fetch-engines" 2>&1
)" || {
  printf '%s\n' "${log}"
  echo "Windows zip must not abort an update when a Linux copy is installed" >&2
  exit 1
}
printf '%s\n' "${log}"
grep -qiE 'windows|zip' <<<"${log}"
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Prowlarr/Prowlarr"

echo "== qBittorrent-nox hold skips replace during an active write =="
python3 - "${WORK}/www/x86_64-qbittorrent-nox" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b"\x7fELF" + bytes(64))
PY
cp "${WORK}/www/x86_64-qbittorrent-nox" "${WORK}/www/x86_64-qbittorrent-nox-new"
rm -f "${POMPEY_ENGINES}/qbittorrent-nox"
log="$(
  POMPEY_SKIP_PROWLARR=1 POMPEY_SKIP_QBIT=0 \
    POMPEY_QBIT_URL="${BASE}/x86_64-qbittorrent-nox" \
    run "${BIN}/fetch-engines" 2>&1
)"
printf '%s\n' "${log}"
grep -q 'qBittorrent-nox ready (ELF)' <<<"${log}"
test -x "${POMPEY_ENGINES}/qbittorrent-nox"
log="$(
  POMPEY_SKIP_PROWLARR=1 POMPEY_SKIP_QBIT=0 POMPEY_HOLD_QBIT=1 \
    POMPEY_QBIT_URL="${BASE}/x86_64-qbittorrent-nox-new" \
    run "${BIN}/fetch-engines" 2>&1
)"
printf '%s\n' "${log}"
grep -q 'not replacing the binary' <<<"${log}"
if grep -qx 'qbittorrent-nox' "${POMPEY_READY}/engines-changed"; then
  echo "held qBittorrent-nox was listed as replaced" >&2
  exit 1
fi
export POMPEY_SKIP_QBIT=1

echo "== Sonarr + Radarr fixtures use the same unpack path =="
rm -rf "${POMPEY_ENGINES}/Sonarr" "${POMPEY_ENGINES}/Radarr"
POMPEY_SKIP_PROWLARR=1 POMPEY_SKIP_SONARR=0 POMPEY_SKIP_RADARR=0 \
  POMPEY_SONARR_URL="${BASE}/Sonarr.main.linux-musl-core-x64.tar.gz" \
  POMPEY_RADARR_URL="${BASE}/Radarr.master.linux-musl-core-x64.tar.gz" \
  run "${BIN}/fetch-engines" >/tmp/pompey-fetch-arr.log 2>&1
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Sonarr/Sonarr"
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Radarr/Radarr"

echo "== Windows zip is refused before tar =="
rm -rf "${POMPEY_ENGINES}/Prowlarr"
if POMPEY_SKIP_SONARR=1 POMPEY_SKIP_RADARR=1 \
  POMPEY_PROWLARR_URL="${BASE}/Prowlarr.master.windows-core-x64.zip" \
  run "${BIN}/fetch-engines" >/tmp/pompey-fetch-winzip.log 2>&1; then
  echo "Windows zip must not unpack" >&2
  cat /tmp/pompey-fetch-winzip.log >&2
  exit 1
fi
grep -qiE 'windows|zip' /tmp/pompey-fetch-winzip.log
if [[ -x "${POMPEY_ENGINES}/Prowlarr/Prowlarr" ]]; then
  echo "Windows zip left a Prowlarr launcher" >&2
  exit 1
fi

echo "== gzip with a PE launcher is refused =="
rm -rf "${POMPEY_ENGINES}/Prowlarr"
if POMPEY_PROWLARR_URL="${BASE}/Prowlarr.master.linux-musl-core-x64-pehost.tar.gz" \
  run "${BIN}/fetch-engines" >/tmp/pompey-fetch-pe.log 2>&1; then
  echo "PE launcher tarball must not be accepted" >&2
  cat /tmp/pompey-fetch-pe.log >&2
  exit 1
fi
grep -qiE 'Windows PE|MZ' /tmp/pompey-fetch-pe.log

echo "== live Servarr URLs are linux tarballs, not Windows =="
urls="$(run "${BIN}/fetch-engines" --print-urls)"
printf '%s\n' "${urls}"
while IFS= read -r url; do
  [[ -n "${url}" ]] || continue
  extra=()
  if [[ "${url}" == *linuxmusl* || "${url}" == *os=linuxmusl* ]]; then
    extra=(--require-musl)
  fi
  if [[ "${url}" == *qbittorrent-nox ]]; then
    continue
  fi
  python3 "${LIB}" inspect-url "${url}" "${extra[@]}" | tee "${WORK}/inspect.out"
  if grep -q '^skipped=' "${WORK}/inspect.out"; then
    echo "skip live fetch $(grep '^skipped=' "${WORK}/inspect.out")" >&2
  fi
done <<<"${urls}"
# Explicit HAOS URL (this VM is glibc; print-urls above is linuxmusl via POMPEY_SERVARR_OS).
musl_prowlarr="https://prowlarr.servarr.com/v1/update/master/updatefile?os=linuxmusl&runtime=netcore&arch=x64"
if python3 "${LIB}" inspect-url "${musl_prowlarr}" --require-musl | tee "${WORK}/inspect.out" | grep -q '^skipped='; then
  echo "skip live musl Prowlarr inspect $(grep '^skipped=' "${WORK}/inspect.out")" >&2
fi
musl_sonarr="https://services.sonarr.tv/v1/download/main/latest?version=4&os=linuxmusl&arch=x64"
if python3 "${LIB}" inspect-url "${musl_sonarr}" --require-musl | tee "${WORK}/inspect.out" | grep -q '^skipped='; then
  echo "skip live musl Sonarr inspect $(grep '^skipped=' "${WORK}/inspect.out")" >&2
fi
musl_radarr="https://radarr.servarr.com/v1/update/master/updatefile?os=linuxmusl&runtime=netcore&arch=x64"
if python3 "${LIB}" inspect-url "${musl_radarr}" --require-musl | tee "${WORK}/inspect.out" | grep -q '^skipped='; then
  echo "skip live musl Radarr inspect $(grep '^skipped=' "${WORK}/inspect.out")" >&2
fi

echo "== real Prowlarr linux-musl tar.gz unpack =="
CACHE="${POMPEY_ARTIFACT_CACHE:-${HOME}/.cache/pompey/artifacts}"
archive="$(python3 "${LIB}" cache-url "${musl_prowlarr}" "${CACHE}")"
echo "cached ${archive} ($(wc -c <"${archive}") bytes)"
python3 - "${archive}" <<'PY'
from pathlib import Path
import sys
data = Path(sys.argv[1]).read_bytes()
assert data[:2] == b"\x1f\x8b", data[:8]
name = Path(sys.argv[1]).name.lower()
assert "musl" in name, name
assert "windows" not in name, name
assert name.endswith(".tar.gz"), name
print(f"archive name ok: {Path(sys.argv[1]).name}")
PY
# Serve the real archive with its filename so Content-Disposition is checked.
real_name="$(basename "${archive}")"
ln -sfn "${archive}" "${WORK}/www/${real_name}"
rm -rf "${POMPEY_ENGINES}/Prowlarr"
log="$(
  POMPEY_PROWLARR_URL="${BASE}/${real_name}" \
    run "${BIN}/fetch-engines" 2>&1
)" || {
  printf '%s\n' "${log}"
  echo "real Prowlarr unpack failed" >&2
  exit 1
}
printf '%s\n' "${log}"
grep -q "${real_name}" <<<"${log}" || grep -q 'linux-musl' <<<"${log}"
test -x "${POMPEY_ENGINES}/Prowlarr/Prowlarr"
python3 "${LIB}" assert-elf "${POMPEY_ENGINES}/Prowlarr/Prowlarr"
# Real build has the .dll files that failed chmod on HAOS /tmp.
dll="$(find "${POMPEY_ENGINES}/Prowlarr" -name 'System.Xml.ReaderWriter.dll' | head -n1)"
test -n "${dll}"
test -f "${dll}"
# And a nested locale dir like the field log (ja/pl/...).
test -d "${POMPEY_ENGINES}/Prowlarr/ja" || test -d "${POMPEY_ENGINES}/Prowlarr/pl" \
  || ls -d "${POMPEY_ENGINES}/Prowlarr"/*/ >/dev/null
echo "real Prowlarr linux-musl unpacked; launcher is ELF, .dll sidecars present"

echo "engine unpack tests ok (${WORK})"
