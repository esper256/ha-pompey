# Release confidence

## Run the suite

Run `bash tests/run.sh`. It builds the amd64 product image once, adds an isolated test-tool directory, runs focused logic and required packet tests, then executes a fresh-install movie journey followed by two journeys on the same running household. Docker or Podman is required. In Distrobox the runner detects `distrobox-host-exec podman`; override with `POMPEY_CONTAINER_COMMAND` when needed. Use `--skip-build --image IMAGE` to test an already-built product image.

The checked-in synthetic media fixture is compressed and verified on extraction; CI does not need FFmpeg. Test tools have their own dynamic loader and libraries under `/test-tools`; installing them does not upgrade a shipped runtime library. The image's `/init`, cont-init, service scripts, controller job bodies and actual pinned applications run in the journey. Tests set qBittorrent to TCP-only because the synthetic peer supports TCP, and set controller configuration/routing intervals to two/one seconds. Production defaults, job deadlines, failure backoff, locks and quality profiles are unchanged. No application API is faked and no application binary is patched.

The runner uses `--network none`, verifies loopback and no external route before starting anything, then creates an internal veth underlay and a local WireGuard pair between two namespaces. A separate unprivileged fixture process serves metadata replay, one Torznab source, one tracker and a controlled synthetic BitTorrent peer. All fixture traffic stays inside the isolated container. The host firewall and default routes are never modified. Namespace creation requires NET_ADMIN and SYS_ADMIN in the isolated test container; engines drop to UID/GID 1000. Packet checks run only in namespaces created for the test and are required, not silently skipped in CI.

## The first household journey

The real Seerr wizard creates its first administrator against external Plex account replay. The household adds a source through real Prowlarr. The production controller installs profiles and configures both Arr applications. A Seerr movie request triggers real Radarr search through Prowlarr and a real qBittorrent transfer. The fixture withholds the last torrent piece while an Arr maintenance check confirms no incomplete import. Releasing the piece lets qBittorrent verify it and Arr import. The controller routes the PG film into the kid library. The test verifies exact media bytes, subtitles, all five engine process identities and household diagnostics with a real release search.

The three movie scenarios cover PG → kid library, R → general library, and unknown rating → general library. The first scenario proves an empty fresh installation. Subsequent scenarios retain earlier titles, requests, files and maintenance activity as background noise. Assertions select exact scenario IDs/hashes/prefixes and also verify that earlier requests, library files and subtitles remain intact. No automatic retries hide interactions between scenarios.

The synthetic film lasts one minute. Its metadata, container runtime, catalogue size, torrent lengths and hashes agree. The real 1080p profile applies; a valid padded Matroska payload is approximately 16 MiB. The fixture implements the necessary external contracts and fails on unknown requests. Update/telemetry endpoints deliberately return an explicit unavailable response.

Artifacts under `.test-results/` include per-scenario prefixes/IDs, stage timings, bytes, paths, identities, preserved-background-title counts and timestamped external requests. Each scenario has its own torrent and media checksum; its run/case identifier occupies a reserved Matroska Void without changing duration or size. Native Arr probing and hash-verified transfers still apply. The runner bounds each batch and stops on any failure.

### State reuse and timing

The default `bash tests/run.sh` uses `--layout shared --scenarios 3`: one fresh install, then independent movie identities on the same running apps. This avoids repeated startup, configuration and shutdown while preserving realistic background state. Scenarios currently run sequentially; global settings are deliberately shared, and a future scenario changing them must declare and restore its changes. Destructive/restart scenarios can choose a separate configured clone.

`--layout cloned` first runs the complete fresh-install journey, waits for the container to stop, and exports `/data`, `/config` and `/media` as tar streams. An unclean engine shutdown rejects the snapshot. Each additional scenario imports a copy into a new container before startup. The clone retains the first title as background noise, then verifies that title/request/files while exercising a new identity. The seed is created using the current candidate during the current batch, never checked in or reused across engine versions. This is test state reuse, not a product database-recovery or migration guarantee. Every new container independently verifies network isolation and builds its local WireGuard fixture before starting clients.

Use `--layout fresh` for a fresh-install control per scenario. Use `--runs 3` to repeat independent batches for timing; this never retries a failure. All layouts share one product/test image build and one set of logic/packet checks within a command. For focused measurements after the full suite has passed:

```sh
bash tests/run.sh --skip-build --journey-only --layout shared
bash tests/run.sh --skip-build --journey-only --layout cloned
bash tests/run.sh --skip-build --journey-only --layout fresh
```

Every run prints section durations and writes `.test-results/timing-summary.json`. Each container retains `result.json`, `timing.json`, a complete log, a per-case report and the external request transcript. Snapshot export/import and container lifecycle overhead are separated from startup and scenario work. Elapsed stages are observation boundaries; application work can overlap them.

Before these changes, three independent fresh journeys took 91.44 seconds excluding shared checks, with a 30.48-second mean per container. A TCP-only experiment reduced the tracker-to-peer handshake delay from four seconds to milliseconds. Earlier long runs also exposed a first-admin/configuration race as well as the rating-routing polling delay; the short intervals remove the one-minute scheduling waits. Test setup creates the Seerr administrator before adding the indexer.

Measurements on 2026-10-26 for three movie scenarios, including container startup/cleanup and snapshot transfers:

| Layout | Batch seconds | Saving versus tuned fresh control |
| --- | ---: | ---: |
| Fresh install per scenario, TCP/fast timers | 91.57 | — |
| One fresh install + two configured clones | 72.15 | 21% |
| One fresh install + two cases on the same running apps | 43.51 | 52% |

