# Changelog

## 0.2.38

- Home Assistant no longer has preferred language / anime audio / subtitles options. Those scored release *names* and did not match how Arr or Seerr actually pick audio. Default and Max now use Arr’s original-language custom format (same mechanism as TRaSH Language: Not Original): a French dub of an English title loses; a French film still matches French. Anything still takes whatever exists. Dual-audio stays a small tie-break. Playback language stays in Plex. Leftover “Pompey English dub/subs” custom formats from 0.2.28 stay at score 0. Rebuild so the banner says **0.2.38**.

## 0.2.37

- Housekeeping will not delete anything that is not a leftover under `downloads/complete`, and it will not scan or import if that folder overlaps a Plex library path. Re-import is skipped whenever the library already has that movie or that `SxxExx` (even if Arr forgot `hasFile`) so a Move cannot recycle-delete the Plex file. Radarr/Sonarr now send replaced files to `downloads/recycle` and do not auto-empty it. qBittorrent still forgets torrents with `deleteFiles=false`. Rebuild so the banner says **0.2.37**.

## 0.2.36

- A season that searched 9…4 then sat is no longer starved by housekeeping. Refresh/Scan every five minutes are higher priority than EpisodeSearch, so they ate the rest of the queue. Housekeeping now skips Refresh while a search is running, skips Refresh/Scan when `complete/` has no videos, and pokes remaining wanted/missing episodes *after* import — once per missing-id set, not every cycle.
- Leftover **loose files** in `downloads/complete` (qBittorrent’s flat save path, not a torrent folder) are removed once the library already has that movie or that `SxxExx`. **0.2.34** only deleted leftover folders, so Silo E04/E07 and a finished movie could sit there after Arr had already copied them. If Arr marks `hasFile` but the library folder does not actually have that episode, the leftover is imported instead of deleted. Do not drag those files out by hand.

## 0.2.35

- App log now prints Sonarr/Radarr **command queue** and Sonarr **wanted/missing** at the start of each housekeeping pass (before Refresh/Scan). Use that to see whether a season search stalled (started EpisodeSearch stuck, later episodes still queued) or whether `complete/` is empty while scans still run. Does not change grab/import.
- The same rebuild drops log junk that hid those lines: Seerr debug Plex-scan “already exists”, Arr `NzbDrone` stack traces (that name is Servarr’s internals, not Usenet), ANSI color, API keys and JWTs in exception URLs, and housekeeping repeating the same `complete/` / waiting-title warning every five minutes until something changes. Structured `[Warn]` / `[Error]` stay. Does not skip scans or start extra EpisodeSearch.
- The sidebar wait screen shows **how much has crossed the Proton tunnel** (totals in and out) and a two-minute graph of recent down/up rates, sampled from the `wg0` adapter. Hidden until the tunnel is up. Rebuild so the banner says **0.2.35**, then send the log around a stuck season (not API keys).

## 0.2.34

- Arr imports the **video** (and `.srt`, including a `Subs` folder) into the movie or TV folder Seerr stored. It does not relocate the whole torrent directory, so `downloads/complete` can keep `.nfo` / `.txt` after the title is in Plex. **0.2.33** could also re-import a title Arr had already assigned (the manual-import list often omits `hasFile`), which deleted the library file and the subtitle that came with it. Housekeeping now looks the title up on Radarr or Sonarr, skips that second import, and removes leftover `complete/` folders once the library already has the video. A show with season 1 on disk is still treated as waiting if later seasons are missing. Rebuild so the banner says **0.2.34**. Do not drag leftover extras out by hand.

## 0.2.33

- **0.2.32** did move the movies into the Plex library folder Seerr stored (this house: `Movies/Not Kid Friendly`). Folders left under `downloads/complete` were leftover subtitles and `.nfo` files, not the movie. The app log listed those extras as `still in complete/`, which looked like the import never ran. Housekeeping now warns only when a **video** is still in `complete/`. It also skips re-importing a title Arr already has a file for (re-import was deleting the library copy because there is no recycle bin). New imports bring subtitle files (including a `Subs` folder) into the movie folder with the video — not the whole torrent dump of `.nfo`/`.txt`. After a file lands, Seerr is asked to check Plex and Arr so requests can flip from active to available (Plex recently-added is also every few minutes on its own). Rebuild so the banner says **0.2.33**. Look in the library folders Plex scans, not in `complete/`. Do not drag leftover extras out by hand.

## 0.2.32

