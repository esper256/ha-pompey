# Product: one search bar, not an *arr console farm

Status: direction for the next design pass
Related: [DESIGN.md](DESIGN.md) still holds for **HAOS + one container + native Proton WireGuard**. That document assumed the product was “bundle Sonarr/Radarr/Prowlarr/qBittorrent UIs.” This document replaces that product assumption.

The desired experience is a **single HAOS app** whose Ingress UI is:

1. **Search** — type a title.
2. **Disambiguate** — pick the exact movie (year, poster, cast) from a metadata DB.
3. **Queue** — the choice becomes a job.
4. **Find releases** — indexer/torrent search for that exact title (the “Prowlarr step”).
5. **Pick** — score against stated preferences (WEB-DL over a transcode). If there is no clear winner, **warn** (lots of seeds but wrong bitrate vs target bitrate with one seed) and let the user decide.
6. **Download** — BitTorrent, then **file by rules** (e.g. US PG-13 and under → `Kid Friendly Movies/`, otherwise `Movies/`).
7. **Land on the NAS** — download in place or move/hardlink when complete.
8. **Tell Plex** to scan (needed on NAS because inotify often does not fire). Skip only if it turns out hard; it is not hard.

What software runs behind that UI is an implementation detail. Shipping five other products’ Web UIs is not the goal.

## The *arr stack is the wrong primary UX

Sonarr, Radarr, Prowlarr, and qBittorrent are **operator tools**. Each assumes you live in that app: quality profiles, indexers, activity, calendar, RSS, upgrades. They are excellent at *automation after you have already decided the rules*. They are a poor first-run experience for “I want this movie.”

Closest existing product:

| Product | What it gets right | What it refuses to be |
| --- | --- | --- |
| **Seerr / Overseerr / Jellyseerr** | Search bar, TMDB/TVDB disambiguation, queue of requests, Plex library awareness | Explicitly **not** a download UI. Interactive torrent pick was requested and closed as out of scope. It still requires Radarr/Sonarr underneath. |
| **Radarr interactive search** | Lists releases with size/indexer/peers; can grab one | Buried behind “add movie → profiles → search.” Auto-grab is the religion; your “warn if no clear winner” is the opposite. |
| **Radarr custom formats / TRaSH** | WEB-DL vs transcode scoring | Configured as a points spreadsheet, not a conversation. |
| **Radarr root folders** | Multiple library paths | Routing by MPAA rating is a custom script, not a first-class feature. |
| **qBittorrent** | The actual torrent engine | Categories/save paths exist; they are not “this title is PG.” |

Reusing those **engines** (or their APIs) is the point. Reusing them as **the thing the user looks at** is how we accidentally designed an *arr appliance.

Sonarr/Radarr’s hidden value is episode/movie matching and interactive `/release` search, plus optional “keep looking for a better encode.” The product is still our confirm UI, not their calendars.

## Map the desired steps to the thinnest backends

