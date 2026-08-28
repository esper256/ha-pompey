# Pompey

**Pompey** is the name of the whole stack and of the Home Assistant app. One sidebar entry. The household searches for a movie or TV show, picks the right title, and it shows up in the right library. Nobody using it should need operator jargon.

The name is a wink at Pompey — English for Gnaeus Pompeius Magnus — who cleared the Mediterranean of pirates in about forty days, then settled them as farmers. Same trick: the rough tools keep working behind the wall. The household searches in Seerr. Whoever manages sources opens Prowlarr. Nobody visits Radarr, Sonarr, or qBittorrent.

## Do not vibe-code the face

[Seerr](https://seerr.dev/) (the 2026 merger of Overseerr and Jellyseerr) is already the polished search / posters / “is it already on Plex?” / request-and-notify UI. A half day of our own search bar will lose to that, obviously. We use their work as the thing you look at. We do not fork it to add a download console; they have spent years refusing to be that.

Seerr is a **request desk**. Search, pick a title, queue, tell Plex’s friends when it is available. Approved requests go to other teams’ engines, which auto-grab from a quality profile. Interactive “pick this file” was asked for and left to die. Routing a title into Kid Friendly vs General by rating is something their users still write webhook scripts for.

So the honest split:

| Step | Who |
| --- | --- |
| Search, posters, pick the title, see if it is already there | **Seerr** on host port 5055 (not Ingress — Next.js has no basePath) |
| Find a release, download, match episodes, land on the NAS | Hidden engines, including **qBittorrent-nox** in this same container |
| Add or rotate a source (indexer) | **Prowlarr** on host port 9696. Seerr cannot do this. |
| All of that on Proton, one box, no extra sidebars | **Pompey** (this app) |
| “This file is popular but the wrong quality” | Not Seerr. Only if we add a small confirm later. |
| Kid-friendly vs general by rating | Not Seerr. A tiny rule after the request, or we ask. |

The family experience of a glued stack **is** Seerr on port 5055. That is a better first screen than anything we would sketch. Pompey's job is to make that screen already wired, on a kill-switched tunnel, without a weekend of operator setup. The Home Assistant sidebar is the box (Proton, status, Open search, Open sources), not an iframe of Seerr.

## What you see

1. **Search** — Seerr at `http://<home-assistant>:5055`. Type a title or browse.
2. **Pick** — the right movie or show.
3. **Request** — it becomes a job. Auto-approve for the household so there is no ticket queue.
4. **Done** — it shows up in Plex. Kid-friendly vs general is our rule on the way to disk, not a Seerr settings page.

Movies and TV use the same search.

## What you never see

Quality-profile spreadsheets, calendars, download-client dashboards, or Seerr's "connect Radarr" wizard. Pompey fills those connections. Radarr, Sonarr, and qBittorrent stay other teams’ moving targets on localhost; we download their official binaries and run them. Prowlarr is the exception: sources go out of date, and Seerr has no indexer UI.

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

- **Face:** Seerr on a published host port (5055). No Ingress path rewrite. Auto-approve for the household. We do not vibe-code discovery. The sidebar is Pompey (setup, status, debug). Prowlarr on **9696** is how you add sources; Seerr cannot.
- **Box:** one Home Assistant app, one container, Proton `wg0`, kill switch, no split tunnel.
- **Torrent engine:** **qBittorrent-nox**, in this same process namespace so it cannot leak off the tunnel. Bind traffic to `wg0`. Apply Proton’s NAT-PMP mapped port. No Web UI in the sidebar — the TV/movie engines talk to it on localhost. We fetch a static Linux binary at runtime (not compiled here). Transmission is simpler and worse for this stack; Deluge and rtorrent are more operator UI. Newer clients are not what the matching engines expect yet.
- **Other engines:** official Linux musl tarballs for the TV/movie/indexer apps. Recyclarr/TRaSH quality so auto-grab is usually right.
- **Seerr itself:** they ship Docker, not a tarball. We download *their* published image at runtime and unpack the app directory. We still do not publish an image of our own, and we do not compile them from source.
- **Not v1:** picking a specific file when quality and seeds disagree; Cloudflare challenge solvers; Jellyfin; exposing search or sources on the public internet; stuffing Seerr under Ingress.
- **Plex:** a separate Home Assistant app or another machine. Pompey never runs Plex. Library folders under the media folder option (same filesystem as downloads).
- **Kid vs general:** after a request, TMDB certification routes the root folder. G/PG/PG-13 and TV-Y/TV-G/TV-PG → kid libraries. Everything else, including unknown, → general. We do not guess kid. We do not block the request to ask.
- **Sources:** we do not ship a catalog of indexers. Add or rotate sources in Prowlarr on :9696.

Hardware: this stack wants a few GB of RAM on top of Home Assistant. A 2 GB Pi is not a target.

## This repo

`pompey/` is the Home Assistant app for the whole stack. The household guide (install, first run, which URLs you open, updates, what is not ready, roadmap) is the [root README](README.md). `0.2.32` is the current cut on real Home Assistant OS. Search offers Max / Default / Anything quality on the request (applied on existing installs, not only first boot). Finished downloads import into the library folders; torrents whose files were already moved are dropped from the hidden download engine so they cannot start again. Live Recyclarr sync of TRaSH scores, and Bazarr for missing subtitles, are still later.
