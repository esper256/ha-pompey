# Pompey product direction

The household should request a movie or show in Seerr and watch it through a separate Plex installation. Pompey manages the VPN and localhost services around that journey, inside one Home Assistant OS add-on.

Keep the integration smaller than the programs it coordinates. Arr owns release matching, upgrades, imports and library moves. Prowlarr owns source synchronization. Recyclarr owns quality profiles. Pompey adds household folder/sharing policy, conservative request closure, live status and recoverable releases.

The default is automatic routing by rating, with unknown ratings going to general libraries. A household member can choose a specific library or quality profile. Ports 5055 and 9696 serve search and sources on the LAN; hidden consoles are available only through Debug in authenticated Ingress.

Reliability means preserving media, honoring sharing choices, blocking internet when the tunnel is unavailable, continuing retries after upstream outages, and restoring app databases together with binaries after a bad update. Unknown files or ambiguous imports should remain reviewable rather than trigger speculative cleanup.

Release confidence comes from fast behavioral/failure tests, a smaller pinned real-engine contract suite, production-layout smoke tests, and final manual HAOS acceptance. Upstream candidate jobs should reveal API drift before it becomes a household update. The dedicated real-downloader test uses a verified loopback-only network namespace, synthetic local payloads and no peers. Other tests use HTTP downloader fakes.
