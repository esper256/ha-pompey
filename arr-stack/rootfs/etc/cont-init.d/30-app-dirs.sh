#!/command/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

for app in qbittorrent prowlarr sonarr radarr bazarr; do
  mkdir -p "/config/${app}"
done

bashio::log.warning "qBittorrent/Prowlarr/Sonarr/Radarr/Bazarr are not packaged in 0.1.1 yet. Enable flags are reserved."
