# Home Assistant App: Arr Stack

Proton VPN plus the *arr applications in **one** container. They share a single WireGuard interface (`wg0`). See the [design doc](../docs/DESIGN.md) for why this is not five addons behind Gluetun, and why Gluetun itself is not in the image.

## Current status

`0.1.1` is a skeleton: native Proton WireGuard, iptables kill switch, NAT-PMP helper, Ingress launcher, HA options schema. qBittorrent / Prowlarr / Sonarr / Radarr / Bazarr are not started yet. Enable flags are already in the options UI so they will do something in a later version.

## Configuration

### Required for the VPN

In Proton, create a **WireGuard** certificate. Enable **NAT-PMP (Port Forwarding)** if you will torrent. Then either:

1. Copy the downloaded `.conf` to the addon config share as `/addon_configs/<hash>_arr-stack/wireguard/wg0.conf`, or
2. Paste **Private key**, **Address**, **Peer public key**, and **Endpoint** from that file into the app options.

There is no country dropdown. The Proton file already chose a server. Generate a new file to change region.

### Important options

| Option | Meaning |
| --- | --- |
| WireGuard config | Filename under `/config/wireguard/` (default `wg0.conf`) |
| WireGuard DNS | Proton tunnel DNS, default `10.2.0.1` |
| Port forwarding | NAT-PMP against that DNS/gateway; later versions push the port into qBittorrent |
| LAN networks | CIDRs that may be reached *without* the VPN (Jellyfin, SMB). Supervisor’s `172.30.32.0/23` is always added |
| Enable * | Which processes to start once they are packaged |
| Connection mode | `ingress_noauth` — HA login is enough (do not publish the LAN port to the internet). `ingress_auth` keeps *arr’s own login. `noingress_auth` is LAN-only |

### Storage

Map libraries and downloads onto **the same filesystem** (`/media` is the usual choice) or hardlinks will copy instead of link. A TRaSH-style layout:

```text
/media/torrents/complete
/media/torrents/incomplete
/media/tv
/media/movies
```

Per-app config lives in the addon config share: `/addon_configs/<hash>_arr-stack/`.

## Ingress

Open Web UI is a launcher. Each app will hang off `/sonarr`, `/radarr`, `/prowlarr`, `/bazarr`, `/qbittorrent` under the Ingress path. There is only one sidebar entry because Supervisor allows one Ingress port per app.

## Network

This app needs `NET_ADMIN` (and `/dev/net/tun` if the kernel has no WireGuard module). That is a security-rating hit; Ingress and a custom AppArmor profile are meant to offset it.

Internet egress uses `wg0` plus an iptables OUTPUT drop if `wg0` is down. Apps do not each get their own adapter; they share the container’s default route.

Do not publish torrent peer ports on the Home Assistant host. Peers should connect through Proton’s forwarded port on the tunnel.

## Updates

Application versions are pinned in the image. Update the app in Home Assistant; do not use each *arr Web UI’s updater.
