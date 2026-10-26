# Integration foundation campaign

## Decisions

Supervisor builds one amd64 image locally. It contains every checksum-pinned engine and pinned Recyclarr resource. Runtime does not download or replace applications. Fresh installations are the target; no legacy migration, downgrade compatibility or binary/database rollback manager is required. Home Assistant cold backups provide recovery.

All engines and controller jobs use one unprivileged `pompey` identity. Executables stay root owned. Privileged network setup installs a fail-closed firewall before engines start; local applications can run without a working tunnel. All internet, including DNS and metadata, requires the VPN. Bootstrap DNS is restricted to privileged endpoint resolution. VPN support is provider neutral.

Keep automatic imports for Prowlarr direct grabs, durable intent and ambiguous-command review. Preserve unexpected files and continue unrelated jobs. Add household diagnostics for request, search, download, import and routing failures. Request closeout remains conservative and must brake on suspected history resets or bulk disappearance.

## Acceptance boundary

The basic journey uses the shipped image, real Seerr, Arr, Prowlarr, qBittorrent and Recyclarr, and production orchestration. Only external services are fixtures: metadata HTTP replay, one Torznab indexer, one tracker and a synthetic BitTorrent peer. Short synthetic media has honest durations, sizes and piece hashes; production quality profiles are unchanged.

The runner proves a new isolated network namespace has no external interface or route before launching a downloader or peer. Local WireGuard fixtures may then be created inside that namespace. No external torrent traffic, discovery or host firewall/default-route changes are permitted.

Keep one empty fresh-install movie journey, then reuse the running household for namespaced PG/R/unknown-rating variants, retaining and verifying earlier requests and media as background noise. Cold configured clones from the same candidate support scenarios that need separate global state. Measure preparation, startup, search, transfer and import separately. Preserve diagnostics and explicitly distinguish fixture failure, timeout and application failure. Do not use whole-suite retries. Keep focused logic tests for difficult boundaries and required isolated packet tests; remove tests that restate implementation.

## Delivery

1. Vendor the immutable runtime, simplify startup/maintenance and drop engine privileges.
2. Replace disconnected integration suites with one measured request-to-library journey.
3. Expand household outcomes using that foundation before enabling unattended dependency promotion and releases. Required CI remains the release authority; manual HAOS acceptance is no longer a release gate. Document the actual boundary: this suite does not reproduce Supervisor or prove real Plex playback/NAS behavior.

Upstream updates must test the candidate's actual binaries and configuration. Passing a fresh-start test does not establish every existing-database upgrade or every household journey; promotion remains disabled while the acceptance foundation is being established.
