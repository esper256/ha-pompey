# What breaks if the Arr stack starts auto-updating

Pompey freezes engines on first fetch (`fetch-engines` skip-if-present) and writes Arr `UpdateAutomatically=False` / `UpdateMechanism=Docker` on first config. Recyclarr’s binary is frozen the same way; TRaSH JSON is not — `recyclarr sync` re-clones guide data on a timer. Seerr’s image is also skip-if-present.

That freeze is why household wiring survived months of uptime. Turning auto-update on (Arr’s own updater, a newer tarball on restart, Recyclarr `latest`, Seerr `latest`) does not require rewriting the product. It does invalidate a list of **shape assumptions** we encoded as string literals. This note is the map for that cleanup — not a promise to enable updates in this change.

The import path (`complete/` → Plex library folder) is the other brittle piece. Unit tests mock Arr’s JSON. `tests/integration.sh` now runs **real** Radarr against a fake qBittorrent that materializes a file. Keep that test when the stack starts moving.

## Why not a second BitTorrent node in tests

A magnet that makes real qBittorrent talk to a test-only seeder would exercise libtorrent, DHT bootstrap, and NAT-PMP — not ManualImport, `hasFile`, leftover `complete/` extras, or `deleteFiles=false`. It is slower, needs `/dev/net/tun` plus a private swarm, and is easy to leak (DHT, PEX, LSD, public trackers). The bugs we actually fought were **after** the file hit `complete/`.

The fake WebUI in `tests/lib/fake_source.py` is the realistic seam: receive the magnet string, write `incomplete/`, move to `complete/`, report `progress=1` + `content_path`, honor `stop`/`pause`/`delete`. Tracker on the fixture magnet is `udp://127.0.0.1:9`. Preferences force `dht`/`pex`/`lsd` off. CI (`tests/run.sh`) still must not start qBittorrent-nox.

## Frozen-binary assumptions (break when the binary changes)

### qBittorrent WebAPI

| Assumption | Where | If it moves |
| --- | --- | --- |
| v4 `pause` vs v5 `stop` | `qbit_stop_url` / `qbit_pause_url` (string split so tests can grep). Housekeep tries stop, falls back to pause. | A v6 rename, or stop that 404s as HTML, leaves torrents `uploading` and Arr will not rename off CIFS. |
| `GET /api/v2/torrents/info` fields `content_path`, `save_path`, `progress`, `amount_left`, `state` | `qbit_should_unlock`, `qbit_payload_gone` | Missing `content_path` makes payload-gone look like “still downloading”. Unlock never fires. |
| State names `pausedUP` / `stoppedUP` / `missingFiles` / `uploading` | `QBIT_STOPPED`, `QBIT_TRANSIENT` | New `stoppedUP` was the v4→v5 break we already patched. The next rename will skip unlock or forget. |
| `deleteFiles=false` on `POST /torrents/delete` | `qbit_forget_torrents` | A default-true or a new field that deletes on omit would wipe the library if it is a hardlink. |
| Category `savePath` = `downloads/complete` | `qbit_category` | AutoTMM or a new default save path drops files into `incomplete/` forever, or into a library folder. |
| Bind `Session\Interface=wg0` | `write-engine-configs` | A settings-key rename leaks off the tunnel. |
| WebAPI version string we do not pin | fake + real client | Radarr’s qbit proxy gates features on version. A jump can stop completed-download handling. |

### Radarr / Sonarr HTTP API

| Assumption | Where | If it moves |
| --- | --- | --- |
| `/api/v3` | every Arr URL in `wire-stack` | v4 would 404 the whole wire. |
| Command names `ManualImport`, `DownloadedMoviesScan`, `DownloadedEpisodesScan`, `RefreshMonitoredDownloads`, `MoviesSearch` / `EpisodeSearch` | housekeep | Silent no-op: files sit in `complete/`. This is the path that already took the most household back-and-forth. |
| ManualImport body: `movieId` vs `seriesId`+`episodeIds`, `importMode: Move`, `quality` + `languages` objects | `manual_import_file` | 400s. We log and the video stays in `complete/`. |
| `GET /manualimport?folder=&filterExistingFiles=` | `import_matched_drop` | List shape change (no `movie` nested object, no `rejections`, no `hasFile`) either re-imports (deletes the library copy) or never matches. |
| `enableCompletedDownloadHandling` and `skipFreeSpaceCheckWhenImporting` on media management | `ensure_media_management` | NAS that reports 0 bytes free skips import again. |
| `removeCompletedDownloads` tied to `after_download` | download client PUT | `true` plus a forget race deletes `complete/` before Arr copies. |
| Quality **names** `WEBDL-1080p`, `Bluray-2160p`, `Remux-2160p` (not `WEB-DL` / `Remux`) | `DEFAULT_GROUPS` / `MAX_GROUPS` | Profiles silently omit the intended qualities. Sonarr vs Radarr already disagree on Remux vs Remux-2160p. |
| Language CF: `LanguageSpecification` with value `-2` (Original), negate | `not_original_language_format` | Original-audio scoring stops; dubs win on Default/Max. Recyclarr’s TRaSH Original is the preferred owner — this CF is the fallback when Recyclarr is missing. |
| Custom format `fields: [{name, value}]` | `ensure_custom_formats` | Arr has flipped between `{name,value}` and a dict more than once. |
| Root-folder POST `{path}` | `ensure_root_folder` | Kid vs Not Kid folders fail to register; Seerr routes into the wrong library. |
| `UpdateAutomatically=False` only on **first** XML write | `write-engine-configs` | An Arr self-update can rewrite config and turn its updater back on. Re-apply on every start if we ever allow updates. |

