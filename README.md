# Pompey

**Pompey** is a Home Assistant OS app: one sidebar entry, one search. The household face is [Seerr](https://seerr.dev/). Pompey is the box around it — Proton WireGuard, kill switch, hidden engines, no extra consoles.

Version **0.2.6**. We do not publish a container image. Supervisor builds this app on the machine. After the tunnel is up, the app fetches the official programs it needs.

The plan is [VISION.md](VISION.md). Operator steps (including Plex in another Docker) are [pompey/DOCS.md](pompey/DOCS.md).

## Should I install this on Home Assistant now?

**Yes, if you want to be the first real install and report what breaks.** You need:

- Home Assistant OS (not Container, not this Cursor VM)
- A Proton **WireGuard** `.conf` (NAT-PMP on if you want incoming download ports)
- Plex reachable at a **numeric IP** with port 32400 published (another Docker app is fine — see DOCS)
- A Plex token
- One source as a URL plus API key (we do not ship a catalog)
- A few GB of RAM on top of Home Assistant, and disk on `/media`

Copy `pompey/` into `/addons`, or add `https://github.com/esper256/ha-pompey` as an Apps repository and look under **Settings → Apps → Install app** (not the installed list). If you added the URL while the repo was still private, remove it and add it again. Let Supervisor build, fill Plex IP + token + source, start **Pompey**, open the sidebar, and paste the Proton WireGuard `.conf` on the wait screen. First start can take several minutes after the tunnel is up. Operator checklist: [pompey/DOCS.md](pompey/DOCS.md).

**Not yet a household app.** A request becoming a file on disk that Plex notices has not been proven. Quality profiles are engine defaults (no Recyclarr). If search is a blank page after the wait screen, that is the bug to send back.

## What is done (0.2.6)

| Piece | What “done” means |
| --- | --- |
| Addon skeleton | Supervisor app: Ingress, `NET_ADMIN`, `/dev/net/tun`. HA options are Plex address/token and one source |
| Store + wait branding | Square `icon.png` for the app list; rectangular `logo.png` for the store page and the loading/wait screen |
| Wait screen | Logo + progress (tunnel → download → start → connect), then reload into search. If Proton is missing, a paste box for the `.conf` you downloaded |
| Proton + kill switch | WireGuard from that pasted Proton file; `PersistentKeepalive`; internet OUTPUT only on `wg0` (nft/iptables-nft, then legacy); RFC1918 LAN (Plex, NAS) allowed. If this host has no filter table, the tunnel still starts |
| Runtime fetch | After the tunnel is up: Seerr, TV/movie/indexer engines, download engine. Retries on failure. Supervisor docker start/stop timeout is 300s (schema max); the wait screen covers the longer fetch |
| Ingress + Seerr | Wait page, then nginx → a rewriter that prefixes `/_next` and `/api/v1` with `X-Ingress-Path` so Next.js works under Ingress. Cookies are scoped to that prefix. |
| Local wiring | Engines on localhost; Prowlarr syncs the source to TV/movie engines; Seerr pointed at them and auto-approves. If Plex is missing, a local Seerr account is created so TV/movie engines still get wired. |
| Kid vs general folders | Kid Friendly vs general roots. Poller moves titles by certification (including nested Radarr fields). Unknown → general |
| NAT-PMP | Proton mapped port is pushed into the download engine (always tried; enable NAT-PMP on the Proton certificate) |
| Agent tests | `bash tests/run.sh` (CI). No torrent client. Fake `wg0` smoke. Ingress rewriter unit tests. TMDB lookup via `tests/integration.sh` |

## What is not done yet

| Gap | Notes |
| --- | --- |
| Request → file → Plex | Wiring is written. Tests do **not** download. This is what your HAOS install is for |
| Recyclarr / TRaSH | Engines use their defaults. Auto-grab may pick a poor release |
| Cloudflare challenge solvers | Not v1 |
| Pick a specific file | Not v1 |
| Jellyfin | Plex only |
| Source catalog | You bring one URL + key |

## This VM (not Home Assistant)

You cannot install the addon here. To see only the wait screen:

```bash
python3 tests/preview.py
```

http://127.0.0.1:8099/

```bash
bash tests/run.sh
```

Agents: [AGENTS.md](AGENTS.md).
