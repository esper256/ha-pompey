# Release confidence

## Run the suite

Run `bash tests/run.sh`. It builds the amd64 product image once, adds an isolated test-tool directory, runs focused logic and required packet tests, then executes one real household journey. Docker or Podman is required. In Distrobox the runner detects `distrobox-host-exec podman`; override with `POMPEY_CONTAINER_COMMAND` when needed. Use `--skip-build --image IMAGE` to test an already-built product image.

The checked-in synthetic media fixture is compressed and verified on extraction; CI does not need FFmpeg. Test tools have their own dynamic loader and libraries under `/test-tools`; installing them does not upgrade a shipped runtime library. The image's `/init`, cont-init, service scripts, controller and actual pinned applications run unchanged in the journey. No application API is faked, no binary is patched, and production quality profiles are not weakened.

The runner uses `--network none`, verifies loopback and no external route before starting anything, then creates an internal veth underlay and a local WireGuard pair between two namespaces. A separate unprivileged fixture process serves metadata replay, one Torznab source, one tracker and a controlled synthetic BitTorrent peer. All fixture traffic stays inside the isolated container. The host firewall and default routes are never modified. Namespace creation requires NET_ADMIN and SYS_ADMIN in the isolated test container; engines drop to UID/GID 1000. Packet checks run only in namespaces created for the test and are required, not silently skipped in CI.

## The first household journey

The real Seerr wizard creates its first administrator against external Plex account replay. The household adds a source through real Prowlarr. The production controller installs profiles and configures both Arr applications. A Seerr movie request triggers real Radarr search through Prowlarr and a real qBittorrent transfer. The fixture withholds the last torrent piece while an Arr maintenance check confirms no incomplete import. Releasing the piece lets qBittorrent verify it and Arr import. The controller routes the PG film into the kid library. The test verifies exact media bytes, subtitles, all five engine process identities and household diagnostics with a real release search.

The synthetic film lasts one minute. Its metadata, container runtime, catalogue size, torrent lengths and hashes agree. The real 1080p profile applies; a valid padded Matroska payload is approximately 16 MiB. The fixture implements the necessary external contracts and fails on unknown requests. Update/telemetry endpoints deliberately return an explicit unavailable response.

Artifacts under `.test-results/` include stage timings, transferred bytes, library path, identities and the external request transcript. The runner bounds the journey and does not retry a whole failed run. Development runs of the current pinned bundle completed the journey in roughly 25–80 seconds, including application startup and setup; timing varies with the production maintenance schedule. These are local measurements, not a GitHub runner guarantee; cold image builds also download and extract the bundled applications.

## Scope and expansion

Focused tests remain for paths/symlinks, request ownership/history discontinuities, sharing boundaries and WireGuard parsing. The previous disconnected real-engine suites, fake application HTTP servers, anime catalogue matrix, fake-VPN helper and artifact/runtime download harnesses are removed.

This first scenario establishes the foundation. Subsequent scenarios must cover TV/season imports, upgrades, all sharing policies, direct grabs and ambiguous imports, request removal/history reset, failures and restart behavior, and VPN outages/rotation. Broader coverage is required before unattended dependency merge/release is enabled. The first journey alone is not evidence that those behaviors have passed.

The automated acceptance suite replaces the manual release gate. Actual Supervisor installation, Ingress forwarding on HAOS, NAS permission/hardlink behavior and Plex discovery/playback remain explicit unautomated boundaries. A local Docker/Podman run does not reproduce them. Recovery relies on HA cold backups; automatic binary/database rollback and old-layout migration are not product guarantees.

## PR completion

Validate locally, push and take at most one non-waiting CI snapshot. Report passed, failed and pending checks accurately, then end the turn. Do not poll or wait for CI unless the user explicitly asks. `CI required` remains the stable aggregate branch-protection check. Candidate discovery is separate from acceptance and promotion.