The final shared batch spent 17.62 seconds on startup/setup, then 6.65, 7.18 and 6.04 seconds on the PG, R and unknown-rating journeys. The latter two preserved one and two earlier requests/titles/files respectively. Container lifecycle overhead was 6.01 seconds. The clone run took 1.80 seconds to export the seed and about 1.55–1.60 seconds to restore each copy; those costs are included above.

The complete default suite passed in 61.78 seconds: the 43.51-second batch plus 18.27 seconds for cached builds, logic/syntax/lint and required isolated packet tests. TCP-only removes the four-second peer-negotiation delay, and short timers remove minute-long scheduling tails, but first-install provisioning still dominates a separate fresh container. Reusing the running household provides the largest measured saving.

These are development-machine timings using host Podman from Fedora Distrobox, not GitHub runner guarantees. Cold image downloads/builds are additional. Repeated future scenarios may have different costs, especially TV, upgrades or restart workflows.

## Adding scenarios

Keep outcome assertions in scenario modules such as [`movie_requests.py`](../tests/journey/movie_requests.py). The small [`journey.py`](../tests/journey/journey.py) driver owns startup, per-scenario budgets and final verification. New scenarios should accept the household, a namespaced catalogue case and background state rather than create new global clients or bootstrap another stack.

Shared operations live in:

| Utility | Responsibility |
| --- | --- |
| [`household.py`](../tests/journey/household.py) | Real Arr/Sonarr/Prowlarr/Seerr/qBittorrent clients, first-run setup, native command completion, engine identities and background preservation |
| [`support.py`](../tests/journey/support.py) | Cookie-aware JSON/form requests, bounded polling, independent deadlines, atomic evidence checkpoints and streaming checksums |
| [`torrent_files.py`](../tests/journey/torrent_files.py) | Exact block reads spanning fixture files without storing every film in memory |
| [`container_runtime.py`](../tests/container_runtime.py) | Host CLI execution, named worker cleanup, immutable image IDs and cold seed transfers |

Use `household.wait` for eventual observations and `wait_command` for real native commands. Expected network startup/502–504 failures can be observed again; permanent API errors, malformed responses, failed commands and assertion failures surface immediately. Startup gets 180 seconds, each scenario gets an independent 120 seconds, and host deadlines include those budgets plus bounded diagnostics/shutdown time. Waiting never borrows a later scenario's budget, and nested HTTP requests respect the current wait's remaining time.

Checkpoints update `result.json` and each case report throughout execution, with explicit running/passed/failed states. Failed runs retain the original exception, last completed checkpoint, partial timing summary and read-only native movie/queue/command/torrent/request snapshots. Diagnostic collection has a separate ten-second HTTP budget and cannot replace the original failure. Complete logs remain artifacts; successful console output stays compact. Check workers and household containers both have named, bounded cleanup, including after timeout.

Background verification batches native API reads and caches file checksums only while device/inode/size/mtime/ctime remain unchanged. Every batch still ends with a full checksum pass. Global setting changes must be explicit and restored by the owning scenario; changes intentionally made to an earlier owned object must also update that object's expected evidence. The current numeric `--scenarios` option repeats movie rating parameters for timing; it does not claim coverage of distinct unimplemented features.

The external world rejects fixture paths on the wrong hostname and has no generic successful metadata fallback. Small status polls omit the growing request transcript; the full timestamped transcript is collected once at exit. Synthetic source files store the zero-filled Matroska Void as sparse holes, and the peer reads blocks from files. Logical lengths, piece hashes and all bytes transferred through real qBittorrent remain unchanged. New fixture responses should model an observed external contract rather than broaden a catch-all response.

Eight focused harness regressions cover permanent/transient failures, nested deadlines, durable partial reports, mandatory final checksums, cross-file/empty-file block boundaries, truncated fixtures and incorrect hosts. The reviewed default suite passed in 61.11 seconds with cached layers, including eight product logic tests, eight harness tests, eight packet tests and three real journeys. A configured-clone run also passed. An intentional real-Radarr 404 failed about 65 ms after the request checkpoint and retained the partial case and native failure snapshots.

## Scope and expansion

Focused tests remain for paths/symlinks, request ownership/history discontinuities, sharing boundaries and WireGuard parsing. The previous disconnected real-engine suites, fake application HTTP servers, anime catalogue matrix, fake-VPN helper and artifact/runtime download harnesses are removed.

These movie scenarios establish the foundation. Subsequent scenarios must cover TV/season imports, upgrades, all sharing policies, direct grabs and ambiguous imports, request removal/history reset, failures and restart behavior, and VPN outages/rotation. Broader coverage is required before unattended dependency merge/release is enabled. The first journey alone is not evidence that those behaviors have passed.

The automated acceptance suite replaces the manual release gate. Actual Supervisor installation, Ingress forwarding on HAOS, NAS permission/hardlink behavior and Plex discovery/playback remain explicit unautomated boundaries. A local Docker/Podman run does not reproduce them. Recovery relies on HA cold backups; automatic binary/database rollback and old-layout migration are not product guarantees.

## PR completion

Validate locally, push and take at most one non-waiting CI snapshot. Report passed, failed and pending checks accurately, then end the turn. Do not poll or wait for CI unless the user explicitly asks. `CI required` remains the stable aggregate branch-protection check. Candidate discovery is separate from acceptance and promotion.
