# HA Arr Stack

A Home Assistant OS app whose **product** is one search bar: disambiguate a title, pick a torrent when the scorer is unsure, file it (including kid-friendly vs general libraries), land it on a NAS, tell Plex to scan. See [docs/PRODUCT.md](docs/PRODUCT.md).

Infrastructure (still true): one Docker image so indexer + BitTorrent share a Proton WireGuard interface. Home Assistant OS cannot attach one addon’s network namespace to another. Gluetun is not used. See [docs/DESIGN.md](docs/DESIGN.md).

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

Product UX and why this is not five *arr consoles: [docs/PRODUCT.md](docs/PRODUCT.md).
HAOS networking, WireGuard, Ingress, and publishing: [docs/DESIGN.md](docs/DESIGN.md).
