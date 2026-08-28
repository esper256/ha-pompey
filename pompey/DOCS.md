# Pompey

Search for a movie or TV show from the Home Assistant sidebar. Confirm if we need you. It lands in the right library and Plex notices.

All internet from this app uses Proton WireGuard. If the tunnel is down, internet is dropped.

This app is **not** published as a container image. Copy `pompey/` into `/addons` and let Supervisor build it on the machine. After the tunnel is up, Pompey downloads the household search UI (Seerr) and the hidden engines onto the config share. First start can take several minutes and a few hundred megabytes.

## Before you start

1. In Proton, create a **WireGuard** certificate. Enable **NAT-PMP (Port Forwarding)** if you want incoming connections for downloads.
2. A Plex server on the LAN, with a token from that Plex account.
3. At least one **source**: a URL plus API key. Pompey does not ship a catalog of sources.
4. Disk that can hold libraries and in-progress downloads on the same filesystem (`/media` is the usual choice). This stack wants a few GB of RAM on top of Home Assistant. A 2 GB Pi is not a target.

## VPN (required)

Either:

1. Copy the downloaded `.conf` to the app config share as `/addon_configs/<hash>_pompey/wireguard/wg0.conf`, or
2. Paste **Private key**, **Address**, **Peer public key**, and **Endpoint** from that file into the app options.

There is no country dropdown. The Proton file already chose a server. Generate a new file to change region.

| Option | Meaning |
| --- | --- |
| WireGuard config | Filename under `/config/wireguard/` (default `wg0.conf`) |
| WireGuard DNS | Proton tunnel DNS, default `10.2.0.1`. Also the NAT-PMP gateway. |
| Port forwarding | Renew a Proton NAT-PMP mapping and apply it to the download engine. |
| LAN networks | Home CIDRs that may be reached without the VPN (Plex, NAS). Supervisor’s `172.30.32.0/23` is always included. |
| Plex address | Where Plex lives, usually on the LAN. |
| Plex token | Lets Pompey sign the household UI into Plex and skip most of the first-run wizard. |
| Source URL | One indexer/source base URL. |
| Source key | API key for that source. |
| Media folder | Root for libraries and downloads (default `/media`). |
| Log level | Verbosity for the app and VPN scripts. |

Do not publish download peer ports on the Home Assistant host.

## What you see

Open **Pompey** in the sidebar. The first boot shows a wait screen with a progress bar: Proton tunnel, download, start engines, connect search. When that finishes, the same sidebar entry reloads into the search UI (Seerr). You should not need a second bookmark.

If the bar stays on the tunnel step, the Proton handshake is not up yet (missing/invalid WireGuard file is the usual cause). Opening `index.html` as a file in the editor is only that wait screen and will never become search.

This cloud/dev VM is not Supervisor. To watch the wait screen here:

```bash
python3 tests/preview.py
```

http://127.0.0.1:8099/

If Plex is not filled in yet, you will land on that UI’s setup wizard instead.

## Storage

```text
/media/Kid Friendly Movies
/media/Movies
/media/Kid Friendly TV
/media/TV
/media/downloads/incomplete
/media/downloads/complete
```

App config (including fetched engines) lives in `/addon_configs/<hash>_pompey/` and `/data`. Restarting does not re-download engines that are already present.

## Tests

Home Assistant OS is not required. Supervisor would write `/data/options.json` from the app options; the suite supplies that file and a small bashio stub.

```bash
bash tests/run.sh
```

Tests never start the torrent client or wait on a download. CI is `tests/run.sh` only.
