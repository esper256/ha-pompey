# Changelog

## 0.4.2

- Discover private directly connected container networks automatically so same-host Plex and other local services work without internal HAOS addresses in the network-access setting. Keep application DNS restricted to the VPN.
- Stop reporting Plex setup as incomplete when Seerr is already initialized, and log successful VPN connection explicitly.
- Keep diagnostics within the Ingress dashboard column and make expanded content scroll without shifting other panels.
- Preserve the original return route for published Seerr, Prowlarr and Ingress TCP interfaces while the VPN is active, including when Local network access is empty. Additional LAN access remains explicitly configured and the fail-closed firewall remains required.

## 0.4.1

- Revert UID/GID 1000 application execution for compatibility with Home Assistant SMB media mounts. Run the stack as container root without changing existing directory ownership; retain VPN-only application DNS with a separate endpoint resolver identity.
- Report storage operation failures with numeric ownership, parent permissions and safe mount details. Forward qBittorrent file logs to Home Assistant Logs.

- Standardize product, Home Assistant configuration, diagnostics, documentation and CI terminology. Use neutral integration and age-rating terminology while preserving existing configuration keys and storage defaults.

## 0.4.0

- Reject TV releases that lack the original language while allowing dual audio that includes it, prefer season packs, and explicitly set Sonarr’s minimum custom-format score to 0.

- Target fresh amd64 installations; migration from earlier layouts is not supported.
- Bundle pinned current stable applications and quality resources during the Supervisor build. Remove runtime application downloads and replacement/rollback machinery.
- Run applications and maintenance jobs as one unprivileged user; keep VPN setup privileged and engine DNS blocked outside the tunnel. Local applications start without a fresh VPN handshake.
- Use Home Assistant cold backups for recovery, and pause request closeout when search history appears reset or bulk-deleted.
- Add diagnostics with explicit release searches and real Arr rejection reasons.
- Verify real movie and TV imports, selected seasons, language/anime preferences, quality profiles, unwanted-file cleanup, request deletion, same-container restart and configured-clone preservation. Keep automatic dependency promotion disabled pending broader and forward-upgrade coverage.
- Known issue: an interrupted download can remain stalled after VPN connectivity recovers. Local applications remain available and the kill switch stays active. The strict recovery scenario runs as advisory CI for this release; other acceptance and packet checks remain required.

## 0.3.7

- Adjust Recyclarr defaults while preserving quality tiers and low-quality release filtering.
- Accept x265 and common audio formats, prefer original-language releases, and prevent Max upgrades based only on release scores.
- Add Recyclarr-managed Anime profiles with Seerr Sonarr routing and dual-audio preference.
- Remove stale managed custom-format state so previous strict Pompey policies do not continue rejecting releases.
- Allow 720p WEB TV fallbacks and keep broad TRaSH unwanted-format heuristics out of relaxed profiles.

## 0.3.6

- Accept smaller 1080p movie releases while preferring approximately 3.5 GB for a two-hour movie.

## 0.3.5

- Wait for Arr wiring before running maintenance jobs during startup.
- Keep manual imports moving when an earlier import loses its status.
- Track library moves and report blocked imports or delayed maintenance.
- Preserve other requested and manually monitored seasons when cancelling TV requests.
- Leave libraries unchanged when service responses are incomplete.
- Reject malformed Arr identities, library paths and snapshots before cleanup or routing mutations.
- Serialize mutating maintenance jobs so concurrent retries do not interfere with one another.

## 0.3.4

- Apply qBittorrent 5.2 share limits so Stop sharing pauses a finished torrent. Arr still removes it after import.
- Stage By Rating under `downloads` so movie and TV directories contain only final libraries. Empty leftover staging folders are removed after their titles move.

## 0.3.3

- Unregister unused historical Arr library folders from older installs. Occupied or extra folders stay registered and are reported.

## 0.3.2

- Prefer complete TV season packs and allow smaller x265 HD releases.
- Avoid replacing completed seasons just to improve release scores.

- Fix request cleanup errors for completed and failed requests.
- Preserve monitoring while request metadata is incomplete.
- Suppress webpage dumps in the app log.

## 0.3.1

- Fix VPN startup when restoring DNS settings on Home Assistant OS.
- Remove duplicate MIME warnings from troubleshooting pages.
- Shorten release notes and sidebar instructions.

## 0.3.0

- Support compatible WireGuard VPN providers and optional port forwarding.
- Improve VPN reconnection and block internet traffic when the tunnel fails.
- Fix download imports, upgrades, and sharing limits; preserve files that need review.
- Keep automatic rating sorting while respecting chosen library folders.
- Preserve files when requests are removed; leave declined requests declined.
- Restore search connections when settings change.
- Deliver tested app versions with Pompey updates and improve recovery from failed updates.
- Keep existing apps available when update downloads fail; block known version downgrades.
- Show live service health in the sidebar.

## 0.2.55

- Fix new requests being incorrectly declined and their downloads cancelled.
- Fix quality presets failing to apply on Home Assistant OS.

## 0.2.54

- Import manually selected downloads for requested titles without downloading duplicate copies.
- Stop further searches and queued downloads when a request is removed.

## 0.2.53

- Stop mistaking dual subtitles for dual audio when choosing releases.

## 0.2.52

- Avoid downloading titles again after files move between library folders.

## 0.2.51

- Search for full anime seasons instead of only individual episodes.

