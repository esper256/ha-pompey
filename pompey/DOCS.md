# Pompey

Search for a movie or TV show from the Home Assistant sidebar. It lands in the right library and Plex is told to scan.

`0.1.1` is the starting point: Proton WireGuard, an iptables kill switch, a NAT-PMP helper, and a placeholder screen. The real face will be Seerr, fetched at runtime. That is not wired yet.

This app is **not** published as a container image. Copy `pompey/` into `/addons` and let Supervisor build it on the machine. Artwork and (later) hidden engines are downloaded while the app runs, after the VPN is up.

## VPN (required)

In Proton, create a **WireGuard** certificate. Enable **NAT-PMP (Port Forwarding)** if you want incoming connections for downloads. Then either:

1. Copy the downloaded `.conf` to the app config share as `/addon_configs/<hash>_pompey/wireguard/wg0.conf`, or
2. Paste **Private key**, **Address**, **Peer public key**, and **Endpoint** from that file into the app options.

There is no country dropdown. The Proton file already chose a server. Generate a new file to change region.

| Option | Meaning |
| --- | --- |
| WireGuard config | Filename under `/config/wireguard/` (default `wg0.conf`) |
| WireGuard DNS | Proton tunnel DNS, default `10.2.0.1`. Also the NAT-PMP gateway. |
| Port forwarding | Renew a Proton NAT-PMP mapping. |
| LAN networks | Home CIDRs that may be reached without the VPN (Plex, NAS). Supervisor’s `172.30.32.0/23` is always included. |

All internet from this container uses `wg0`. If the tunnel is down, internet is dropped. Do not publish download peer ports on the Home Assistant host.

## Storage

Libraries and in-progress downloads should share a filesystem (`/media` is the usual choice) so completed titles can land in place:

```text
/media/Kid Friendly Movies
/media/Movies
/media/Kid Friendly TV
/media/TV
```

App config lives in `/addon_configs/<hash>_pompey/`.