- Finished files on a NAS share were staying in `downloads/complete` instead of the Plex library folder. qBittorrent never receives that library path — Seerr tells Radarr/Sonarr the Movies/TV folder (Kid vs Not Kid) when you request; that path lives on the Arr title. Housekeeping does **not** guess the folder from the filename. It asks Arr to import only files that already match a title, into that stored path, and logs unmatched names as `not guessing Kid vs Not Kid`. If Arr never starts the import there is no “failed move” line (a 100% torrent still seeding looks like a normal download). Finished torrents in `complete/` are **stopped** so Arr will treat them as ready; import is a same-share rename (hardlinks off). Rebuild so the banner says **0.2.32**.

## 0.2.31

- The wait screen could stay on **Could not finish connecting search** with Sonarr `MinimumFreeSpaceWhenImporting` must be ≥ 100. **0.2.27** set that field to 0 so a NAS that reports no free space would still import; Sonarr rejects 0, and that PUT runs *before* quality profiles, so Max / Default / Anything never applied. The skip-free-space flag stays on (that is what ignores a 0-byte NAS). The number is 100. A media-management 400 no longer fails wiring. Rebuild so the banner says **0.2.31**.

## 0.2.30

- 0.2.28 could leave the wait screen on **Engines started but wiring failed** and the request quality list on Radarr’s stock names (Any, HD-720p, Ultra-HD). Arr rejects a profile whose quality groups have no id, and it requires every custom format on the profile. Wiring now applies Max / Default / Anything on an existing install, keeps leftover stock names until those three exist, and does not treat a quality-profile hiccup as a full wiring failure. Rebuild so the banner says **0.2.30**.

## 0.2.29

- Do not move files out of `downloads/complete` by hand. After a title is in the library (or you already moved it), Pompey drops the qBittorrent torrent **without deleting files**, so a hidden client cannot start downloading the same thing again. It also asks Radarr/Sonarr to import anything still sitting in `complete/`. Rebuild so the banner says **0.2.29**.

## 0.2.28

- Search offers three quality choices when you request a title: **Max** (remux / 4K / lossless audio, large files on purpose), **Default** (1080p WEB-DL or BluRay encode, about 2.5–8 GB per 150 minutes; under about 1 GB for two hours is rejected), and **Anything** (obscure titles — take what exists, including CAM). The request dropdown is the Seerr advanced-request control; household users now have that permission. Leftover Arr profiles (Any, HD-720p, Ultra-HD, the old HD name) are removed so the list is those three. Rebuild so the banner says **0.2.28**. Already-queued grabs are not cancelled.
- Home Assistant options for **preferred language**, **anime audio** (dual audio by default), and **subtitles**. These score the release *name* (Dual Audio, English Dub, advertised English subs). They are not a per-request language picker — Seerr does not have one — and they do not download missing subtitles after the file lands (Bazarr is later).

## 0.2.27

- A finished download sitting in `downloads/complete` is not the library. Radarr/Sonarr now import into the Movies/TV folder Seerr used. Network shares (this house: `/media/dlna`) often report no free space, which used to skip that move. Rebuild retries completed torrents still in qBittorrent. The app log warns if one is stuck.

## 0.2.26

- Auto-grab prefers **1080p WEB-DL / BluRay encodes**, not remux or 4K. A 26 GB remux was Radarr’s default ranking, not a bad source. The household profile also prefers x265 and WEB-DL, rejects CAM/TS, and caps 1080p size (about 10–15 GB for a two-hour movie). Already-queued grabs are not cancelled. Search still uses this profile after a rebuild.
- **After a title is in the library** is a Home Assistant option (default **stop sharing**). Finished torrents are removed from qBittorrent so they do not sit in RAM forever. You can share to a 1.0 ratio or for one day instead. The library file is kept.

## 0.2.25

- Empty Query in Prowlarr History during a Seerr request is often a **top100 browse**, not an IMDb search: Radarr sent an ID (or nothing), the source ignored it, and listed popular torrents. Arr now talks to Prowlarr through a localhost proxy that advertises title search only, so the movie name goes to every source. Open sources stays on port 9696. After wiring, the app log labels History rows as ID, title, RSS, or browse/top100.
- Prowlarr sources whose list payload omitted RSS/automatic/interactive flags were left unset; those flags are now read from the source detail and turned on.
- Radarr and Sonarr applications get movie vs TV `syncCategories`. After sync, the app log counts Arr indexers against Prowlarr and warns when a source did not land (category test / CloudFlare). That is why a Seerr movie can title-search only two of five sources.
- Wiring no longer POSTs Seerr `/auth/local` once the disk API key exists, so the log is not a loop of invalid pompey@local passwords. Sonarr’s deprecated language-profile GET is skipped.

## 0.2.24

