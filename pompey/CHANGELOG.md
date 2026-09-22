# Changelog

## 0.3.4

- Apply qBittorrent 5.2 share limits so Stop sharing pauses a finished torrent. Arr still removes it after import.
- Stage By Rating under `downloads` so the movie and TV folders only contain the libraries people browse. Empty leftover staging folders are removed after their titles move.
- Ignore executable, script, archive, and disc-image payloads, including `.zip`, `.rar`, `.7z`, `.iso`, and `.zipx`. A download with no playable video is removed and blocklisted. A video that arrives beside one of those files keeps the video.
- Reject obsolete video containers (RealMedia, Windows Media, Flash, Ogg, 3GP, DVD VOB, and `.qt`). AVI, MPEG program streams, and DivX stay, along with Matroska, MP4, QuickTime `.mov`, WebM, and MPEG transport streams.

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

- Import manually selected downloads for requested titles without grabbing duplicate copies.
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
- Add By Rating sorting and respect explicit kid or general folder choices.
- Remove obsolete library choices from requests.

## 0.2.47

- Fix blank Radarr and Sonarr troubleshooting pages.

## 0.2.46

- Add a simultaneous download limit, defaulting to eight.
- Keep stalled downloads from blocking the queue.

## 0.2.45

- Add optional troubleshooting consoles in the Home Assistant sidebar.

## 0.2.44

- Fix kid-rated titles failing to move to the kid library.
- Reduce log noise and avoid replaying old download logs.
- Fix quality presets failing to apply and remove obsolete quality choices.

## 0.2.43

- Look for better copies of Default and Max titles when sources are added.
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

- Fix setup getting stuck after changing the media folder.

## 0.2.21

- Default storage to /media/dlna with separate kid and general movie and TV folders.

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

- Fix setup getting stuck before Plex connection.

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
- Sort titles into kid or general libraries by rating.
- Add the app icon and logo.

## 0.2.0

- Add search and automatic setup after VPN connection.
- Add separate kid and general libraries.
- Add VPN port forwarding.

## 0.1.1

- Introduce the Pompey name and local installation through Home Assistant.

## 0.1.0

- Initial WireGuard VPN support with internet blocking when disconnected.
