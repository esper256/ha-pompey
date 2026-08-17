# HA Arr Stack

A Home Assistant OS app repository. The *arr applications (qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr) run **in one Docker image** so they share a single Proton VPN connection.

Home Assistant OS cannot attach one addon’s network namespace to another (the usual Gluetun `network_mode: service:gluetun` pattern). Putting the VPN client and the apps in the same container is the supported way to force all of their internet traffic through Proton. See [docs/DESIGN.md](docs/DESIGN.md).

## Apps

| Folder | Status | What it is |
| --- | --- | --- |
| [`arr-stack`](arr-stack/) | experimental skeleton | Proton VPN (Gluetun) + Ingress launcher. *arr binaries land in a follow-up |

## Install (once a GHCR image is published)

1. Settings → Apps → ⋮ → Repositories
2. Add `https://github.com/esper256/ha-arr-addons`
3. Install **Arr Stack**, paste a Proton WireGuard private key, start it

Until `arr-stack/config.yaml` sets `image:`, Supervisor will build the Dockerfile locally. That is fine for development and not what we want on a Pi.

## Development

Copy `arr-stack/` into `/addons` on an HAOS machine (Samba or SSH addon), then Settings → Apps → Check for updates. Local apps appear at the top of the store.

Architecture, HA options, Ingress, kill switch, updates, and a survey of alexbelgium’s *arr addons are in the [design doc](docs/DESIGN.md).
