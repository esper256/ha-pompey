# Pompey

Search for a movie or TV show from the Home Assistant sidebar. Confirm if we need you. It lands in the right library and Plex notices.

**0.2.10 is the first cut meant for a real Home Assistant OS install.** A title landing in Plex is still unproven. Recyclarr quality profiles, Cloudflare solvers, and picking a specific file are not in this version.

All internet from this app uses Proton WireGuard. If the tunnel is down, internet is dropped.

This app is **not** published as a container image. Copy `pompey/` into `/addons` and let Supervisor build it on the machine. After the tunnel is up, Pompey downloads the household search UI (Seerr) and the hidden engines. First start can take several minutes and a few hundred megabytes. Supervisor waits at most **300 seconds** for Docker to start or stop the container (that is the schema maximum). Engine download happens *after* the container is up, on the wait screen.

## Before you start

1. In Proton, create a **WireGuard** certificate. Enable **NAT-PMP (Port Forwarding)** if you want incoming connections for downloads. Download the `.conf` file.
2. Plex on the LAN (another Docker app is fine) with **port 32400 published on a host IP**, plus a token from that Plex account.
3. At least one **source**: a URL plus API key. Pompey does not ship a catalog of sources. Without this, search will not find releases.
4. Disk that can hold libraries and in-progress downloads on the same filesystem (`/media` is the usual choice). This stack wants a few GB of RAM on top of Home Assistant. A 2 GB Pi is not a target.

## First boot checklist

Do this **before** you start the app:

1. Current `main` (0.2.10). After adding the GitHub Apps repository, look under **Install app**, not the installed-apps list. If you added the URL while the repo was private, remove it and add it again.
2. A Proton **WireGuard** `.conf` on your computer (create a certificate in Proton, NAT-PMP on if you want incoming download ports). You will paste that file after start — not into the HA options list.
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

The GitHub repo can be public. Home Assistant still **does not publish or pull a Docker image**; Supervisor builds `pompey/` on the machine.

**Add as an Apps repository (now that the repo is public):**

1. Settings → Apps → **Install app** (the store, not the list of already-installed apps).
2. ⋮ → Repositories → add `https://github.com/esper256/ha-pompey`.
3. If you added it **while the repo was still private**, remove that repository and add it again. Supervisor keeps the failed clone and will not pick up Pompey until you do.
4. ⋮ → Check for updates. Search the store for **Pompey**. Custom repositories are often at the **bottom**, under a heading named Pompey. It is marked experimental.

**Or copy the folder:** put `pompey/` on the machine as `/addons/pompey` (USB/Samba/SSH), then Check for updates.

If it still does not appear: Settings → System → Logs → **Supervisor**. Look for `Can't read` / `pompey`. An invalid `config.yaml` is skipped with no store card (that is what `timeout: 1800` did). Pompey only lists on **aarch64** and **amd64** (not 32-bit Raspberry Pi). `bash tests/run.sh` now includes that schema check.

Then:

1. Fill **Plex address** (numeric IP), **Plex token**, **source URL**, and **source key**. Those are the only app options.
2. Start **Pompey**. Open it in the sidebar. Paste the Proton WireGuard `.conf` on the wait screen, then wait for search.

If the wait screen asks for Proton, paste the whole file Proton gave you (it starts with `[Interface]`). If search is a blank page after the wait screen, check the app log for `pompey-ingress` / Seerr.

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

Start the app and open **Pompey** in the sidebar. Paste the Proton WireGuard `.conf` you downloaded. Pompey reads PrivateKey, Address, peer PublicKey, and Endpoint from that file and adds keepalive if Proton omitted it. A valid paste starts the tunnel even if this Home Assistant host has no legacy iptables filter table (common on HAOS). The kill switch uses nft when it can; if no firewall table exists, the log says so and the tunnel still comes up.

The kill switch is **OUTPUT** (internet from this app). Home Assistant Ingress to port 8099 is **INPUT** from Supervisor (`172.30.32.2`) and is not blocked. If the tunnel cannot start, the wait screen stays up; the container is not halted.

There is no country dropdown. The Proton file already chose a server. Generate a new file to change region.

Home Assistant’s option list is only Plex and source. It does not ask for WireGuard internals (filename, DNS, LAN CIDRs, log level). Those are fixed: Proton DNS `10.2.0.1`, RFC1918 LAN plus Supervisor `172.30.32.0/23`, media at `/media`.

| Option | Meaning |
| --- | --- |
| Plex address | Numeric IP where Plex listens, usually on the LAN. |
| Plex token | Lets Pompey sign the household UI into Plex and skip most of the first-run wizard. |
| Source URL | One indexer/source base URL. |
| Source key | API key for that source. |

Do not publish download peer ports on the Home Assistant host.

## What you see

Open **Pompey** in the sidebar. If Proton is not configured yet, paste the `.conf` you downloaded. After that, the wait screen shows a progress bar: Proton tunnel, download, start engines, connect search. When that finishes, the same sidebar entry reloads into the search UI (Seerr). You should not need a second bookmark.

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

That includes `tests/test_ha_config.py`, which loads `pompey/config.yaml` and `repository.yaml` through Supervisor’s app schema (the check that would have caught `timeout: 1800`). Tests never start the torrent client or wait on a download. CI is `tests/run.sh` only.
