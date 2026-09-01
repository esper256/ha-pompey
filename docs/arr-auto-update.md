# Engine updates

Pompey ships other teams’ programs (Seerr, Prowlarr, Radarr, Sonarr, qBittorrent-nox, Recyclarr) inside one Home Assistant add-on. Those programs move. Indexers and trackers move faster. If the hidden engines freeze at first boot, search eventually finds nothing. If every engine release also requires a Pompey store bump, the household is stuck in a rebuild treadmill that has nothing to do with Proton or the wait screen.

This note is the update **plan** (why, what we are doing, what we refused) plus the **brittleness map** for that plan. It is not the feature itself. Today the add-on still freezes binaries on first fetch. README roadmap item 2 is the work that implements the decision here.

## Why this is a product problem

Two clocks must not be the same:

| Clock | What it is for | Who should bump it |
| --- | --- | --- |
| **Pompey add-on version** (Supervisor rebuild) | Our glue: Proton, kill switch, wait screen, wiring, housekeep | Us, when *Pompey* changes |
| **Engine binaries** on `/data/engines` | Other teams’ indexer parsers, grab logic, Seerr UI | Those teams, on their cadence |

The Arr stack (Prowlarr / Radarr / Sonarr) is in an adversarial race with indexers. A Prowlarr that is six months old is not “stable”; it is a source UI that returns no results. qBittorrent and Seerr break less often that way, but they still ship API renames (qbit v4 `pause` → v5 `stop` already burned us).

We will not version-bump Pompey every time Radarr tags a release. That is the grind this plan exists to avoid.

## How these apps update when Pompey is not in the way

Most self-hosters run this stack as **several Docker containers**. A smaller set run **native Windows/Linux** installs and click Update inside the app. Those are the two trod paths. Pompey is neither: one HAOS container, engines fetched onto disk, UIs hidden except Seerr and Prowlarr.

Servarr’s own wiki is blunt: on Docker, do **not** use the in-app installer. Pull a new image and recreate the container. The in-app button is for native installs.

