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

Reusing those **engines** (or their ideas) is reasonable. Reusing them as **the thing the user looks at** is how we accidentally designed an *arr appliance.

Radarr’s real unique value is not search: it is **keep watching this movie until a better release appears**. If v1 is “I picked this title, pick a torrent, file it, done,” we do not need that loop yet.

## Map the desired steps to the thinnest backends

| Step | Best source of truth | Candidate implementation | Do not use as the UI |
| --- | --- | --- | --- |
| Disambiguation | [TMDB](https://developer.themoviedb.org/) (posters, year, overview, `release_dates` → US certification) | Our app calls TMDB. Same DB Radarr uses. | Radarr “add movie” screen |
| Queue | Our job table | SQLite in addon `/data` | Radarr queue |
| Find magnets/indexers | Indexer HTTP APIs | **Headless Prowlarr** (cookies, caps, *arr-compatible search) *or* Jackett. Our UI calls Prowlarr’s API on localhost. | Prowlarr’s own UI |
| Score / warn | Release name + size + seeders + user prefs | Our scorer. Parse names with something like [guessit](https://github.com/guessit-io/guessit); prefer WEB-DL/Remux over `WEBRip`/`HDTV`/obvious transcodes; flag seed vs quality conflicts. TRaSH custom-format lists are **data**, not a product. | Radarr quality-profile editor |
| BitTorrent | Wire protocol | **qBittorrent** or libtorrent. Bind to `wg0` (see DESIGN.md). Set `savepath` per job. | qBittorrent WebUI as home |
| File by rating | TMDB certification | Rule table in our app: e.g. G/PG/PG-13 → `/media/Kid Friendly Movies/<Title (Year)>/`; R/NC-17/NR/unknown → `/media/Movies/...`. Unknown ratings should **ask**, not guess. | Hoping Radarr tags do this |
| NAS | Same filesystem as Plex libraries | Download into the final folder when possible (no copy). If incomplete must live elsewhere, **move/hardlink** onto the NAS path Plex already sees. | Extra unpack/copy hops |
| Plex rescan | [PMS API](https://developer.plex.tv) | `GET /library/sections/{id}/refresh?path={folder}&X-Plex-Token=…` (partial scan). HA also has `plex.refresh_library` if we want Core to do it. | A Plex plugin |

Plex notify is **easy**. NAS mounts do not deliver inotify to the Plex container; calling refresh after a completed move is the standard fix. Keep it.

v1 can be **movies only**. TV (series/season/episode) is a second product surface.

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
  Ingress UI  ── search / pick / warn / queue  (the product)
  job worker  ── TMDB, Prowlarr API, scorer, filing rules, Plex refresh
  Prowlarr    ── headless indexer adapter (optional later: replace)
  qBittorrent ── headless, bind wg0, per-job save path
  WireGuard   ── Proton, iptables kill switch (DESIGN.md)
  /media/...  ── Kid Friendly Movies | Movies  (NAS)

Plex addon (separate container)
  libraries point at those same folders
  receives refresh API after each completed job
```

User-facing surface: **one page**. Prowlarr/qBittorrent stay off the sidebar unless we add a hidden “advanced” link.

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

Plex kids profiles work when **Kid Friendly Movies** and **Movies** are **two libraries** (or one library with collections — two libraries is simpler for restrictions). Our job is only to drop the file in the right tree with a Plex-friendly name (`Title (Year)/Title (Year).mkv`).

Certification source: TMDB `release_dates` for a configured country (default US). Do not use genre “Family” as a substitute for MPAA.

## What to steal vs what to ignore

Steal:

- TMDB (identity + rating)
- Prowlarr as a **library of indexer connectors**, not as a UI
- qBittorrent as a **download engine**
- TRaSH/guessit **naming conventions**
- alexbelgium-style HA Ingress + `addon_config` + `/media` maps
- native WireGuard in-process (DESIGN.md)

Ignore as the product:

- Sonarr/Radarr Web UIs, calendars, RSS, “wanted”
- Seerr’s “request and wait for Radarr” (wrong interaction model)
- Gluetun (already dropped)
- Plex plugins
- Bazarr/subtitles until the movie pipeline works

## Implementation phases (product order)

1. Ingress search + TMDB disambiguation + job list (no torrent yet).
2. WireGuard + kill switch (already skeletoned) + qBittorrent headless.
3. Indexer search + scorer + confirm UI.
4. Save-path rules + NAS landing.
5. Plex partial refresh.
6. Only then consider TV, monitoring/upgrades, or a hidden Radarr for “keep looking.”

GHCR publishing remains optional (local Dockerfile build still works).
