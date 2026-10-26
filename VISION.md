# Pompey product direction

The household should request a movie or show in Seerr and watch it through a separate Plex installation. Pompey manages the VPN and localhost services around that journey, inside one Home Assistant OS add-on.

Keep the integration smaller than the programs it coordinates. Arr owns release matching, upgrades, imports and library moves. Prowlarr owns source synchronization. Recyclarr owns quality profiles. Pompey adds household folder/sharing policy, conservative request closure, live status and tested pinned releases.

The default is automatic routing by rating, with unknown ratings going to general libraries. A household member can choose a specific library or quality profile. Ports 5055 and 9696 serve search and sources on the LAN; hidden consoles are available only through Debug in authenticated Ingress.

Reliability means preserving media, honoring sharing choices, blocking internet when the tunnel is unavailable, continuing retries after upstream outages, and making recovery through Home Assistant cold backups straightforward. Unknown files or ambiguous imports should remain reviewable rather than trigger speculative cleanup.

Release confidence comes from focused tests of difficult safety logic, isolated packet tests and household outcomes through the actual shipped applications. External metadata, one source, one tracker and a synthetic peer provide a deterministic world. Short synthetic media obeys the production quality profiles. The automated suite is the release authority; it does not reproduce Supervisor, real NAS semantics or Plex playback. Dependency promotion and automatic release are enabled only after the foundational journey is expanded to cover the household promises.