| App | Native / Windows (click path) | Docker compose (trod self-host path) | In-app “update available”? | Can it replace its own files? |
| --- | --- | --- | --- | --- |
| **Prowlarr** | System → Updates → Install. Same BuiltIn updater as the other *arr apps. Optional `UpdateAutomatically`. | `docker compose pull && up -d` on the linuxserver/hotio image. Config is a volume. Wiki: do not BuiltIn-update inside the container. | Yes. This is the banner people remember. | Yes on native (writes `/opt/Prowlarr` or equivalent, restarts itself). **Must not** on Docker. |
| **Radarr** | Same BuiltIn updater; `radarr.servarr.com/v1/update/…`. Branch `master` / `develop` / `nightly`. | Same as Prowlarr. Image tag is the branch. | Yes, but the household never opens this UI in Pompey (localhost). | Same split: native yes, Docker no. |
| **Sonarr** | Same. Download URL is `services.sonarr.tv` rather than `*.servarr.com`. | Same. | Yes, also hidden here. | Same. |
| **qBittorrent** | GUI Windows build: Help → Check for updates. Linux is distro packages or replace the binary. **qBittorrent-nox has no in-app installer.** | Pull `lscr.io/linuxserver/qbittorrent` (or the official `qbittorrent-nox` image) and recreate. LinuxServer: do not update the app inside the container. | No useful click-to-install on nox. | No. Someone outside the process replaces the binary or the image. |
| **Seerr** | Not a first-class path. They ship Docker ([docs](https://docs.seerr.dev/getting-started/docker/)): `ghcr.io/seerr-team/seerr:latest`. | Pull the image, recreate. Docs also mention Watchtower. Config is `/app/config` on a volume. | A version banner (Overseerr-era) can say a release exists. It does **not** install. | No. Recreate the container. |
| **Recyclarr** | Download a new GitHub release tarball and overwrite the binary ([manual install](https://recyclarr.dev/guide/installation/manual-install/)). | Pull `ghcr.io/recyclarr/recyclarr:<major>` (they **stopped publishing `latest`**; major tag e.g. `8` is the floating line). | No. | No. Replace the binary or the image. **Separate clock:** `recyclarr sync` clones TRaSH Guides JSON every run even if the binary is unchanged. |
| **Pompey** | n/a | n/a | Supervisor “check for updates” / rebuild the add-on when `config.yaml` version changes. | Supervisor rebuilds **our** image (WireGuard, nginx, scripts). Engines are not in that image. |
| **Plex** | Its own updater / NAS app. | Out of Pompey. | Out of Pompey. | Out of Pompey. |

`UpdateMechanism` on the *arr apps is how they describe that split: `BuiltIn` (self-replace), `Docker` (someone else replaces the process), `External` (apt), `Script`. `UpdateAutomatically` only matters for `BuiltIn`.

## What Pompey is in that picture

We already lie `UpdateMechanism=Docker` to Radarr, Sonarr, and Prowlarr. That is the correct lie. On HAOS there is no second container to pull. Supervisor will not `docker pull radarr`. The files live on `/data/engines` because they must survive a Pompey image rebuild and because they are not ours to bake in.

So Pompey has to play the **Docker-orchestrator** role: the thing that fetches a new artifact, puts it on disk, restarts the process, and leaves config/data alone. That is closer to `docker compose pull` than to “stop pinning and let Prowlarr’s Update button run.”

We already fetch from the same official channels a first boot uses:

- Prowlarr / Radarr: `*.servarr.com/v1/update/master/updatefile`
- Sonarr: `services.sonarr.tv/v1/download/main/latest`
- qBittorrent-nox: `userdocs/qbittorrent-nox-static` GitHub `latest`
- Recyclarr: GitHub `latest` musl tarball (their Docker world floats a major tag; we are on the binary path)
- Seerr: `crane export ghcr.io/seerr-team/seerr:latest`

First boot already takes `latest`. The freeze is only **skip if the file exists**. Two households that installed a month apart already run different engines. Auto-update is making later boots as current as a new install, on a timer, without a Pompey version bump.

## Options we considered

### A. Stop pinning and let the *arr BuiltIn updater run

Flip `UpdateAutomatically=True`, `UpdateMechanism=BuiltIn`. Prowlarr’s banner becomes a real Install button. Radarr/Sonarr would only move if we also set auto, because those UIs are not in the sidebar.

**Rejected.** Servarr documents this as the thing you must not do in a container: the app replaces files while the orchestrator also might, databases migrate forward, a later “image” (for us, a Pompey fetch) can land *older* bits on a newer DB. Arr’s updater restarts itself and fights s6. It does nothing for Seerr, qBittorrent, or Recyclarr — the household would still rot on three of six programs. Two updaters on Prowlarr (BuiltIn plus our fetch) is worse than one.

### B. Version-bump Pompey for every engine release

Pin exact URLs or hashes in the add-on. Ship a new Pompey whenever Radarr moves.

**Rejected.** That is the grind. Pompey releases would track other teams’ calendars, not ours. The household would rebuild for indexer-parser fixes they never asked to think about. We would also fall behind whenever we did not ship.

### C. Re-run the first-setup fetch only when the add-on starts

Change skip-if-present to “always pull latest,” but only on Supervisor start.

**Rejected as the whole plan; kept as one trigger.** Households leave HAOS up for months. README already said a wrapper we do not touch for months still has to refresh Radarr. Start-only fetch helps the person who reboots, not the person who does not.

Re-running **wire** after a binary replace *is* part of the plan (idempotent first-setup path). It is not a substitute for replacing the binary.

### D. Pompey is the updater (chosen)

Keep `UpdateMechanism=Docker` / `UpdateAutomatically=False` so the apps never self-replace. Pompey fetches from the official channels above, **skips when the on-disk version is already current**, replaces atomically (we already stage under `.partial-*`), restarts that s6 service, then runs `wire-stack` again so download clients, quality names, and Seerr connections re-assert.

Check on add-on start **and** on a slow timer (days, through the tunnel, same as Recyclarr’s TRaSH sync). Failures keep the previous binary and log. Search stays up.

This matches the Docker trod path (orchestrator replaces the process; data volume stays). It covers apps with no in-app installer. It does not require the household to open Radarr. It does not require a Pompey store bump when Prowlarr tags a release.

### E. Split policy: float *arr, pin Seerr and qBittorrent

Prowlarr/Radarr/Sonarr need indexer defs; Seerr and qbit have been the glue-breakers.

**Not the first cut.** Two clocks and two code paths. First boot already takes `latest` for all six; the plan is to keep that one policy and let later boots catch up. If Seerr `:latest` is too spicy in practice, pinning Seerr to a major tag is a later tightening, not a different architecture.

### F. Watchtower / nested Docker / publish Arr images

**Not available.** No `docker.sock` in the add-on. Supervisor owns the one container. We do not publish GHCR images of Radarr.

## Decision (short)

**Pompey updates engines the way Docker compose updates a stack: we replace the artifact, the app does not. Pompey’s own version is not that clock.**

- Do not enable Arr BuiltIn / `UpdateAutomatically`.
- Do not require a Pompey add-on bump for an upstream engine release.
- Do not treat “file already exists” as “we are done forever.”
- Do re-apply `UpdateMechanism=Docker` on every start so a curious Prowlarr click cannot turn BuiltIn back on (today that XML is first-write-only).
- Do re-run wire after a replace (the first-setup path is the migrate path).
- Do keep TRaSH JSON on its existing timer; fold the Recyclarr *binary* into the same fetch policy as the others.

## What implementing this looks like (not done)

1. `fetch-engines` grows a version/ETag compare. Presence is not a skip. Staging and ELF checks stay.
2. A timer (housekeep or a sibling service), after the tunnel is up, calls that fetch. Not every five minutes.
3. If a binary actually changed: restart that engine, then `wire-stack` (not a distinct “update mode” — the existing idempotent wire).
4. `write-engine-configs` stops being first-write-only for the update flags. Re-stamp `UpdateAutomatically=False` / `UpdateMechanism=Docker` every boot.
5. Tests: `tests/integration.sh` against **whatever** fetch just pulled, on a schedule, not only the tarball cached on the agent VM.

Until that ships, skip-if-present remains the freeze. The tables below are what we encoded as string literals and will feel first when binaries start moving.

## Why not a second BitTorrent node in tests

A magnet that makes real qBittorrent talk to a test-only seeder would exercise libtorrent, DHT bootstrap, and NAT-PMP — not ManualImport, `hasFile`, leftover `complete/` extras, or `deleteFiles=false`. It is slower, needs `/dev/net/tun` plus a private swarm, and is easy to leak (DHT, PEX, LSD, public trackers). The bugs we actually fought were **after** the file hit `complete/`.

The fake WebUI in `tests/lib/fake_source.py` is the realistic seam: receive the magnet string, write `incomplete/`, move to `complete/`, report `progress=1` + `content_path`, honor `stop`/`pause`/`delete`. Tracker on the fixture magnet is `udp://127.0.0.1:9`. Preferences force `dht`/`pex`/`lsd` off. CI (`tests/run.sh`) still must not start qBittorrent-nox. Unit tests mock Arr’s JSON. `tests/integration.sh` runs **real** Radarr against that fake client and asserts three outcomes: incomplete files never reach the library, a finished file does, and `downloads/` does not keep leftover videos. It does not pin which process renamed the file. Keep that test when the stack starts moving.

## Risks in this plan

Unattended `latest` can break the house overnight. Mitigations: keep the previous binary on fetch failure; wait screen / app log when wire fails; integration against live latest. Remaining risks are shape assumptions we already encoded. They do not argue for pinning forever; they argue for contract tests before we flip skip-if-present.

**Operational (not API strings)**

- Replacing qBittorrent while a torrent is writing to `incomplete/` — stop or wait for a quiet window; do not `mv` the binary under a live libtorrent.
- Arr SQLite migrations do not roll back. A bad Radarr build that migrated the DB cannot be undone by restoring the old ELF. Keep config backups the apps already know how to write, or do not auto-jump `nightly`.
- Recyclarr major CLI (v8 already dropped `--app-data`). Their Docker world pins a major tag; our GitHub `latest` binary can take a breaking major without a Pompey bump. Watch the CLI, not only the tarball.
- Someone with **Open sources** can still change Prowlarr’s update settings in the UI. Re-stamp Docker/False or they get option A by accident.
- Fetch only after `vpn-up`. A failed check must not empty `/data/engines`.
- `.NET` trees are large; a failed extract already uses `.partial-*` so we do not leave a half Radarr.

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
| Quality **names** in Recyclarr YAML (`Bluray-2160p`, `WEB 2160p`, remux off) | `recyclarr-sync` `render_config` | Guide rename: Recyclarr exits non-zero or Max loses 1080p fallback. Pompey no longer encodes `WEBDL-*` names for Default/Max. |
| Language CF: `LanguageSpecification` with value `-2` (Original), negate | `not_original_language_format` | Original-audio scoring stops; dubs win on Default/Max. Recyclarr’s TRaSH Original is the preferred owner — this CF is the fallback when Recyclarr is missing. |
| Custom format `fields: [{name, value}]` | `ensure_custom_formats` | Arr has flipped between `{name,value}` and a dict more than once. |
| Root-folder POST `{path}` | `ensure_root_folder` | Kid vs Not Kid folders fail to register; Seerr routes into the wrong library. |
| `UpdateAutomatically=False` only on **first** XML write | `write-engine-configs` | A Prowlarr UI change or an Arr rewrite can turn BuiltIn back on. Re-stamp Docker/False every start as part of this plan. |

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
| Binary CLI: `RECYCLARR_CONFIG_DIR` + `RECYCLARR_DATA_DIR` + `recyclarr sync -c` (v8 dropped `--app-data`) | `recyclarr-sync` | Already broke once. Next CLI rename leaves Default/Max as name stubs (stock Arr clone) until Recyclarr can sync. |
| Quality-profile **trash_ids** (`d1d67249…` HD, `64fb5f98…` UHD, Sonarr WEB-1080p / WEB-2160p) | `recyclarr-sync` | Guide rename/split: Recyclarr exits non-zero; we log and keep stale profiles. |
| Recyclarr YAML `name: Default` / `Max` with `reset_unmatched_scores` | generated `recyclarr.yml` | Reset is correct for Default/Max (TRaSH owns scores). A YAML that named Anything would wipe CAM-allowed scores. |
| `delete_old_custom_formats: false` | YAML | `true` would delete Anything’s formats or leftover household CFs. |
| Anything is **not** Recyclarr-managed | `apply_household_quality_profiles` | A third TRaSH profile named similarly would fight CAM-allowed. |
| Daily sync of TRaSH JSON while the Recyclarr **binary** stays frozen | housekeep `run_recyclarr` | Guide-side quality sizes / Original language scores can still move under a frozen binary. That is already “auto-update” for scoring, just not for the Arr apps. |

### Fetch / unpack

| Assumption | Where | If it moves |
| --- | --- | --- |
| Skip if `Radarr/Radarr` (etc.) is already executable | `fetch-engines` | This skip **is** the freeze. The plan replaces it with a version compare. |
| Servarr linux-musl tarball layout `Name/Name` ELF | unpack | A distro change (single binary, different folder) fails `assert_elf_launcher`. |
| qBittorrent from `userdocs/qbittorrent-nox-static` `latest` | URL | `latest` already floats when the file is **missing**. Once present, it never refreshes — first-boot vs later-boot divergence. |
| Seerr `ghcr.io/seerr-team/seerr:latest` via crane | unpack | Same first-fetch pin. Tag `latest` is not a version. |

## Product assumptions that are not API strings

- **One container, localhost engines.** Arr “Docker” update mechanism is the orchestrator contract, not a nested Docker daemon. A real Docker updater inside HAOS would fight Supervisor.
- **Same-share rename.** `copyUsingHardlinks=false` because CIFS. A future Arr that forces copy-on-import doubles disk and time; one that assumes hardlinks will “import” and leave `complete/` as the only copy.
- **Kid vs Not Kid lives on the Arr title path**, not the filename. Housekeep must not guess. Parser changes that drop `movie` from manualimport rows will look like “unknown movie”.
- **Plex is outside Pompey.** Seerr’s library scan is the only “is it on Plex?” signal. Arr `hasFile` can be true while Plex has not scanned yet — we already tickle Seerr; job names are the fragile bit.
- **Quality names in Seerr** are Default / Max / Anything. Recyclarr overwrites Default/Max in place (same name). Renaming TRaSH profiles would desync the Seerr dropdown from Arr.

## What to do before flipping skip-if-present

1. Keep `tests/integration.sh` green against **whatever binary fetch-engines just pulled** (a nightly job, not only the cached tarball on the agent VM).
2. Re-stamp `UpdateAutomatically=False` / `UpdateMechanism=Docker` on every start so Prowlarr’s UI cannot enable BuiltIn.
3. Treat Recyclarr trash_ids and qbit stop/pause as **contract tests** (HTTP fixture + one real-Arr import), not comments.
4. Do not let Recyclarr YAML name Anything. Re-assert Pompey CFs on Anything after Recyclarr. Default/Max scores are Recyclarr's.
5. When Arr ships `/api/v4`, gate with a probed capability list rather than a big-bang string replace.

Until skip-if-present is replaced, freeze-on-first-fetch is still what ships. The decision is the contract; the feature is not a toggle on `UpdateAutomatically`.
