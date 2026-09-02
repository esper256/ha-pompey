# Pompey

Search for a movie or TV show in Home Assistant. Confirm if we need you. It lands in the right library, and Plex notices.

Pompey is one Home Assistant OS app. The sidebar is the box: Proton, status, a button to search. Search itself is [Seerr](https://seerr.dev/) on this machine’s port **5055**, not an iframe. Downloads, matching, and the VPN stay inside Pompey. All internet from this app uses Proton WireGuard. If the tunnel is down, internet is dropped. **Plex is a separate Home Assistant app** (or another machine). Pompey does not run Plex.

This is the user guide for the product we are building. It describes the journey as it should feel. [What is not ready yet](#what-is-not-ready-yet) is honest about the current cut (**0.2.39**). The [roadmap](#roadmap) is how we close the gap.

## How a day with it should feel

1. Open **Pompey** in the Home Assistant sidebar (setup and status).
2. Open **search** on this Home Assistant machine’s port **5055**. Search for a title — posters, “already on Plex?”, the right movie or show.
3. Request it. For people in the house, that should just go through. Pick **Default** for a 1080p encode, **Max** when the TV and sound system should show off, or **Anything** when the title is obscure and you will take what exists.
4. Watch it in **Plex** (your Plex app, not Pompey). Kid titles land in the kid libraries. Everything else, including unknown ratings, lands in general.

You should not bookmark Radarr, Sonarr, or qBittorrent. Sources (Prowlarr) are on port **9696** because Seerr cannot add indexers. You should not keep a spreadsheet of quality profiles. Those other programs run inside Pompey. The household face is [Seerr](https://seerr.dev/) on port 5055. Pompey is the box around it: Proton, kill switch, wiring, one sidebar for the box.

Movies and TV use the same search.

## What you need

- **Home Assistant OS** on a 64-bit machine (Intel/AMD or aarch64). Not Home Assistant Container, not Supervised on random Linux, not a 32-bit Pi. This app wants a few GB of RAM on top of Home Assistant.
- A **Proton** account. You will create a WireGuard certificate and download a `.conf` file. Turn on **NAT-PMP (Port Forwarding)** on that certificate if you want incoming connections for downloads.
- **Plex** on your LAN as a **separate** Home Assistant app (or another machine), with port **32400** published on a numeric IP. Pompey never installs or runs Plex.
- **One source** — a tracker or indexer you already have — as a base URL plus API key. You add it in **Open sources** (Prowlarr). Pompey does not ship a catalog of sources.
- Disk on **`/media`** (or the media share Home Assistant already maps there) for libraries and in-progress downloads. Same filesystem for both; this stack does not copy finished files across disks.

You do not need Docker Hub, a GitHub Container image, or five community add-ons. Supervisor builds Pompey on the machine. After the tunnel is up, Pompey downloads the official search UI and engines itself.

## Install

### 1. Add this repository

1. In Home Assistant: **Settings → Apps**.
2. Open **Install app** (the store — not the list of apps you already installed).
3. ⋮ → **Repositories** → add:

   `https://github.com/esper256/ha-pompey`

4. ⋮ → **Check for updates**. Search the store for **Pompey**. Custom repositories often sit at the **bottom**. It is marked experimental.

If you added that URL while the GitHub repo was still private, remove the repository and add it again. Supervisor keeps the failed clone and will not list Pompey until you do.

You can instead copy the `pompey/` folder onto the machine as `/addons/pompey` (Samba, USB, or SSH) and Check for updates.

Pompey only lists on **amd64** and **aarch64**. If it still does not appear, **Settings → System → Logs → Supervisor** and look for `pompey` / `Can't read`.

### 2. Install the app

Install **Pompey**. Supervisor compiles a thin image (WireGuard, nginx, our scripts). That can take a few minutes. The search UI and the download engines are **not** in that image yet — they arrive on first run, after Proton is up.

### 3. Say where media lives

Open **Settings → Apps → Pompey → Configuration**. The defaults already match this house: a Media network share named `dlna`, with sibling kid / not-kid folders. Change a field only if the share name or a library folder is different.

| Option | Default |
| --- | --- |
| Media folder | `/media/dlna` |
| Movies | `Movies/Not Kid Friendly` |
| Kid movies | `Movies/Kid Friendly` |
| TV | `TV/Not Kid Friendly` |
| Kid TV | `TV/Kid Friendly` |
| After a title is in the library | Stop sharing |

Those four library folders must be **siblings** (neither library folder sits inside another). In-progress downloads go in `downloads/` under the media folder. Do not add `downloads` as a Plex library.

**After a title is in the library** is not a hidden default. Sharing finished torrents uses RAM and CPU in this app. **Stop sharing** (the default) removes the torrent from qBittorrent once the file is in your library. You can instead share until a 1.0 ratio, or for one day. The library file is kept either way.

If you already saved older defaults (`/media` plus `Movies` / `Kid Friendly Movies`), update the five fields and **restart** so engines pick up the folders.

Then in Plex, scan those same library folders. Adults who should also see kid movies can add the kid movies folder as a **second location** on the adult movie library.

Seerr’s first screen asks for a Plex address. Hostnames (`plex.local`, `plex`) will not resolve: Proton’s DNS is `10.2.0.1` and does not know your LAN names. Use a numeric IP.

| Where Plex runs | Address to type in Seerr |
| --- | --- |
| Docker on **this** Home Assistant machine, port 32400 published (or host network) | `http://172.30.32.1:32400` (HA host from the add-on network) or the machine’s LAN IP |
| Docker or Plex on **another** machine on the LAN | `http://192.168.x.x:32400` |
| Hostname only | Will not work. Switch to the IP. |

## First run

1. **Start** Pompey. It should start on Home Assistant reboot after that (`boot: auto`).
2. Open **Pompey** in the sidebar. You will get a wait screen, not search, the first time.
3. **Paste the Proton WireGuard `.conf`** into the box (the whole file, starting with `[Interface]`). That is the file Proton gave you when you created the WireGuard certificate. It is not a Home Assistant option. There is no country dropdown — the file already chose a server. Generate a new certificate in Proton to change region, then paste again (see [Keeping it up to date](#keeping-it-up-to-date)).
4. Wait. First start downloads several hundred megabytes and can take several minutes. The bar is: tunnel → download → start → connect search. Home Assistant’s own start timeout is 300 seconds; engine download happens **after** the container is up, on this screen.
5. When the bar finishes, the sidebar stays Pompey and shows **Open search** and **Open sources**. Search is `http://<this-home-assistant>:5055/` (Seerr). Sources is `http://<this-home-assistant>:9696/` (Prowlarr) — Seerr cannot add indexers. Bookmark search. Keep the sidebar for status and later actions.
6. Seerr’s first screen asks which media server. Choose **Plex**, sign in, and enter the **numeric IP of your Plex app**. That wizard creates the first admin. Pompey then points search at the movie and TV engines in the background.
7. **Open sources** and set a Prowlarr login on first visit. Add your source there (base URL plus API key). Search cannot find releases until that exists.
8. Request a title you do not already have. It should be auto-approved for the household.

**Success** is: wait screen finishes, **Open search** works, you can find a title and request it, and later that title is on disk in the right folder and Plex notices.

If the wait screen never gets to Open search, or port 5055 does not load, send the app log (**Settings → Apps → Pompey → Log**) and which step it stuck on. Do not send the Proton private key or a Plex token.

## What you open (and what you never open)

Daily use is two places: **search** (`http://<home-assistant>:5055`) and **Plex** (watching). Adding or rotating a source is **Prowlarr** on port **9696**. The Home Assistant sidebar is the box (Proton, status, Open search, Open sources). Radarr, Sonarr, and qBittorrent stay inside the add-on.

| What | Who it is for | Address |
| --- | --- | --- |
| Search, posters, requests | Everyone in the house | `http://<this-home-assistant-ip>:5055` — Seerr, on the LAN. Do not put this on the public internet. |
| Sources (indexers) | Whoever manages what we can grab | `http://<this-home-assistant-ip>:9696` — Prowlarr. Seerr cannot do this. First visit sets a login. Do not put this on the public internet. |
| Pompey (Proton, status, Open search, Open sources) | Whoever installed the app | Home Assistant sidebar → **Pompey**. Always this UI, never rewritten into Seerr. |
| Media folders and after-download | Whoever installed the app | **Settings → Apps → Pompey → Configuration** |
| Watching | Everyone | Your Plex apps. On the LAN, Plex itself is typically `http://<plex-ip>:32400`. Pompey does not run Plex. |
| Proton account / new WireGuard file | Whoever owns the VPN | Proton’s site, then paste into Pompey if you rotate the file |
| App log | When something is stuck | **Settings → Apps → Pompey → Log** |

These run **inside** Pompey. They are not extra Home Assistant sidebar entries. That is deliberate: one container, one VPN.

| Program | Job | Inside the add-on |
| --- | --- | --- |
| [Seerr](https://seerr.dev/) | Search and requests | Published as host **5055** |
| Prowlarr | Your source(s), synced into Radarr and Sonarr | Published as host **9696** |
| Radarr | Movies: pick a release, land it in the movies / kid movies folders you set | `127.0.0.1:7878` (not published) |
| Sonarr | TV, same idea for the TV folders you set | `127.0.0.1:8989` (not published) |
| qBittorrent-nox | The download client. Bound to the Proton interface. No Web UI in the sidebar. | `127.0.0.1:8080` (not published) |
| Plex | Watching. **Not in this add-on.** | Your other app / machine, usually `:32400` |

Do not publish 7878 / 8989 / 8080 on the Home Assistant host. **Do** leave 5055 and 9696 published (Supervisor maps them by default; you can change the host ports in the app’s network settings). Do not port-forward download peer ports on that host — Proton NAT-PMP is how incoming download ports should appear, on the tunnel, not on your house IP. Do not port-forward 5055 or 9696 to the internet.

If a download is stuck, the **intended** product is still: handle that in Pompey. Until that exists, the log is the supported way in. Opening Radarr/Sonarr over SSH is a workaround, not the journey. Adding another source is Prowlarr on :9696.

## Using it after setup

- **Search and request** at `http://<home-assistant>:5055`. The sidebar is the box, not an iframe of search. Household members should not see a ticket queue. Seerr can have more than one user; the first admin is whoever completed the Plex wizard.
- **Kid vs general** is a rule after the request, from TMDB certification. G / PG / PG-13 and TV-Y / TV-G / TV-PG go to the kid folders. Anything else, **including unknown**, goes to general. Pompey does not guess kid, and it does not stop the request to ask.
- **After a title is in the library** is in **Settings → Apps → Pompey → Configuration**. Stop sharing is the default so finished torrents do not sit in RAM. Share to a 1.0 ratio or for one day if you want to give a little back. The library file is kept.
- **Already on Plex** is Seerr’s job. If it is already there, you should see that before you request it.
- **Confirm if we need you** is for the cases the stack cannot decide — not for every request. Picking a specific torrent by hand is not the default path.
- **Open sources → History.** Empty Query is not always the Seerr title. Click the row: **top100** (or similar browse) means the source was asked with no name — that is not the request. **Parameters** with IMDb/TMDb is an ID search. The movie name in Query is a title search. RSS is empty Query, empty Parameters, event type RSS. After **0.2.25**, Arr is told to title-search so a request should show the movie name on every source that actually synced into Radarr/Sonarr. A source that fails Prowlarr’s category test (or is blocked by CloudFlare) never becomes an Arr indexer — the app log warns when that happens.

Opening `index.html` as a file on your laptop is only the wait screen. It will never become search.

## Keeping it up to date

Three different things get “old,” and they are not updated the same way.

### Home Assistant and Plex

Update those as you already do. Pompey does not replace Plex’s own updater.

### Pompey (this app)

**Settings → Apps → Check for updates.** When a new Pompey version is in this GitHub repo, Supervisor rebuilds the thin image (WireGuard, nginx, scripts) and restarts the add-on. Your Proton file, source, Plex connection, and libraries stay.

That rebuild is how you get wait-screen fixes, Ingress fixes, and wiring fixes. After an update, glance at the sidebar once and confirm search still loads.

### Search UI and engines (Seerr, Radarr, Sonarr, Prowlarr, qBittorrent)

These are **official upstream binaries**, fetched after the tunnel is up, stored under the add-on’s data directory. A restart checks upstream and skips the download when the on-disk copy is already current. Pompey also re-checks about once a day while the add-on stays up. Updating the Pompey add-on is not how Radarr gets a new version — that clock is separate.

The product keeps those current for you — through the VPN, without you visiting each engine’s “System → Updates” page, and without you running Recyclarr by hand. See [docs/arr-auto-update.md](docs/arr-auto-update.md).

- You should still update **Pompey** when we ship a wrapper fix.
- You should not open Radarr/Sonarr to click Update, and Arr’s in-app updater stays off.
- Quality profiles: **0.2.38** puts three choices on the Seerr request: Max, Default, Anything. Recyclarr applies TRaSH Guides to Default and Max (Anything stays ours). **0.2.31** is the cut that can actually apply named profiles on this install (earlier cuts died on a Sonarr free-space field before quality ran).

### Proton

The `.conf` is a certificate, not a password you type every day. To change region, create a new WireGuard certificate in Proton (NAT-PMP on if you still want inbound download ports) and paste the new file. The wait screen asks for Proton when none is configured; replacing a working file is still clumsy — that is on the roadmap too.

Proton DNS stays `10.2.0.1`. LAN to Plex and NAS (RFC1918 plus Supervisor’s `172.30.32.0/23`) stays allowed. Incoming to the wait screen from Home Assistant is not blocked if the tunnel fails; you should still see Pompey, on the wait screen, instead of a dead Ingress.

### Your source

A private tracker with a stable API key is set-and-forget. Prowlarr copies that one source to Radarr and Sonarr; nothing in the Arr stack (and not Recyclarr, which is quality profiles) rotates indexers for you. Public indexers die, change URLs, and hide behind Cloudflare — those are always in flux, and Pompey does not pick replacements. Add or rotate a source in **Open sources** (Prowlarr on :9696). Seerr cannot do this. There is no Home Assistant options field for a source.

## What is not ready yet

The journey above is the target. **0.2.39** is a real Home Assistant OS install of that box, not the finished household app.

| In the guide | On the machine today |
| --- | --- |
| Request → file on disk → Plex notices | **0.2.27** imports a finished torrent from `downloads/complete` into the Movies/TV folder Seerr used. **0.2.29** drops the hidden qBittorrent torrent if those files were already moved, so it cannot start downloading them again. Automated tests never start the torrent client. |
| Auto-grab is usually the right quality | **0.2.38** offers **Max** / **Default** / **Anything** on the Seerr request. Default is TRaSH HD (1080p WEB/Bluray). Max is TRaSH UHD (4K encodes, 1080p fallback, no remux). Anything takes CAM if that is all there is. Recyclarr refreshes TRaSH scores after the tunnel is up (and about daily). |
| Language, subtitles, dual audio | **0.2.38** uses Arr’s original-audio custom format (not Home Assistant options, not a Seerr language picker). Default/Max skip a dub when the original exists. Anything still takes whatever is left. Missing subtitles after the file lands are later (Bazarr). Playback language is Plex. |
| “Confirm if we need you” when quality and seeds disagree | Not built. Seerr is not a download console; we will not fork it into one. |
| Engines stay current for years | **0.2.39** fetches official artifacts on add-on start and about daily, replaces a binary only when upstream has moved, restarts that process, and re-runs wire. Arr’s BuiltIn updater stays off. A failed check keeps the previous copy. Recyclarr still also re-syncs TRaSH JSON through the tunnel. Decision and native-update table: [docs/arr-auto-update.md](docs/arr-auto-update.md). |
| Add another source from search/settings | **Open sources** (Prowlarr :9696). A Pompey-native source UI is still roadmap. |
| Replace Proton / change region from the running app | Paste on first wait screen. No later “new .conf” flow. |
| Household members as first-class users | Seerr supports users; we have not productized invites or permissions beyond “first admin is the Plex wizard” and advanced-request so the quality dropdown shows. |
| Status when a download is stuck | App log. No in-sidebar job list. The sidebar shows Proton tunnel totals and a short bandwidth graph. |
| Engine Web UIs for operators | Radarr/Sonarr/qBittorrent stay localhost. Prowlarr is on :9696 for sources. |
| Cloudflare-protected sources | No challenge solvers. |
| Jellyfin | Plex only. |
| Use this from outside the house | Out of scope. Search is on the LAN at :5055. Sources at :9696. Do not port-forward either. |

If search is a blank page on port 5055, or the Plex button on setup does nothing, that is a bug — send the log. Rebuild so the banner says **0.2.39** so engines keep current without a Pompey bump per Radarr release. Rebuild to **0.2.38** to drop the Home Assistant language/subtitle options and to let Recyclarr apply TRaSH Default/Max (Anything still takes CAM). Rebuild to **0.2.37** if leftover videos are still sitting loose in `downloads/complete` after the title is in the library. Rebuild to **0.2.34** if leftover torrent *folders* stay after the title is on Plex. Rebuild to **0.2.33** if Seerr never marked a finished title available. Rebuild to **0.2.32** if a finished **video** is still under `downloads/complete`. Rebuild to **0.2.31** if the wait screen says wiring failed (including Sonarr minimum free space ≥ 100) and the request quality list is still Any / HD-720p / Ultra-HD. Rebuild to **0.2.29** if you moved a file by hand and worry qBittorrent will grab it again. Also rebuild if auto-grab picked a huge remux on Default, a Seerr request only title-searched two Prowlarr sources, or you still need tagged app logs, household media-folder defaults, **Open sources**, or an older wait screen.

## Roadmap

Work that turns the current box into the guide above, in the order it unblocks the household. All of it has to fit **one Home Assistant add-on, one container, Proton `wg0`, Seerr as the search face, Prowlarr as the source console.**

1. **Prove request → file → Plex** on a real HAOS install. Until that loop is boring, nothing else is the product. Kid/general folders, Plex libraries, and NAT-PMP are already aimed at this.
2. **Engine and Seerr updates.** **0.2.39** is this: Pompey plays the Docker-orchestrator role. It fetches official artifacts (Servarr update APIs, Seerr’s published image, qBittorrent-nox static builds, Recyclarr GitHub releases), replaces on-disk copies when upstream moves, restarts those processes, re-runs wire. Check on add-on start *and* on a timer so a wrapper we do not touch for months still refreshes Radarr. Never require the user to open an engine UI to click Update. Never bump the Pompey add-on version just because Radarr tagged a release. Arr’s BuiltIn updater stays off (Servarr’s own Docker advice). Design, native-update table, rejected options: [docs/arr-auto-update.md](docs/arr-auto-update.md).
3. **Quality profiles (Recyclarr / TRaSH).** **0.2.38** is Max / Default / Anything on the Seerr request, with Recyclarr applying TRaSH HD to Default and TRaSH UHD (plus 1080p fallback, remux off) to Max. Anything stays a Pompey profile. Recyclarr’s *binary* updates with the other engines as of **0.2.39**; TRaSH JSON was already a daily sync. **Bazarr** (download missing English subtitles after the file is in the library) is a later engine in the same container — not a Seerr fork, and not a per-request language dropdown (Seerr does not have one; Sonarr language profiles are gone).
4. **Operator status in the sidebar wait/search chrome** — enough to see “downloading / failed / needs you” without Radarr’s queue. This is ours, not a Seerr fork.
5. **Confirm when we cannot decide.** A small “this file vs that file” step for the rare case. Not v1 if it means becoming a torrent picker. Not a Cloudflare solver.
6. **Sources without opening Prowlarr.** Adding a source, rotating a key, and “source is down” should be possible from Pompey. Until then, **Open sources** is Prowlarr on :9696. Still no indexer catalog shipped in the repo, and no Home Assistant options for this.
7. **Proton file lifecycle.** Replace a working `.conf` (new region, rotated certificate) from the running app. Keep the kill switch. Do not put private keys in a Home Assistant options list.
8. **Household users.** After the Plex wizard, inviting someone who already uses that Plex server should be enough. Auto-approve for the house; no ticket queue.
9. **Not this product:** Jellyfin, split tunnel, publishing a Docker image, challenge-solver sidecars, exposing search or sources on the public internet, stuffing Seerr under Ingress (Next.js has no basePath; rewriting `/_next` will keep breaking).
10. **Optional Radarr/Sonarr consoles** for the few people who want them — still not a second sidebar app, still not on the LAN by default.

## Storage

Inside the add-on, the media folder option is the parent (default `/media`). Library folders are relative to it. Downloads are always `downloads/` under that parent:

```text
<media folder>/<movies folder>
<media folder>/<kid movies folder>
<media folder>/<tv folder>
<media folder>/<kid tv folder>
<media folder>/downloads/incomplete
<media folder>/downloads/complete
```

App config lives in `/addon_configs/<id>_pompey/`. Fetched engines live in the add-on **data** directory (`/data/engines` inside the container), not the config share.

## If it fails

- Wait screen stuck on Proton: the `.conf` is incomplete, or this host cannot create `wg0`. Paste the full file. Check the log for WireGuard, not for Seerr.
- Sidebar stuck after Proton with no **Open search**: engine download or wiring. Log lines are stamped with a time. Do not send keys.
- **Open search** / port 5055 does not load: check **Settings → Apps → Pompey → Configuration → Network** that 5055/tcp is mapped. Use this machine’s LAN IP, not Home Assistant Cloud.
- **Open sources** / port 9696 does not load: same Network page, 9696/tcp. First visit should ask you to set a Prowlarr login.
- Sidebar used to be a blank Seerr page, or Plex button did nothing: you need **0.2.16** or newer (Ingress used to rewrite Seerr’s JavaScript). Search is now `:5055`, not the iframe.
- Search warns that `/config/seerr` is not a volume: you need **0.2.17**. The data was already persisted; Seerr was looking at a leftover `DOCKER` sentinel.
- Search never finds releases: add a source in **Open sources**, and give wiring a minute after the Plex wizard.
- New titles land on the Home Assistant disk, not the NAS: Media folder should be `/media/<network-storage-name>` (default `/media/dlna`) and the four library fields should be folders Plex already scans. If you installed 0.2.20 with the old `/media` defaults, change Configuration and restart.
- A TV season searches some episodes in **Open sources** then sits: rebuild to **0.2.36**. Housekeeping no longer posts Refresh/Scan on top of a running EpisodeSearch (those commands jump the queue). Remaining missing episodes get one poke after import. The app log still shows Sonarr’s command queue and wanted/missing first. Send that stretch of the log (not API keys).
- Download finished but leftover videos are still loose under `downloads/complete` after the title is in Plex: rebuild to **0.2.37**. **0.2.34** only removed leftover torrent folders; a flat `.mkv` in `complete/` was left behind. Housekeeping now deletes that leftover once the library has that movie or that `SxxExx`, and it will not delete or re-import a path that is a Plex library folder. Replaced files go to `downloads/recycle`. Do not drag files out of `complete/` yourself. Do not use Seerr’s red Remove / Clear Data buttons for this. Plex only sees files that made it into the library folders.
- Plex wizard cannot see the server: numeric IP of **your Plex app**, port 32400 published, LAN not blocked. Pompey does not contain Plex.

More Home Assistant-specific notes (Supervisor skip reasons, copy-to-`/addons`): [pompey/DOCS.md](pompey/DOCS.md).

## This repository

We do not publish a container image. `pompey/` is the Home Assistant app. [VISION.md](VISION.md) is why the face is Seerr and the engines stay hidden. [CHANGELOG.md](pompey/CHANGELOG.md) is what each version fixed.

Contributors and cloud agents: [AGENTS.md](AGENTS.md).
