#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"
write-engine-configs
python3 /usr/local/bin/pompey_storage.py validate-expected
# Applications share the container's root identity for HAOS network storage.
# Preserve existing ownership; no recursive media or configuration changes.
media_failure() {
  while IFS= read -r detail; do
    bashio::log.error "${detail}"
  done < <(python3 /usr/local/bin/pompey_storage.py describe "${folder}")
  bashio::exit.nok "Media folder is not usable by the application user (UID/GID 0): ${folder}. See the ownership, permissions and storage check above."
}
while IFS= read -r folder; do
  mkdir -p "${folder}" || media_failure
  if ! python3 /usr/local/bin/pompey_storage.py check "${folder}"; then
    media_failure
  fi
done < <(PYTHONPATH=/usr/local/bin python3 - <<'FOLDERS'
import pompey_common as p
print("\n".join([
    p.media_root()+'/downloads', p.media_root()+'/downloads/By Rating',
    p.downloads_recycle(), p.downloads_complete(), p.downloads_manual(),
    p.media_root()+'/downloads/incomplete', p.movies_dir(), p.movies_kid_dir(),
    p.tv_dir(), p.tv_kid_dir(), p.movies_auto_dir(), p.tv_auto_dir(),
]))
FOLDERS
)
python3 /usr/local/bin/pompey_storage.py baseline
chmod 755 "${POMPEY_READY}"
