# Changelog

## 0.2.3

- Home Assistant options are only questions a household can answer: **Plex address**, **Plex token**, **source URL**, **source key**. WireGuard filename, private key, address, peer key, endpoint, DNS, LAN list, media folder, port-forward toggle, and log level are gone.
- Missing Proton no longer kills the container. Start Pompey, open it in the sidebar, **paste the Proton WireGuard .conf** on the wait screen. That is the file Proton gives you when you create a WireGuard certificate.

## 0.2.2

- Supervisor `config.yaml` validator in `tests/test_ha_config.py` (same schema that skips a bad app so it never lists). `timeout` must be 10–300 seconds; **1800 was rejected**, which is why Pompey did not appear under Install app after the repo cloned.
- `timeout` is now 300 (the maximum). That is Docker start/stop, not the engine download. Engines still fetch after the container is up.

## 0.2.1

- Ready for a first Home Assistant OS try: Ingress rewriter so Seerr assets load under `/api/hassio_ingress/…`, Proton `PersistentKeepalive`, iptables-legacy kill switch, longer first-boot timeout, auto start on HA reboot.
- WireGuard Endpoint hostnames are resolved to IPv4 before Proton DNS and the kill switch (so `wg-quick` can still handshake). AppArmor allows `/sbin` and `/usr/sbin` (iptables-legacy, `ip`).
- Plex in another Docker: warn if the address is a hostname (Proton DNS will not resolve LAN names). Use a numeric IP.
- Wire Prowlarr indexers to the TV/movie engines after adding the source. Kid routing also reads nested certification fields.
- First-boot wiring no longer crashes when Seerr settings endpoints return objects instead of arrays (common before initialize / without Plex). A local Seerr account from persisted secrets lets Radarr/Sonarr be wired even if Plex is filled in later.
- Ingress `Set-Cookie` `Path=/` is rewritten to the Ingress prefix so the household stay signed in under the sidebar URL. Seerr binds localhost.
- Store and wait-screen branding: square `icon.png`, rectangular `logo.png` (also the loading screen).
- README says what 0.2.0/0.2.1 actually does vs what is still unproven. After adding the GitHub URL as an Apps repository, Pompey is under **Install app** (often at the bottom). If the repo was added while private, remove it and add it again.

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