- Enabled Prowlarr sources get RSS, automatic, and interactive search turned on. After wiring, Radarr and Sonarr search titles that still have no file, so a request that hit too few indexers is tried again. You do not wait for the next Arr cycle, and you do not cancel the Seerr request.

## 0.2.23

- The app log is the mux for every hidden service (s6 already joins their stdout). Each line is tagged with the service name. You see when a service **starts** and **stops**. qBittorrent’s file log — previously invisible from Home Assistant — is copied in. Radarr, Sonarr, Prowlarr, Seerr, and nginx were already in this log and now carry the same tag so an error is not anonymous. After wiring, the log lists each Prowlarr source and whether search is on.

## 0.2.22

- Fixed the wait screen getting stuck on **Could not finish connecting search** after a media-folder change. Search already had Radarr; updating the library path is allowed again.

## 0.2.21

- Media folder defaults match this house: `/media/dlna`, with `Movies/Not Kid Friendly`, `Movies/Kid Friendly`, `TV/Not Kid Friendly`, and `TV/Kid Friendly`. Change the Home Assistant options if a share or library folder is named differently. If you already saved the old `/media` defaults, update Configuration once and restart.

## 0.2.20

- Home Assistant options for **where files go**: media folder (default `/media`) plus movies, kid movies, TV, and kid TV folders relative to it. In-progress downloads use `downloads/` under the media folder. Point Plex at those same library folders. A network Media share is `/media/<the name you gave it>`. Saving the options and restarting the app updates an existing install.

## 0.2.19

- Home Assistant no longer has Plex or source fields for this app. Connect Plex in Seerr’s first-run wizard. Add sources with **Open sources** (Prowlarr).

## 0.2.18

- **Open sources** in the sidebar. Prowlarr is on this machine’s port **9696**. First visit asks you to set a login.
- Radarr, Sonarr, and qBittorrent stay unpublished. Search stays on port **5055**.
- Updating keeps existing Prowlarr sources.

## 0.2.17

- Seerr no longer warns that `/config/seerr` is not a volume. Search data already persists.
- Sidebar stays Pompey’s wait and status screen. Search stays on port **5055**.

## 0.2.16

- Search is on this machine’s port **5055**. The sidebar shows **Open search** when ready; it is not an iframe of Seerr.
- Plex is a separate app. Pompey does not run Plex.

## 0.2.15

- Fixed Plex setup on the search screen doing nothing.

## 0.2.14

- Fixed the wait screen never finishing: search opens for the Plex wizard, then the movie and TV engines connect after the first admin exists.

## 0.2.13

- Fixed the first-run Plex button doing nothing.
- Plex in Home Assistant options is optional. You can finish Plex in Seerr. Use a numeric IP (Proton DNS will not resolve LAN names).

## 0.2.12

- Wait screen stays up if search cannot be connected, instead of opening an empty search page.

## 0.2.11

- First start can download the engines on Home Assistant OS (unpack no longer fails).

## 0.2.10

- Proton tunnel stays up (WireGuard config is accepted).

## 0.2.9

- Proton tunnel stays up on Home Assistant OS when network sysctl is read-only.
- App log timestamps WireGuard lines.
- Engines wait until the VPN handshake exists, not only until the Proton file is saved.

## 0.2.8

- A failed Proton tunnel no longer takes down the whole app. The wait screen stays up.

## 0.2.7

- App log is quieter and every Pompey line has a time.

## 0.2.5

- Pasting a Proton WireGuard file works on Home Assistant OS.
- Engines no longer retry the VPN before a Proton file exists.

## 0.2.4

- Proton paste box no longer flickers during startup.

## 0.2.3

- Home Assistant options are Plex address, Plex token, source URL, and source key.
- Paste the Proton WireGuard file on the wait screen. Missing Proton no longer stops the app.

## 0.2.2

- Pompey appears under **Install app** (start timeout was too high and hid it).
- Start wait is 300 seconds. Engines still download after the app is up.

## 0.2.1

- First Home Assistant OS build: wait screen, Proton, kill switch, starts on reboot.
- Proton server hostnames resolve so the tunnel can connect.
- Use a numeric IP for Plex. LAN names will not resolve through Proton.
- Titles route to kid or general folders from the rating (unknown goes to general).
- Store icon and logo.

## 0.2.0

- After Proton is up, downloads search and the hidden engines and wires them.
- Wait screen, then search.
- Kid vs general library folders.
- NAT-PMP for downloads.

## 0.1.1

- Named **Pompey**.
- Supervisor builds the app on the machine. No Docker image to pull.

## 0.1.0

- Starting point: Proton WireGuard and kill switch. Search is not connected yet.
