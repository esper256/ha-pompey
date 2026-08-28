# Pompey

**Pompey** is the name of the whole stack and of the Home Assistant app. One sidebar entry. The household searches for a movie or TV show, picks the right title, and it shows up in the right library. Nobody using it should need operator jargon.

The name is a wink at Pompey — English for Gnaeus Pompeius Magnus — who cleared the Mediterranean of pirates in about forty days, then settled them as farmers. Same trick: the rough tools keep working behind the wall. The household never visits their ports.

## Do not vibe-code the face

[Seerr](https://seerr.dev/) (the 2026 merger of Overseerr and Jellyseerr) is already the polished search / posters / “is it already on Plex?” / request-and-notify UI. A half day of our own search bar will lose to that, obviously. We use their work as the thing you look at. We do not fork it to add a download console; they have spent years refusing to be that.

Seerr is a **request desk**. Search, pick a title, queue, tell Plex’s friends when it is available. Approved requests go to other teams’ engines, which auto-grab from a quality profile. Interactive “pick this file” was asked for and left to die. Routing a title into Kid Friendly vs General by rating is something their users still write webhook scripts for.

So the honest split:

| Step | Who |
| --- | --- |
| Search, posters, pick the title, see if it is already there | **Seerr** (Ingress) |
| Find a release, download, match episodes, land on the NAS | Hidden engines, including **qBittorrent-nox** in this same container |
| All of that on Proton, one box, no extra sidebars | **Pompey** (this app) |
| “This file is popular but the wrong quality” | Not Seerr. Only if we add a small confirm later. |
| Kid-friendly vs general by rating | Not Seerr. A tiny rule after the request, or we ask. |

The family experience of a glued stack **is** Seerr. That is a better first screen than anything we would sketch. Pompey's job is to make that screen the *only* screen, already wired, on a kill-switched tunnel, without a weekend of operator setup.

## What you see

1. **Search** — Seerr. Type a title or browse.
2. **Pick** — the right movie or show.
3. **Request** — it becomes a job. Auto-approve for the household so there is no ticket queue.
4. **Done** — it shows up in Plex. Kid-friendly vs general is our rule on the way to disk, not a Seerr settings page.

Movies and TV use the same search.

## What you never see

Indexer consoles, quality-profile spreadsheets, calendars, download-client dashboards, or Seerr's "connect Radarr" wizard. Pompey fills those connections. Those tools stay other teams’ moving targets; we download their official binaries and run them.

## How it ships

We do **not** publish a container image to Docker Hub, GHCR, or anywhere else.

Home Assistant OS builds a thin Dockerfile on the machine: WireGuard, kill switch, a little supervisor of our own. No posters, no other teams’ programs in the image.

After the tunnel is up, Pompey downloads official upstream artifacts onto the config share and starts them. For Seerr that means unpacking their published image (they do not ship a tarball). For the engines, official Linux binaries. We do not compile their source, and we do not host their bits.

Title art still comes from metadata CDNs at search time.

## Where it runs

One Home Assistant OS app, one container. That is why this is not five community addons glued in the store: Home Assistant OS will not attach those addons to one VPN network namespace, and each addon grows its own sidebar. All internet from this container leaves through Proton WireGuard (`wg0`). If the tunnel is down, internet is dropped. Home LAN (Plex, NAS) is allowed.

Challenge-solver sidecars are not in this starting point.

## v1 locks

These are decided so we can build:

- **Face:** Seerr on Ingress. Auto-approve for the household. We do not vibe-code discovery.
- **Box:** one Home Assistant app, one container, Proton `wg0`, kill switch, no split tunnel.
- **Torrent engine:** **qBittorrent-nox**, in this same process namespace so it cannot leak off the tunnel. Bind traffic to `wg0`. Apply Proton’s NAT-PMP mapped port. No Web UI in the sidebar — the TV/movie engines talk to it on localhost. We fetch a static Linux binary at runtime (not compiled here). Transmission is simpler and worse for this stack; Deluge and rtorrent are more operator UI. Newer clients are not what the matching engines expect yet.
- **Other engines:** official Linux musl tarballs for the TV/movie/indexer apps. Recyclarr/TRaSH quality so auto-grab is usually right.
- **Seerr itself:** they ship Docker, not a tarball. We download *their* published image at runtime and unpack the app directory. We still do not publish an image of our own, and we do not compile them from source.
- **Plex only.** Library folders on `/media` (same filesystem as downloads).
- **Kid vs general:** after a request, TMDB certification routes the root folder. G/PG/PG-13 and TV-Y/TV-G/TV-PG → kid libraries. Everything else, including unknown, → general. We do not guess kid. We do not block the request to ask.
- **Not v1:** picking a specific file when quality and seeds disagree; Cloudflare challenge solvers; Jellyfin; exposing this outside Home Assistant login.
- **Sources:** we do not ship a catalog of indexers. First-run Pompey asks for at least one source as a URL plus key (plain language). No engine UI.

Hardware: this stack wants a few GB of RAM on top of Home Assistant. A 2 GB Pi is not a target.

## This repo

`pompey/` is the Home Assistant app for the whole stack. `0.2.7` is the first cut meant for a real Home Assistant OS try (Proton file pasted on the wait screen, Plex at a numeric IP, one source). Recyclarr/TRaSH quality profiles are not in this cut — engines use their defaults. See the [root README](README.md).
