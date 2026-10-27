#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail
# shellcheck source=/dev/null
source "$(command -v pompey-env)"
write-engine-configs
# Own mutable configuration, policy state and generated media directories only.
# Never chown the existing media tree recursively.
chown -R pompey:pompey \
  "${POMPEY_CONFIG}/radarr" "${POMPEY_CONFIG}/sonarr" \
  "${POMPEY_CONFIG}/prowlarr" "${POMPEY_CONFIG}/seerr" \
  "${POMPEY_CONFIG}/qBittorrent" "${POMPEY_DATA}"
while IFS= read -r folder; do
  mkdir -p "${folder}"
  chown pompey:pompey "${folder}" 2>/dev/null || true
  if ! s6-setuidgid pompey test -w "${folder}"; then
    bashio::exit.nok "Media folder is not writable by UID/GID 1000: ${folder}. Check the share permissions."
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
chown -R pompey:pompey "${POMPEY_READY}"
chmod 755 "${POMPEY_READY}"