### Prowlarr

| Assumption | Where | If it moves |
| --- | --- | --- |
| ApplicationIndexerSync into Radarr/Sonarr | `wire-stack` apps | Caps or category ids (`2000`/`5000`) drift; Sonarr rejects the source (“no results in configured categories”). Fake TV item exists only so that test passes. |
| Download-client schema field names `host` / `userName` / `movieCategory` | `qbit_client_values` | Client never points at localhost qbit. |
| Caps proxy on `:9698` | `prowlarr-arr-proxy` | Title-search fields Arr expects disappear; household “search finds nothing”. |

### Seerr

| Assumption | Where | If it moves |
| --- | --- | --- |
| `/api/v1/settings/public` means the process is up | wire required check | Next.js route rename keeps the wait screen up forever. |
| `/settings/radarr` POST/PUT body (server ids, root folders, quality profile **names** Default/Max/Anything) | Seerr→Arr | Profile rename or PUT-vs-POST (already burned once) disconnects requests. |
| Jobs `plex-recently-added-scan`, `radarr-scan`, `sonarr-scan` | `tickle_seerr_availability` | Requests stay “requested” after the file is in Plex. |
| API key impersonates user id 1; 403 until the Plex wizard | wire retry | A Seerr auth change marks search ready with a hollow UI, or never marks ready. |
| Image layout `/app/dist/index.js`, no `DOCKER` sentinel | `fetch-engines` | Unpack “succeeds” and the UI is empty. |

### Recyclarr / TRaSH

| Assumption | Where | If it moves |
| --- | --- | --- |
| Binary CLI: `RECYCLARR_CONFIG_DIR` + `RECYCLARR_DATA_DIR` + `recyclarr sync -c` (v8 dropped `--app-data`) | `recyclarr-sync` | Already broke once. Next CLI rename leaves Default/Max on the Pompey fallback. |
| Quality-profile **trash_ids** (`d1d67249…` HD, `64fb5f98…` UHD, Sonarr WEB-1080p / WEB-2160p) | `recyclarr-sync` | Guide rename/split: Recyclarr exits non-zero; we log and keep stale profiles. |
| Recyclarr YAML `name: Default` / `Max` with `reset_unmatched_scores` | generated `recyclarr.yml` | Reset wipes Pompey CFs (Not Original, reject CAM) if we ever let Recyclarr own those names without re-applying scores. |
| `delete_old_custom_formats: false` | YAML | `true` would delete Anything’s formats or leftover household CFs. |
| Anything is **not** Recyclarr-managed | `apply_household_quality_profiles` | A third TRaSH profile named similarly would fight CAM-allowed. |
| Daily sync of TRaSH JSON while the Recyclarr **binary** stays frozen | housekeep `run_recyclarr` | Guide-side quality sizes / Original language scores can still move under a frozen binary. That is already “auto-update” for scoring, just not for the Arr apps. |

### Fetch / unpack

| Assumption | Where | If it moves |
| --- | --- | --- |
| Skip if `Radarr/Radarr` (etc.) is already executable | `fetch-engines` | Auto-update on restart needs a version compare, not presence. Today, presence **is** the freeze. |
| Servarr linux-musl tarball layout `Name/Name` ELF | unpack | A distro change (single binary, different folder) fails `assert_elf_launcher`. |
| qBittorrent from `userdocs/qbittorrent-nox-static` `latest` | URL | `latest` already floats when the file is **missing**. Once present, it never refreshes — first-boot vs later-boot divergence. |
| Seerr `ghcr.io/seerr-team/seerr:latest` via crane | unpack | Same first-fetch pin. Tag `latest` is not a version. |

## Product assumptions that are not API strings

- **One container, localhost engines.** Arr “Docker” update mechanism is a lie we tell so they do not self-replace. A real Docker updater inside HAOS would fight Supervisor.
- **Same-share rename.** `copyUsingHardlinks=false` because CIFS. A future Arr that forces copy-on-import doubles disk and time; one that assumes hardlinks will “import” and leave `complete/` as the only copy.
- **Kid vs Not Kid lives on the Arr title path**, not the filename. Housekeep must not guess. Parser changes that drop `movie` from manualimport rows will look like “unknown movie”.
- **Plex is outside Pompey.** Seerr’s library scan is the only “is it on Plex?” signal. Arr `hasFile` can be true while Plex has not scanned yet — we already tickle Seerr; job names are the fragile bit.
- **Quality names in Seerr** are Default / Max / Anything. Recyclarr overwrites Default/Max in place (same name). Renaming TRaSH profiles would desync the Seerr dropdown from Arr.

## What to do before enabling auto-update

1. Keep `tests/integration.sh` green against **whatever binary fetch-engines just pulled** (a nightly job, not only the cached tarball on the agent VM).
2. Re-apply `UpdateAutomatically=False` on every start until we intentionally flip it, or stop writing that flag and pin versions in fetch URLs instead of `latest`.
3. Treat Recyclarr trash_ids and qbit stop/pause as **contract tests** (HTTP fixture + one real-Arr import), not comments.
4. Do not let Recyclarr `reset_unmatched_scores` run without re-asserting Pompey CFs on Anything and the Not Original fallback.
5. When Arr ships `/api/v4`, gate with a probed capability list rather than a big-bang string replace.

Until then, freeze-on-first-fetch is the feature. Auto-update is a contract change, not a toggle.