## 0.2.50

- Enable direct downloads from Open sources.
- Enable manual release selection in the troubleshooting consoles.

## 0.2.49

- Organize downloaded extras and specials for Plex; remove sample videos.

## 0.2.48

- Fix troubleshooting pages failing to load.
- Fix access to advanced request options.
- Add By Rating sorting and respect explicit rating-specific or general library choices.
- Remove obsolete library choices from requests.

## 0.2.47

- Fix blank Radarr and Sonarr troubleshooting pages.

## 0.2.46

- Add a simultaneous download limit, defaulting to eight.
- Keep stalled downloads from blocking the queue.

## 0.2.45

- Add optional troubleshooting consoles in the Home Assistant sidebar.

## 0.2.44

- Fix rating-based routing failures for titles with lower age ratings.
- Reduce log noise and avoid replaying old download logs.
- Fix quality presets failing to apply and remove obsolete quality choices.

## 0.2.43

- Search for higher-quality releases of Default and Max titles when sources are added.
- Stop future searches when a request is removed, keeping library files.

## 0.2.42

- Remove obsolete quality choices from requests.

## 0.2.41

- Prefer dual-audio releases at the same quality for Default and Max.

## 0.2.40

- Keep the sidebar on the ready screen after setup finishes.
- Hide completed setup steps and progress.

## 0.2.39

- Automatically check for app updates at startup and daily.
- Keep existing apps running when update checks fail.

## 0.2.38

- Improve Default and Max quality presets using TRaSH Guides.
- Prefer original-language audio; remove language and subtitle settings.

## 0.2.37

- Protect library files from duplicate imports and download cleanup.
- Keep replaced files in a recovery folder.

## 0.2.36

- Fix stalled season searches.
- Clean up leftover downloads without deleting episodes missing from the library.

## 0.2.35

- Show VPN transfer totals and a recent speed graph.
- Make stalled downloads easier to diagnose; reduce log noise and hide credentials.

## 0.2.34

- Prevent duplicate imports from deleting library files and subtitles.
- Import subtitles and clean up leftover download folders.
- Keep incomplete series marked as waiting.

## 0.2.33

- Import subtitles alongside videos.
- Avoid false warnings about leftover non-video files.
- Refresh request availability after imports.

## 0.2.32

- Fix completed downloads remaining outside their selected library folders.

## 0.2.31

- Fix setup and quality selection failing on network shares.

## 0.2.30

- Fix Max, Default, and Anything quality choices on existing installations.

## 0.2.29

- Clear finished downloads without deleting files.
- Retry importing completed downloads into the library.

## 0.2.28

- Add Max, Default, and Anything quality choices when requesting titles.
- Add language, anime audio, and subtitle preferences.

## 0.2.27

- Fix completed downloads failing to import on network shares.
- Report downloads waiting to enter the library.

## 0.2.26

- Prefer smaller 1080p releases and reject low-quality recordings.
- Add sharing choices: stop, share to a 1:1 ratio, or share for one day.

## 0.2.25

- Improve title searches on sources with limited search support.
- Fix missing movie and TV sources.
- Reduce repeated login errors in the log.

## 0.2.24

- Enable search on configured sources and retry unfilled requests.

## 0.2.23

- Combine app and download logs with clear service labels.

## 0.2.22

- Fix setup failures after changing the media folder.

## 0.2.21

- Default storage to /media/dlna with separate rating-specific and general movie and TV libraries.

## 0.2.20

- Add configurable media and library folders, including network shares.

## 0.2.19

- Move Plex setup to the search wizard and source setup to Open sources.

## 0.2.18

- Add Open sources for managing download sources.
- Preserve sources across updates.

## 0.2.17

- Remove an incorrect warning about search data not being saved.

## 0.2.16

- Add Open search in the sidebar, opening search in a separate tab.

## 0.2.15

- Fix unresponsive Plex setup.

## 0.2.14

- Fix setup failures before Plex connection.

## 0.2.13

- Fix the first-run Plex button.
- Allow Plex setup through the search wizard.

## 0.2.12

- Show setup errors instead of opening an empty search page.

## 0.2.11

- Fix first-time app installation on Home Assistant OS.

## 0.2.10

- Fix VPN startup failures.

## 0.2.9

- Fix VPN startup on Home Assistant OS.
- Wait for a VPN connection before downloading apps.
- Add timestamps to VPN logs.

## 0.2.8

- Keep the sidebar available when the VPN fails.

## 0.2.7

- Reduce log noise and add timestamps.

## 0.2.5

- Fix saving VPN configurations on Home Assistant OS.
- Wait for VPN setup before retrying the connection.

## 0.2.4

- Fix the VPN setup box flickering during startup.

## 0.2.3

- Add Plex and source settings.
- Accept VPN configuration in the sidebar without stopping the app.

## 0.2.2

- Fix Pompey missing from Home Assistant's app store.

## 0.2.1

- Add Home Assistant OS support and automatic startup.
- Fix connections to VPN servers specified by hostname.
- Sort titles into rating-specific or general libraries by rating.
- Add the app icon and logo.

## 0.2.0

- Add search and automatic setup after VPN connection.
- Add separate rating-specific and general libraries.
- Add VPN port forwarding.

## 0.1.1

- Introduce the Pompey name and local installation through Home Assistant.

## 0.1.0

- Initial WireGuard VPN support with internet blocking when disconnected.
