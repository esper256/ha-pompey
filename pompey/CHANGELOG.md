# Changelog

## 0.2.1

- Ready for a first Home Assistant OS try: Ingress rewriter so Seerr assets load under `/api/hassio_ingress/…`, Proton `PersistentKeepalive`, iptables-legacy kill switch, longer first-boot timeout, auto start on HA reboot.
- WireGuard Endpoint hostnames are resolved to IPv4 before Proton DNS and the kill switch (so `wg-quick` can still handshake). AppArmor allows `/sbin` and `/usr/sbin` (iptables-legacy, `ip`).
- Plex in another Docker: warn if the address is a hostname (Proton DNS will not resolve LAN names). Use a numeric IP.
- Wire Prowlarr indexers to the TV/movie engines after adding the source. Kid routing also reads nested certification fields.
- First-boot wiring no longer crashes when Seerr settings endpoints return objects instead of arrays (common before initialize / without Plex). A local Seerr account from persisted secrets lets Radarr/Sonarr be wired even if Plex is filled in later.
- Ingress `Set-Cookie` `Path=/` is rewritten to the Ingress prefix so the household stay signed in under the sidebar URL. Seerr binds localhost.
- Store and wait-screen branding: square `icon.png`, rectangular `logo.png` (also the loading screen).
- README says what 0.2.0/0.2.1 actually does vs what is still unproven. Home Assistant cannot clone this GitHub repo as an Apps repository while it is private (`could not read Username`); copy `pompey/` into `/addons` instead.

## 0.2.0

- First stab at the full box: after Proton is up, fetch Seerr, the TV/movie/indexer engines, and qBittorrent-nox, then wire them on localhost.
- Ingress starts as a wait screen, then proxies the household search UI.
- Operator options for Plex and one source (URL plus key). No source catalog is shipped.
- Kid vs general folders from title certification (unknown → general).
- NAT-PMP mapped port is applied to the download engine.
- Agent/dev: `POMPEY_FAKE_VPN=1` brings up a veth named `wg0` that NATs out the default adapter (no Proton). `tests/integration.sh` fetches glibc TV/movie engines and looks up The Wild Robot on TMDB (no torrent client, no grab).

## 0.1.1

- Named **Pompey** (the stack and the Home Assistant app).
- Seerr is the planned household UI; we do not reinvent it.
- No container image is published. Supervisor builds locally. Runtime downloads later; nothing extra is baked into the image.

## 0.1.0

- Clean start: search screen, Proton WireGuard, kill switch, NAT-PMP helper.
- Search is not connected yet.
