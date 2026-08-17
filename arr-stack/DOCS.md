# Home Assistant App: Arr Stack

Proton VPN plus the *arr applications in **one** container. See the [design doc](../docs/DESIGN.md) for why this is not five addons behind Gluetun.

## Current status

`0.1.0` is a skeleton: Gluetun, an Ingress launcher, and the HA options schema. qBittorrent / Prowlarr / Sonarr / Radarr / Bazarr are not started yet. Enable flags are already in the options UI so they will do something in a later version.

## Configuration

### Required for the VPN

1. In Proton, create a **WireGuard** certificate. Enable **NAT-PMP (Port Forwarding)** if you will torrent.
2. Paste the `PrivateKey` value into **WireGuard private key**.
3. Set **Server countries** to a Proton country you actually have servers in (default `Netherlands`).
4. Leave **VPN type** on `wireguard` unless you have a reason to use OpenVPN.

OpenVPN uses the *OpenVPN-specific* username and password from Proton’s dashboard, not your account password. For port forwarding, append `+pmp` to that username.

The app will not start Gluetun until one of those credential sets is present.

### Important options

| Option | Meaning |
| --- | --- |
| Port forwarding | Restricts Gluetun to Proton P2P/PF servers and pushes the forwarded port into qBittorrent (once that service exists) |
| LAN networks | CIDRs that may be reached *without* the VPN (Jellyfin, SMB, printers). Supervisor’s `172.30.32.0/23` is always added |
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

This app needs `NET_ADMIN` and `/dev/net/tun`. That is a security-rating hit; Ingress and a custom AppArmor profile are meant to offset it.

Do not publish torrent peer ports on the Home Assistant host. Peers should connect through Proton’s forwarded port on the tunnel.

## Updates

Application versions are pinned in the image. Update the app in Home Assistant; do not use each *arr Web UI’s updater.
