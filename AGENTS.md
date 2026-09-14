# Agent development instructions

Read [README.md](README.md), [pompey/DOCS.md](pompey/DOCS.md) and [docs/testing.md](docs/testing.md). Product direction is in [VISION.md](VISION.md).

Supervisor builds `pompey/Dockerfile` locally and starts **one** HAOS add-on container. The household never starts Docker, and we never publish images to Docker Hub/GHCR. A local Docker build checks the Dockerfile; `tests/smoke_runtime.py` checks the built musl runtime. Neither substitutes for Supervisor or the manual HAOS release gate. Do not spend the session faking Supervisor.

Run `bash tests/run.sh` for routine work. Real pinned artifact, Arr import/Recyclarr, and Seerr API tests are described in the testing guide. Runtime Python sources use `.py` suffixes; only lint shell entrypoints with shellcheck. CI must require the isolated packet tests rather than silently skip them.

Never start a torrent client, speak BitTorrent, or wait for peers in tests. Fake Torznab and downloader HTTP APIs are allowed. Use synthetic media. Public metadata lookup by real Arr is allowed. Never apply an OUTPUT DROP policy or change default routes in the host namespace; use isolated network namespaces for firewall/handshake tests. `pompey-dev-vpn` is an optional legacy local helper, not the product VPN or a required CI gate.

The real service order is s6 cont-init then supervised services. Engines wait for a working VPN and generated configuration. `wire_stack.py` must fail on a required Prowlarr app/source, downloader category, or initialized Seerr→Arr connection failure. Seerr's public setup wizard is permitted before user id 1 exists; its API key legitimately gets 403 then. After initialization both Radarr and Sonarr are required. Plex/local login and optional Seerr chrome settings must not conceal a required miss.

Arr owns normal imports/upgrades; Prowlarr owns native source synchronization. Do not add speculative file deletion, identifier stripping, fake quality profiles, declined-request resurrection or unbounded shell retry loops. Mutating controller jobs share a lock; engine swaps also restore managed app databases. Release artifacts and resource commits are pinned in the manifest, with candidate discovery separate from promotion.
