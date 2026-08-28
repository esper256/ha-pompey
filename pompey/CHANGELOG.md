# Changelog

## 0.2.11

- Engine fetch no longer dies on `tar: Prowlarr/*.dll: Cannot change mode ... Operation not permitted`. That was GNU tar restoring archive modes under `/tmp`, which HAOS AppArmor allows only `rwk` (no chmod). Linux Servarr builds are .NET — lots of `.dll` files plus an **ELF** launcher — not a Windows zip. Unpack under `/data/engines` with `--no-same-owner --no-same-permissions`, refuse zip/Windows/PE launchers, and log the artifact name (`linux-musl-core-x64`). Same path for Sonarr, Radarr, and Seerr. Rebuild so the banner says **0.2.11**.
- `tests/run.sh` now unpacks a Prowlarr-shaped fixture **and** the real linux-musl Prowlarr `.tar.gz` (cached). Range-GET alone could not catch this.
- Dev `tests/integration.sh` wires a fake Torznab source into Prowlarr, waits until Radarr/Sonarr have the synced indexer, searches **The Wild Robot**, and stops when the fake qBittorrent WebUI records the magnet add. The fake source answers TV-category probes with a dummy show so Sonarr does not reject the indexer. Prowlarr's sync command is `ApplicationIndexerSync` (the plural name 500s). Still no torrent client and no wait on peers.

## 0.2.10

- `Table = off` belongs in `[Interface]`. 0.2.9 appended it after `[Peer]`, so `wg addconf` said `Line unrecognized: Table=off` and deleted `wg0`. Rebuild so the banner says **0.2.10**.

## 0.2.9

- `wg-quick` no longer dies on `sysctl: error setting key 'net.ipv4.conf.all.src_valid_mark': Read-only file system`. Kernel WireGuard still created `wg0`; Proton `AllowedIPs = 0.0.0.0/0` makes `wg-quick` set that sysctl, HAOS `/proc/sys` is read-only, and the rollback deleted `wg0` (then the same thing with wireguard-go). Runtime conf uses `Table = off` (we add the default route ourselves) and a PATH helper ignores that one sysctl key.
- Every `wg-quick` `[#]` line is stamped `[HH:MM:SS] INFO:` so the app log is readable. Engines and NAT-PMP wait until the handshake exists (`vpn-up`), not merely until the Proton file is on disk — that was why they logged `WireGuard wg0 has no fresh handshake` and `engine fetch service exited` while the tunnel was still retrying.
- Writing `status.json` no longer crashes with `FileNotFoundError` if `/tmp/pompey` is missing mid-write.

## 0.2.8

- `wg-quick` no longer dies on `resolvconf: signature mismatch` for `/etc/resolv.conf` (Proton `DNS=` plus our rewrite of that file). Kernel WireGuard was actually working; resolvconf rolled `wg0` back and the WireGuard service **halted the whole container**, which took Ingress nginx with it (Home Assistant then showed an nginx error). Runtime conf strips `DNS=` (we still set Proton `10.2.0.1` ourselves); `wg-quick` gets a no-op resolvconf; a failed tunnel **leaves the wait screen up**. Kill switch is OUTPUT only — Supervisor Ingress to 8099 is INPUT and was never blocked.

## 0.2.7

- App log is quieter and every Pompey line has a time. The wait screen polled `/status.json` once a second and nginx wrote the full User-Agent on each hit; those polls (and logo/static assets) are no longer logged. Python services (`pompey-setup`, ingress rewriter, wire-stack) use the same `[HH:MM:SS] LEVEL:` prefix as bashio. NAT-PMP and wiring retries no longer repeat the same warning on every loop.

## 0.2.5

- Pasting a valid Proton `.conf` no longer fails with “Saved the file, but could not apply it” on Home Assistant OS. The wait screen was treating a kill-switch miss as a bad paste. HAOS has no `/lib/modules` and no legacy `iptables` filter table (`Table does not exist` / `modprobe: can't change directory to '/lib/modules'`). Pompey now probes `iptables-nft`, then `iptables`, then `iptables-legacy`, then `nft`. If none can attach, the Proton file stays saved and the tunnel still starts; the log says the kill switch could not attach.
- Engines and NAT-PMP no longer countdown a WireGuard handshake (and restart every ~90s) before a Proton file exists.

## 0.2.4

- Wait screen no longer flickers the Proton paste box. Other boot services were overwriting status every couple of seconds (marking later steps done, then resetting to only the tunnel), which hid the textarea and jumped the progress list. Proton paste stays until the `.conf` is applied.

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
