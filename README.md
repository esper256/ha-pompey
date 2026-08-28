# Pompey

Search for a movie or TV show in Home Assistant. Confirm if we need you. It lands in the right library, and Plex notices.

Pompey is one Home Assistant OS app. The sidebar is search. Downloads, matching, and the VPN stay behind that screen. All internet from this app uses Proton WireGuard. If the tunnel is down, internet is dropped.

This is the user guide for the product we are building. It describes the journey as it should feel. [What is not ready yet](#what-is-not-ready-yet) is honest about the current cut (**0.2.14**). The [roadmap](#roadmap) is how we close the gap.

## How a day with it should feel

1. Open **Pompey** in the Home Assistant sidebar.
2. Search for a title — posters, “already on Plex?”, the right movie or show.
3. Request it. For people in the house, that should just go through.
4. Watch it in **Plex**. Kid titles land in the kid libraries. Everything else, including unknown ratings, lands in general.

You should not bookmark Radarr, Sonarr, Prowlarr, or qBittorrent. You should not SSH in to add a source. You should not keep a spreadsheet of quality profiles. Those programs run inside Pompey. The household face is [Seerr](https://seerr.dev/). Pompey is the box around it: Proton, kill switch, wiring, one sidebar.

Movies and TV use the same search.

## What you need

- **Home Assistant OS** on a 64-bit machine (Intel/AMD or aarch64). Not Home Assistant Container, not Supervised on random Linux, not a 32-bit Pi. This app wants a few GB of RAM on top of Home Assistant.
- A **Proton** account. You will create a WireGuard certificate and download a `.conf` file. Turn on **NAT-PMP (Port Forwarding)** on that certificate if you want incoming connections for downloads.
- **Plex** on your LAN, with port **32400** published on a numeric IP. Another Docker app on the same machine is fine.
- **One source** — a tracker or indexer you already have — as a base URL plus API key. Pompey does not ship a catalog of sources.
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

### 3. Fill the four options

Open the app’s configuration. That is the only form Home Assistant keeps:

| Option | Required? | What to put |
| --- | --- | --- |
| **Source URL** | Yes, if you want search to find releases | Base URL of your indexer/source (this goes to the hidden indexer engine, not to Plex) |
| **Source key** | With the URL | API key for that source |
| **Plex address** | No | Numeric IP, e.g. `http://192.168.1.10:32400`. Skip this if you would rather finish Plex on Pompey’s first screen. |
| **Plex token** | No | Only with the address, to skip the first-run Plex wizard. [How to find a Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |

Leave Plex empty unless you already know the token. The canonical Plex connection is the setup screen inside search.

Hostnames (`plex.local`, `plex`) will not resolve. Proton’s DNS is `10.2.0.1` and does not know your LAN names. Use a numeric IP in Home Assistant **and** on the Plex wizard.

| Where Plex runs | Address to use |
| --- | --- |
| Docker on **this** Home Assistant machine, port 32400 published (or host network) | `http://172.30.32.1:32400` (HA host from the add-on network) or the machine’s LAN IP |
| Docker or Plex on **another** machine on the LAN | `http://192.168.x.x:32400` |
| Hostname only | Will not work. Switch to the IP. |

### 4. Point Plex at the libraries

Create these folders if they do not exist, then add them as Plex libraries (movies vs TV, kid vs general):

```text
/media/Movies
/media/TV
/media/Kid Friendly Movies
/media/Kid Friendly TV
```

Downloads use `/media/downloads/incomplete` and `/media/downloads/complete` on the same disk. You do not add those as Plex libraries.

## First run

1. **Start** Pompey. It should start on Home Assistant reboot after that (`boot: auto`).
2. Open **Pompey** in the sidebar. You will get a wait screen, not search, the first time.
3. **Paste the Proton WireGuard `.conf`** into the box (the whole file, starting with `[Interface]`). That is the file Proton gave you when you created the WireGuard certificate. It is not an app option. There is no country dropdown — the file already chose a server. Generate a new certificate in Proton to change region, then paste again (see [Keeping it up to date](#keeping-it-up-to-date)).
4. Wait. First start downloads several hundred megabytes and can take several minutes. The bar is: tunnel → download → start → connect search. Home Assistant’s own start timeout is 300 seconds; engine download happens **after** the container is up, on this screen.
5. The same sidebar entry **reloads into search**.
6. If you did not fill Plex in the app options, Seerr’s first screen asks which media server. Choose **Plex**, sign in, and enter the **numeric IP**. That wizard creates the first admin. Pompey then points search at the movie and TV engines in the background.
7. Request a title you do not already have. It should be auto-approved for the household.

**Success** is: wait screen finishes, sidebar is search, you can find a title and request it, and later that title is on disk in the right folder and Plex notices.

If the wait screen never leaves, or search is a blank page, send the app log (**Settings → Apps → Pompey → Log**) and which step it stuck on. Do not send the Proton private key or a Plex token.

## What you open (and what you never open)

Daily use is two places: **Home Assistant** (search) and **Plex** (watching). Everything else is inside the add-on, on localhost, wired for you.

| What | Who it is for | Address |
| --- | --- | --- |
| Search, posters, requests | Everyone in the house | Home Assistant sidebar → **Pompey**. That is Seerr, under Home Assistant’s login. There is no extra port to remember and nothing to expose on the internet. |
| Watching | Everyone | Your Plex apps. On the LAN, Plex itself is typically `http://<plex-ip>:32400`. |
| App options (source, optional Plex shortcut) | Whoever installs the app | **Settings → Apps → Pompey → Configuration** |
| Proton account / new WireGuard file | Whoever owns the VPN | Proton’s site, then paste into Pompey if you rotate the file |
| App log | When something is stuck | **Settings → Apps → Pompey → Log** |

These run **inside** Pompey. You are not expected to interact with them in a browser. They are not published on your LAN, and they are not extra Home Assistant sidebar entries. That is deliberate: one container, one VPN, one face.

| Program | Job | Inside the add-on |
| --- | --- | --- |
| [Seerr](https://seerr.dev/) | Search and requests | `127.0.0.1:5055` (this is what the sidebar proxies) |
| Radarr | Movies: pick a release, land it in `/media/Movies` or Kid Friendly Movies | `127.0.0.1:7878` |
| Sonarr | TV, same idea for `/media/TV` | `127.0.0.1:8989` |
| Prowlarr | Your source(s), synced into Radarr and Sonarr | `127.0.0.1:9696` |
| qBittorrent-nox | The download client. Bound to the Proton interface. No Web UI in the sidebar. | `127.0.0.1:8080` |

Do not publish 7878 / 8989 / 9696 / 8080 / 5055 on the Home Assistant host. Do not port-forward download peer ports on that host either — Proton NAT-PMP is how incoming download ports should appear, on the tunnel, not on your house IP.

If a download is stuck or a source needs a second key, the **intended** product is: handle that in Pompey (status, “add another source”, a confirm screen when quality is ambiguous). Until those exist, the log is the supported way in. Opening the engine UIs over SSH is a workaround, not the journey.

## Using it after setup

- **Search and request** in the sidebar. Household members should not see a ticket queue. Seerr can have more than one user; the first admin is whoever completed the Plex wizard.
- **Kid vs general** is a rule after the request, from TMDB certification. G / PG / PG-13 and TV-Y / TV-G / TV-PG go to the kid folders. Anything else, **including unknown**, goes to general. Pompey does not guess kid, and it does not stop the request to ask.
- **Already on Plex** is Seerr’s job. If it is already there, you should see that before you request it.
- **Confirm if we need you** is for the cases the stack cannot decide — not for every request. Picking a specific torrent by hand is not the default path.

Opening `index.html` as a file on your laptop is only the wait screen. It will never become search.

## Keeping it up to date

Three different things get “old,” and they are not updated the same way.

### Home Assistant and Plex

Update those as you already do. Pompey does not replace Plex’s own updater.

### Pompey (this app)

**Settings → Apps → Check for updates.** When a new Pompey version is in this GitHub repo, Supervisor rebuilds the thin image (WireGuard, nginx, scripts) and restarts the add-on. Your Proton file, source, Plex connection, and libraries stay.

That rebuild is how you get wait-screen fixes, Ingress fixes, and wiring fixes. After an update, glance at the sidebar once and confirm search still loads.

### Search UI and engines (Seerr, Radarr, Sonarr, Prowlarr, qBittorrent)

These are **official upstream binaries**, fetched after the tunnel is up, stored under the add-on’s data directory. A restart does **not** re-download them if they are already present. Updating the Pompey add-on today also does **not** bump Radarr’s version by itself.

The product should keep those current for you — through the VPN, without you visiting each engine’s “System → Updates” page, and without you running Recyclarr by hand. That updater is not built yet. See the [roadmap](#roadmap). Until it is:

- You should still update **Pompey** when we ship a wrapper fix.
- You should not be expected to open Radarr/Sonarr to click Update.
- Quality profiles stay at each engine’s defaults until Recyclarr/TRaSH is wired (auto-grab may pick a poor release).

### Proton

The `.conf` is a certificate, not a password you type every day. To change region, create a new WireGuard certificate in Proton (NAT-PMP on if you still want inbound download ports) and paste the new file. The wait screen asks for Proton when none is configured; replacing a working file is still clumsy — that is on the roadmap too.

Proton DNS stays `10.2.0.1`. LAN to Plex and NAS (RFC1918 plus Supervisor’s `172.30.32.0/23`) stays allowed. Incoming to the wait screen from Home Assistant is not blocked if the tunnel fails; you should still see Pompey, on the wait screen, instead of a dead Ingress.

### Your source

If the indexer rotates keys or URLs, change **Source URL** / **Source key** in the app options and restart Pompey. Multiple sources from the UI, without editing options, is roadmap.

## What is not ready yet

The journey above is the target. **0.2.14** is a real Home Assistant OS install of that box, not the finished household app.

| In the guide | On the machine today |
| --- | --- |
| Request → file on disk → Plex notices | Wiring is written. Automated tests never start the torrent client or wait on peers. A title actually landing in Plex is what a real install is still proving. |
| Auto-grab is usually the right quality | Engines use their own defaults. Recyclarr / TRaSH profiles are not applied. |
| “Confirm if we need you” when quality and seeds disagree | Not built. Seerr is not a download console; we will not fork it into one. |
| Engines stay current for years | First fetch only. Already-present binaries are skipped on restart. |
| Add another source from search/settings | One URL + key in Home Assistant options. |
| Replace Proton / change region from the running app | Paste on first wait screen. No later “new .conf” flow. |
| Household members as first-class users | Seerr supports users; we have not productized invites or permissions beyond “first admin is the Plex wizard.” |
| Status when a download is stuck | App log. No in-sidebar job list. |
| Engine Web UIs for operators | Listening on localhost only. Not in the sidebar. Not on the LAN. |
| Cloudflare-protected sources | No challenge solvers. |
| Jellyfin | Plex only. |
| Use this from outside Home Assistant’s login | Out of scope. The sidebar is the access control. |

If search is a blank page after the wait screen, or the Plex button on setup does nothing, that is a bug — send the log. Rebuild so the banner says **0.2.14** if you are on an older wait screen.

## Roadmap

Work that turns the current box into the guide above, in the order it unblocks the household. All of it has to fit **one Home Assistant add-on, one container, Proton `wg0`, Seerr as the only face.**

1. **Prove request → file → Plex** on a real HAOS install. Until that loop is boring, nothing else is the product. Kid/general folders, Plex libraries, and NAT-PMP are already aimed at this.
2. **Engine and Seerr updates.** After the tunnel is up, check official channels (Servarr update APIs, Seerr’s published image, qBittorrent-nox static builds). Replace on-disk copies when upstream moves, then restart those processes. Tie a check to Pompey add-on updates *and* to a periodic run so a wrapper we do not touch for months still refreshes Radarr. Never require the user to open an engine UI to click Update.
3. **Quality profiles (Recyclarr / TRaSH).** Apply a sane default profile inside the container so auto-grab is usually right. Keep it off the sidebar. Refresh those profiles when engines update.
4. **Operator status in the sidebar wait/search chrome** — enough to see “downloading / failed / needs you” without Radarr’s queue. This is ours, not a Seerr fork.
5. **Confirm when we cannot decide.** A small “this file vs that file” step for the rare case. Not v1 if it means becoming a torrent picker. Not a Cloudflare solver.
6. **Sources without the options form.** First source can stay in Home Assistant options (plain language). Adding a second, rotating a key, and “source is down” should be possible from Pompey. Still no indexer catalog shipped in the repo.
7. **Proton file lifecycle.** Replace a working `.conf` (new region, rotated certificate) from the running app. Keep the kill switch. Do not put private keys in the options list.
8. **Household users.** After the Plex wizard, inviting someone who already uses that Plex server should be enough. Auto-approve for the house; no ticket queue.
9. **Optional advanced consoles, still behind Home Assistant login** — path prefixes on the same Ingress (`/radarr`, `/sonarr`, …) with Arr `urlBase` set, for the few people who want the upstream UI. Default off. Never host ports on the LAN. Never a second sidebar app.
10. **Not this product:** Jellyfin, split tunnel, publishing a Docker image, challenge-solver sidecars, exposing search on the public internet.

## Storage

Inside the add-on, media is `/media`:

```text
/media/Kid Friendly Movies
/media/Movies
/media/Kid Friendly TV
/media/TV
/media/downloads/incomplete
/media/downloads/complete
```

App config lives in `/addon_configs/<id>_pompey/`. Fetched engines live in the add-on **data** directory (`/data/engines` inside the container), not the config share.

## If it fails

- Wait screen stuck on Proton: the `.conf` is incomplete, or this host cannot create `wg0`. Paste the full file. Check the log for WireGuard, not for Seerr.
- Wait screen stuck after Proton: engine download or wiring. Log lines are stamped with a time. Do not send keys.
- Sidebar is search but Plex button does nothing: you need **0.2.14** or newer (Ingress used to break Seerr’s `/login` regex).
- Search never finds releases: source URL/key, and give wiring a minute after the Plex wizard (Arr is connected after the first admin exists).
- Plex wizard cannot see the server: numeric IP, port 32400 published, LAN not blocked.

More Home Assistant-specific notes (Supervisor skip reasons, copy-to-`/addons`): [pompey/DOCS.md](pompey/DOCS.md).

## This repository

We do not publish a container image. `pompey/` is the Home Assistant app. [VISION.md](VISION.md) is why the face is Seerr and the engines stay hidden. [CHANGELOG.md](pompey/CHANGELOG.md) is what each version fixed.

Contributors and cloud agents: [AGENTS.md](AGENTS.md).