| Step | Best source of truth | Candidate implementation | Do not use as the UI |
| --- | --- | --- | --- |
| Disambiguation | [TMDB](https://developer.themoviedb.org/) (posters, year, overview, `release_dates` → US certification) | Our app calls TMDB. Same DB Radarr uses. | Radarr “add movie” screen |
| Queue | Our job table | SQLite in addon `/data` | Radarr queue |
| Find magnets/indexers | Indexer HTTP APIs | **Headless Prowlarr**, queried *through* Sonarr/Radarr `/release` so TV searches are episode-scoped | Prowlarr’s own UI; raw Prowlarr search for a series title |
| Score / warn | Release name + size + seeders + TRaSH scores | Read quality/custom-format scores from the *arr release payload; our UI warns on seed vs quality conflict | Our own indexer parsers; a hand-maintained regex museum |
| BitTorrent | Wire protocol | **qBittorrent** or libtorrent. Bind to `wg0` (see DESIGN.md). Set `savepath` per job. | qBittorrent WebUI as home |
| File by rating | TMDB certification | Rule table in our app: e.g. G/PG/PG-13 → `/media/Kid Friendly Movies/<Title (Year)>/`; R/NC-17/NR/unknown → `/media/Movies/...`. Unknown ratings should **ask**, not guess. | Hoping Radarr tags do this |
| NAS | Same filesystem as Plex libraries | Download into the final folder when possible (no copy). If incomplete must live elsewhere, **move/hardlink** onto the NAS path Plex already sees. | Extra unpack/copy hops |
| Plex rescan | [PMS API](https://developer.plex.tv) | `GET /library/sections/{id}/refresh?path={folder}&X-Plex-Token=…` (partial scan). HA also has `plex.refresh_library` if we want Core to do it. | A Plex plugin |

Plex notify is **easy**. NAS mounts do not deliver inotify to the Plex container; calling refresh after a completed move is the standard fix. Keep it.

Movies **and TV** are in scope. TV is why Prowlarr alone is not enough (see below).

## Where it should run

### Not a Plex plugin

Plex **removed the plug-in system** for third-party channels in 2018. Legacy Python agents are on the way out (Plex’s own 2026 plan is custom **metadata providers** over HTTP, not in-process plugins). A plugin cannot:

- run a BitTorrent client with `NET_ADMIN` and `wg0`
- own a Proton kill switch
- be a long-running download worker independent of PMS
- survive PMS upgrades

Plex’s new extension point is “tell PMS metadata about files it already has,” not “acquire files.”

### Not inside the HAOS Plex app

The official / community **Plex addon** is Plex Media Server in its own Supervisor container. We cannot inject a torrent+VPN pipeline into that image, share its network namespace, or ship a plugin that PMS will keep. Plex should stay a **separate** app so playback is not on the VPN kill switch (already in DESIGN.md).

This product **talks to** Plex over the LAN/`hassio` network. It does not live in Plex.

### HAOS app is a good home *if* HAOS is already the always-on box

Keep it as one Supervisor app when:

- HAOS is the machine that is always on
- NAS libraries are already mounted (`/media`, `/share`, or HA Storage)
- Proton VPN must wrap indexer + torrent traffic (same one-container constraint as DESIGN.md)
- Ingress is an acceptable “open this from the HA sidebar / Nabu Casa” shell

HAOS is a **weak** home if the real media brain is a NAS/Unraid box and HA is a Pi that should not chew torrents. Then this same app should be a normal Docker Compose project on the NAS, and HA is optional.

The product is **not** “home automation.” HAOS is a convenient appliance + Ingress + mounts + VPN capability. If those stop mattering, leave HA.

### Recommended shape

```text
HAOS app (one container, one netns)
  Ingress UI     ── search / pick / warn / queue  (the product — only user-facing surface)
  job worker     ── TMDB/TVDB search UX, confirm UI, rating→path, Plex refresh
  Prowlarr       ── headless; indexer cat-and-mouse
  Sonarr         ── headless; TV identity, episode/season matching, release search API
  Radarr         ── headless; movie identity, aliases, release search API
  qBittorrent    ── headless; torrents on wg0
  Recyclarr data ── TRaSH custom formats into Sonarr/Radarr (naming quicksand)
  Byparr         ── only if indexers need a Cloudflare solver (Prowlarr talks to it)
  WireGuard      ── Proton, iptables kill switch (DESIGN.md)
  /media/...     ── Kid Friendly {Movies,TV} | {Movies,TV}

Plex addon (separate container)
  libraries point at those same folders
  receives refresh API after each completed job
```

User-facing surface: **one page**. The *arr processes stay off the sidebar. We call their APIs; we do not become their maintainers.

## Preferences and “no clear winner”

Store prefs in HA options, for example:

- resolution ceiling (1080p vs 4K)
- prefer WEB-DL / Remux; penalize `WEBRip`, `HDTV`, `HDTS`, `CAM`
- max size / min size for a runtime
- minimum seeders for auto-pick
- language

**Auto-pick** when one candidate is strictly better on the weighted score *and* seeders ≥ threshold.

**Warn and stop** when:

- best quality has 1 seeder and a worse encode has 200, or
- nothing matches the preferred source type, or
- certification is missing so the kids folder is ambiguous

That confirmation step is the product. Automating it away is how you get Radarr.

## Filing rules vs Plex libraries

Plex kids profiles work when **Kid Friendly Movies**, **Movies**, **Kid Friendly TV**, and **TV** are **separate libraries**. Our job is to drop files in the right tree with Plex-friendly names.

Certification source: TMDB `release_dates` for a configured country (default US). Do not use genre “Family” as a substitute for MPAA.

## Quicksand: reuse the teams who already live in it

The failure mode to avoid is **our** code parsing tracker HTML, scene names, and episode numbering. Those change constantly. Other teams already burn their time there. We take their **APIs and releases**, hide their UIs, and only write the stable glue: search UX, “no clear winner” warnings, rating-based folders, NAS path, Plex notify, VPN.

### Must reuse (moving target; we would lose)

| Project | Quicksand they absorb | How we use it | Do not |
| --- | --- | --- | --- |
| **Prowlarr** | Indexer site HTML/APIs, login cookies, caps, tracker categories. This is the cat-and-mouse you named. Jackett is the older cousin; Prowlarr is the Servarr-native one and the default in 2026. | Headless. Add indexers once. Sonarr/Radarr get them via Prowlarr sync. | Rewrite indexer parsers. Use Jackett unless Prowlarr is missing a tracker we care about. |
| **Byparr** (FlareSolverr’s replacement) | Cloudflare / anti-bot pages in front of indexers. FlareSolverr is effectively archived and losing to current CF. | Optional sidecar Prowlarr already knows how to call. Only if an indexer needs it. | Build a browser solver. Keep using dead FlareSolverr. |
| **Sonarr** | TV is the second quicksand: series aliases, scene vs TVDB vs absolute numbering, daily shows, specials, season packs vs single episodes, “which file is S02E04.” Searching Prowlarr for the series title is the wrong query. | Headless. Our UI picks the show/season/episode (TVDB/TMDB). We add it via API, then `GET /api/v3/release` and grab the chosen row. | Reimplement episode matching. Movies-only forever. |
| **Radarr** | Movie aliases, collections, foreign titles, Plex-friendly `{Movie Title} (Year)` paths, unpack/import. | Same pattern as Sonarr: identity + interactive release API, not their Web UI. | Treat “search Prowlarr for the movie name” as enough forever (it is closer than TV, still worse than Radarr). |
| **qBittorrent** | BitTorrent itself: magnets, DHT, stalling, rechecks, bind-to-interface. Slower-moving than indexers, still not ours. | Headless download client on `wg0`. Sonarr/Radarr already speak its API. | libtorrent-in-process unless qBit becomes a problem. |
| **TRaSH Guides + Recyclarr** | Release-group naming and “WEB-DL vs transcode” tokens change as groups rename. TRaSH maintains the lists; Recyclarr ships them into Sonarr/Radarr custom formats. | Recyclarr (or a one-shot sync) on image/update so quality scoring stays current **without our commits**. Our warn-UI reads the same scores. | Hand-maintaining a regex museum in our repo. |

Prowlarr is **necessary and not sufficient**. It finds *rows on indexers*. Sonarr/Radarr turn “this episode of this show” into the right query and the right file on disk. For TV, skip Sonarr and we *become* Sonarr.

### Stable enough to own (our code, rarely churns)

| Piece | Why it is not quicksand |
| --- | --- |
| Search / disambiguation UI | TMDB/TVDB HTTP APIs are versioned and boring. This *is* the product. |
| Confirm / warn when scores disagree | Our rules on top of Sonarr/Radarr release payloads (seeders, size, quality, custom-format score). |
| Rating → folder | TMDB certifications; a small table (G/PG/PG-13 vs R, TV-PG vs TV-MA). Ratings do not redesign weekly. |
| Plex partial refresh | One documented GET. |
| Proton WireGuard + iptables | DESIGN.md. Not indexer HTML. |

### Do not reuse as the product (wrong job)

- Sonarr/Radarr/Prowlarr/qBittorrent **Web UIs**
- **Seerr / Overseerr** — search is good; they will not do interactive torrent pick; they still need the *arrs
- **Gluetun** — dropped
- **Plex plugins** — dead
- **Bazarr** — subtitle sites are another cat-and-mouse; later, as a hidden engine, not first
- **Jackett** — only as a Prowlarr fallback for a missing tracker

### How we avoid fiddling after it works

1. **Other people’s release cadence, not ours.** Prowlarr/Sonarr/Radarr/qBittorrent are pinned binaries in the image. When indexers break, we bump those versions (linuxserver/official tags) and ship an addon update. We do not patch CSS selectors.
2. **TRaSH via Recyclarr on a timer**, not hardcoded quality logic in our UI.
3. **Our git repo only changes** for UX, filing rules, VPN, and Plex. That is the point of hiding the *arrs.
4. **Hidden monitoring later is free.** Once Sonarr/Radarr are in the box, “keep looking for a better WEB-DL” is a flag, not a rewrite.

Interactive search flow (TV or movie):

1. User disambiguates in **our** UI (TMDB/TVDB).
2. Worker upserts the item in Radarr or Sonarr (set root folder from rating rules).
3. Worker asks that app for releases (`/api/v3/release`) — **Prowlarr is already behind that call**.
4. Our UI scores/warns (seeds vs quality). User confirms or we auto-pick.
5. Worker grabs that release; qBittorrent downloads on `wg0`; *arr imports to the NAS path; we refresh Plex.

## Implementation phases (product order)

1. Ingress search + TMDB/TVDB disambiguation + job list (movies *and* series).
2. WireGuard + kill switch (already skeletoned) + headless qBittorrent.
3. Headless Prowlarr + Radarr + Sonarr; Recyclarr profiles; our confirm UI on `/release`.
4. Rating → root folder + NAS landing + Plex partial refresh.
5. Optional: Byparr if Cloudflare-blocked indexers appear; Bazarr later.

GHCR publishing remains optional (local Dockerfile build still works).
