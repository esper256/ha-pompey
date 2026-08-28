# Pompey

Search for a movie or TV show from the Home Assistant sidebar. Confirm if we need you. It lands in the right library and Plex notices.

**0.2.1 is the first cut meant for a real Home Assistant OS install.** A title landing in Plex is still unproven. Recyclarr quality profiles, Cloudflare solvers, and picking a specific file are not in this version.

All internet from this app uses Proton WireGuard. If the tunnel is down, internet is dropped.

This app is **not** published as a container image. Copy `pompey/` into `/addons` and let Supervisor build it on the machine. After the tunnel is up, Pompey downloads the household search UI (Seerr) and the hidden engines. First start can take several minutes and a few hundred megabytes. Supervisor is allowed up to 30 minutes for that.

## Before you start

1. In Proton, create a **WireGuard** certificate. Enable **NAT-PMP (Port Forwarding)** if you want incoming connections for downloads. Download the `.conf` file.
2. Plex on the LAN (another Docker app is fine) with **port 32400 published on a host IP**, plus a token from that Plex account.
3. At least one **source**: a URL plus API key. Pompey does not ship a catalog of sources. Without this, search will not find releases.
4. Disk that can hold libraries and in-progress downloads on the same filesystem (`/media` is the usual choice). This stack wants a few GB of RAM on top of Home Assistant. A 2 GB Pi is not a target.

## First boot checklist

Do this **before** you start the app:

1. Use the **0.2.1** tree (the first-install PR), not an older copy of `main`.
2. Proton WireGuard `.conf` in the app config share as `wireguard/wg0.conf`.
3. Plex token: open any item on the Plex server in a browser, View Source, search for `X-Plex-Token`, or follow [Plex’s token article](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).
4. Plex address is a **numeric IP** (see table below).
5. In Plex, add libraries that point at these folders (create them if Plex does not see them yet):

```text
/media/Movies
/media/TV
/media/Kid Friendly Movies
/media/Kid Friendly TV
```

6. One source URL + API key.

**Success** is: wait screen finishes, sidebar reloads into search, you can find a title and request it. Whether that file shows up in Plex is what this install is meant to prove.

**If it fails**, send the app log (Settings → Apps → Pompey → Log) plus which wait-screen step it stuck on. Do not send the Proton private key or Plex token.

## Install

1. Copy the `pompey/` folder into `/addons` on the Home Assistant OS machine (USB/Samba/SSH).
2. Settings → Apps → ⋮ → Check for updates. Open **Pompey** and install. Supervisor builds the image locally.
3. Put the Proton `.conf` in the app config share as `wireguard/wg0.conf` (see VPN below).
4. Fill **Plex address** (numeric IP), **Plex token**, **source URL**, and **source key**.
5. Start **Pompey**. Open it in the sidebar. First boot shows a wait screen, then should reload into search.

If the bar stays on the tunnel step, Proton is not up (missing/invalid WireGuard file is the usual cause). If search is a blank page, check the app log for `pompey-ingress` / Seerr.

## Plex in another Docker app

Pompey cannot see unpublished container-to-container DNS names. Proton’s DNS is `10.2.0.1` and will not resolve `plex.local`.

Use a **numeric IP** and a published port:

| Plex setup | Plex address to put in Pompey |
| --- | --- |
| Docker on the **same HA machine**, `-p 32400:32400` (or host network) | `http://172.30.32.1:32400` (HA host from the addon network) or the LAN IP of that machine, e.g. `http://192.168.1.10:32400` |
| Docker on **another machine** on your LAN, port 32400 published | `http://192.168.x.x:32400` |
| Hostname (`plex.local`, `plex`) | Will not work. Switch to the IP. |

The default LAN list already includes `10.0.0.0/8`, `172.16.0.0/12`, and `192.168.0.0/16`, so those IPs are allowed past the kill switch. Supervisor’s `172.30.32.0/23` is always allowed.

## VPN (required)

Either:

1. Copy the downloaded `.conf` to the app config share as `/addon_configs/<hash>_pompey/wireguard/wg0.conf` (**preferred** — Proton files already have keepalive), or
2. Paste **Private key**, **Address**, **Peer public key**, and **Endpoint** from that file into the app options. Pompey adds `PersistentKeepalive = 25` if the file did not.

There is no country dropdown. The Proton file already chose a server. Generate a new file to change region.

| Option | Meaning |
| --- | --- |
| WireGuard config | Filename under `/config/wireguard/` (default `wg0.conf`) |
| WireGuard DNS | Proton tunnel DNS, default `10.2.0.1`. Also the NAT-PMP gateway. |
| Port forwarding | Renew a Proton NAT-PMP mapping and apply it to the download engine. |
| LAN networks | Home CIDRs that may be reached without the VPN (Plex, NAS). Supervisor’s `172.30.32.0/23` is always included. |
| Plex address | Numeric IP where Plex listens, usually on the LAN. |
| Plex token | Lets Pompey sign the household UI into Plex and skip most of the first-run wizard. |
| Source URL | One indexer/source base URL. |
| Source key | API key for that source. |
| Media folder | Root for libraries and downloads (default `/media`). |
| Log level | Verbosity for the app and VPN scripts. |

Do not publish download peer ports on the Home Assistant host.

## What you see

Open **Pompey** in the sidebar. The first boot shows a wait screen with a progress bar: Proton tunnel, download, start engines, connect search. When that finishes, the same sidebar entry reloads into the search UI (Seerr). You should not need a second bookmark.

Opening `index.html` as a file in the editor is only that wait screen and will never become search.

If Plex is not filled in yet, Pompey still creates a local search account and wires the TV/movie engines. You will land on that UI’s Plex setup wizard for the library connection.

## Storage

```text
/media/Kid Friendly Movies
/media/Movies
/media/Kid Friendly TV
/media/TV
/media/downloads/incomplete
/media/downloads/complete
```

App config lives in `/addon_configs/<hash>_pompey/`. Fetched engines live in the addon **data** directory (`/data/engines` inside the container), not the config share. Restarting does not re-download engines that are already present.

## Tests

Home Assistant OS is not required. Supervisor would write `/data/options.json` from the app options; the suite supplies that file and a small bashio stub.

```bash
bash tests/run.sh
```

Tests never start the torrent client or wait on a download. CI is `tests/run.sh` only.
