# Pompey

**Pompey** is a Home Assistant OS app: one sidebar entry, one search. The household face is [Seerr](https://seerr.dev/). Pompey is the box around it — Proton WireGuard, kill switch, hidden engines, no extra consoles.

Version **0.2.0**, marked experimental. We do not publish a container image. Supervisor builds this app on the machine. After the tunnel is up, the app fetches the official programs it needs.

The plan is [VISION.md](VISION.md). Operator steps are [pompey/DOCS.md](pompey/DOCS.md).

## Should I test this on Home Assistant now?

**No — not as a household app.** Nothing here has been proven on a real Home Assistant OS box with Proton. This cloud/dev VM is not HAOS, so we cannot promise that search → request → file on disk → Plex actually works.

**Only if you want to be the first install and report what breaks.** You will need:

- Home Assistant OS (not Container, not this Cursor VM)
- A Proton **WireGuard** config (NAT-PMP on if you want incoming download ports)
- A Plex URL + token
- One source as a URL plus API key (we do not ship a catalog)
- A few GB of RAM on top of Home Assistant, and disk on `/media`

Expect first start to take several minutes and a few hundred megabytes through the tunnel. Likely snags: Proton handshake, Seerr behind Ingress, Plex first-run wizard. A title landing in Plex is **not** a promised path yet.

If you do try it: copy `pompey/` into `/addons`, let Supervisor build, fill Proton + Plex + source, start **Pompey**, open the sidebar. If the wait screen stays on the tunnel step, Proton is not up.

## What is done (in this repo)

These pieces exist, have tests or a smoke path, and are what 0.2.0 is *trying* to be:

| Piece | What “done” means |
| --- | --- |
| Addon skeleton | `pompey/` is a Supervisor app: Ingress, `NET_ADMIN`, `/dev/net/tun`, options for Proton / Plex / one source / media folder |
| Wait screen | Sidebar shows a progress bar (tunnel → download → start → connect), then reloads into search |
| Proton + kill switch | WireGuard from a file or pasted fields; internet OUTPUT only on `wg0`; LAN (Plex, NAS) allowed |
| Runtime fetch | After the tunnel is up: Seerr, TV/movie/indexer engines, download engine. Nothing extra is baked into the image |
| Local wiring | Engines talk to each other on localhost; Seerr is pointed at them; no extra sidebars |
| Kid vs general folders | Creates Kid Friendly vs general movie/TV roots. A poller moves titles by TMDB certification (unknown → general). **Unit-tested only** |
| NAT-PMP | Proton mapped port is pushed into the download engine (when port forwarding is on) |
| Agent tests | `bash tests/run.sh` (CI). No Home Assistant OS. No torrent client. Fake `wg0` smoke. TMDB lookup of *The Wild Robot* via `tests/integration.sh` |

CI compiles the Dockerfile (push disabled) and runs `tests/run.sh`. That is **not** a Home Assistant install.

## What is not done yet

Do not expect these. They are the reason it is premature for the family:

| Gap | Notes |
| --- | --- |
| A real HAOS + Proton trial | Never completed. First person to install it is finding bugs, not verifying a finished app |
| Request → download → Plex | Wiring is written. Tests deliberately do **not** download. Grab quality, naming, and Plex update are unproven |
| Recyclarr / TRaSH quality profiles | Engines use their defaults. Auto-grab may pick a poor release |
| Seerr on Ingress, for real | Wait page → proxy is written. Next.js behind Home Assistant’s Ingress subpath often breaks; that has not been shaken out on HA |
| Kid routing on live engines | Logic exists; it has not been watched against real Radarr/Sonarr certification fields |
| Cloudflare challenge solvers | Not v1 |
| Pick a specific file when quality and seeds disagree | Not v1 |
| Jellyfin | Plex only |
| Source catalog | You bring one URL + key |
| Image publishing | We will not put this on Docker Hub or GHCR |

## This VM (not Home Assistant)

You cannot install the addon here. To see only the wait screen:

```bash
python3 tests/preview.py
```

http://127.0.0.1:8099/ — that is the wait UI, not Seerr.

Tests supply the `options.json` Supervisor would write:

```bash
bash tests/run.sh
```

A longer agent run (`bash tests/integration.sh`) starts a fake `wg0`, fetches the TV/movie engines, and has Radarr look up *The Wild Robot* on TMDB. It does not start a torrent client and does not wait on a download.

Agents: [AGENTS.md](AGENTS.md).
