# HA Arr Stack

A Home Assistant OS app repository. The *arr applications (qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr) run **in one Docker image** so they share a single Proton WireGuard interface.

Home Assistant OS cannot attach one addon’s network namespace to another. Putting the VPN tunnel and the apps in the same container is the supported way to force their internet traffic through Proton. Gluetun is not used: its job is sharing a namespace with *other* containers, which we already have. See [docs/DESIGN.md](docs/DESIGN.md).

## Apps

| Folder | Status | What it is |
| --- | --- | --- |
| [`arr-stack`](arr-stack/) | experimental skeleton | Proton WireGuard (`wg0`) + kill switch + Ingress launcher. *arr binaries land in a follow-up |

## Install (once a GHCR image is published)

1. Settings → Apps → ⋮ → Repositories
2. Add `https://github.com/esper256/ha-arr-addons`
3. Install **Arr Stack**, drop a Proton WireGuard config into the addon config share (or paste the fields), start it

Until `arr-stack/config.yaml` sets `image:`, Supervisor will build the Dockerfile locally. That is fine for development and not what we want on a Pi.

## Development

Copy `arr-stack/` into `/addons` on an HAOS machine (Samba or SSH addon), then Settings → Apps → Check for updates. Local apps appear at the top of the store.

Architecture, HA options, Ingress, kill switch, updates, and a survey of alexbelgium’s *arr addons are in the [design doc](docs/DESIGN.md).
