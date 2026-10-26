# Release confidence

## Run the suite

Run `bash tests/run.sh`. It builds the amd64 product image once, adds an isolated test-tool directory, runs focused logic and required packet tests, then executes a fresh-install movie journey followed by three journeys on the same running household. Docker or Podman is required. In Distrobox the runner detects `distrobox-host-exec podman`; override with `POMPEY_CONTAINER_COMMAND` when needed. Use `--skip-build --image IMAGE` to test an already-built product image.

The checked-in synthetic media fixture is compressed and verified on extraction; CI does not need FFmpeg. Test tools have their own dynamic loader and libraries under `/test-tools`; installing them does not upgrade a shipped runtime library. The image's `/init`, cont-init, service scripts, controller job bodies and actual pinned applications run in the journey. Tests set qBittorrent to TCP-only because the synthetic peer supports TCP, and set all four controller intervals to two seconds. Production defaults, job deadlines, failure backoff, locks and quality profiles are unchanged. No application API is faked and no application binary is patched.

The runner uses `--network none`, verifies loopback and no external route before starting anything, then creates an internal veth underlay and a local WireGuard pair between two namespaces. A separate unprivileged fixture process serves metadata replay, one Torznab source, one tracker and a controlled synthetic BitTorrent peer. All fixture traffic stays inside the isolated container. The host firewall and default routes are never modified. Namespace creation requires NET_ADMIN and SYS_ADMIN in the isolated test container; engines drop to UID/GID 1000. Docker network-test containers also use `apparmor=unconfined` because the namespace bind mounts are denied by Docker’s default profile ([Docker documentation](https://docs.docker.com/engine/security/apparmor/)); this does not exercise the product’s HAOS AppArmor profile. Packet probes additionally require NET_RAW for ping. Packet checks run only in namespaces created for the test and are required, not silently skipped in CI.

## The first household journey

The real Seerr wizard creates its first administrator against external Plex account replay. The household adds a source through real Prowlarr. The production controller installs profiles and configures both Arr applications. A Seerr movie request triggers real Radarr search through Prowlarr and a real qBittorrent transfer. The fixture withholds the last torrent piece while an Arr maintenance check confirms no incomplete import. Releasing the piece lets qBittorrent verify it and Arr import. The controller routes the PG film into the kid library. The test verifies exact media bytes, subtitles, all five engine process identities and household diagnostics with a real release search.

The four movie scenarios cover PG → kid library, R → general library, unknown rating → general library, and a late-certification staging import followed by a real move-with-files. The first scenario proves an empty fresh installation. Subsequent scenarios retain earlier titles, requests, files and maintenance activity as background noise. Assertions select exact scenario IDs/hashes/prefixes and also verify that earlier requests, monitoring, library files and subtitles remain intact. No automatic retries hide interactions between scenarios.

The synthetic film lasts one minute. Its metadata, container runtime, catalogue size, torrent lengths and hashes agree. The real 1080p profile applies; a valid padded Matroska payload is approximately 16 MiB. The fixture implements the necessary external contracts and fails on unknown requests. Update/telemetry endpoints deliberately return an explicit unavailable response.

Artifacts under `.test-results/` include per-scenario prefixes/IDs, stage timings, bytes, paths, identities, preserved-background-title counts and timestamped external HTTP/DNS requests. Setup, controller restart, VPN recovery and batch completion require fresh successful configuration/controller jobs. Each ordinary journey instead checks current healthy services/jobs, Ready status, no unexpected attention and sources in both Arr apps at one shared invariant checkpoint. Stop-sharing removes the case torrent and queue entry and leaves no owned staging payload; failed case-owned native commands are rejected. Diagnostics run through nginx. Each scenario has its own torrent and media checksum; its run/case identifier occupies a reserved Matroska Void without changing duration or size. Native Arr probing and hash-verified transfers still apply. The runner bounds each batch and stops on any failure.

### State reuse and timing

The default `bash tests/run.sh` uses `--layout shared --scenarios 4`: one fresh install, then independent movie identities on the same running apps. This avoids repeated startup, configuration and shutdown while preserving realistic background state. Scenarios currently run sequentially; global settings are deliberately shared, and a future scenario changing them must declare and restore its changes. Destructive/restart scenarios can choose a separate configured clone.

`--layout cloned` first runs the complete fresh-install journey, waits for the container to stop, and exports `/data`, `/config` and `/media` as tar streams. Every completed journey is checked for an unclean engine shutdown; an unclean shutdown rejects the snapshot. The shipped image sets the same 30-second service grace and 3-second kill grace as acceptance, within Supervisor’s 300-second stop timeout. Each additional scenario imports a copy into a new container before startup. The clone retains the first title as background noise, then verifies that title/request/files while exercising a new identity. The seed is created using the current candidate during the current batch, never checked in or reused across engine versions. This is test state reuse, not a product database-recovery or migration guarantee. Every new container independently verifies network isolation and builds its local WireGuard fixture before starting clients.

CI uses `--layout ci`: the four shared cases plus one new movie in a cold configured clone, verifying all four retained titles. It exercises a second boot and reconciliation, with explicit test-only downloader settings retained; it is not a cross-version upgrade test.

Use `--layout fresh` for a fresh-install control per scenario. Use `--runs 3` to repeat independent batches for timing; this never retries a failure. All layouts share one product/test image build and one set of logic/packet checks within a command. For focused measurements after the full suite has passed:

```sh
bash tests/run.sh --skip-build --journey-only --layout shared
bash tests/run.sh --skip-build --journey-only --layout cloned
bash tests/run.sh --skip-build --journey-only --layout fresh
```

Every run prints section durations and writes `.test-results/timing-summary.json`. Each container retains `result.json`, `timing.json`, a complete log, a per-case report and the external request transcript. Snapshot export/import and container lifecycle overhead are separated from startup and scenario work. Elapsed stages are observation boundaries; application work can overlap them.

Before these changes, three independent fresh journeys took 91.44 seconds excluding shared checks, with a 30.48-second mean per container. A TCP-only experiment reduced the tracker-to-peer handshake delay from four seconds to milliseconds. Earlier long runs also exposed a first-admin/configuration race as well as the rating-routing polling delay; the short intervals remove the one-minute scheduling waits. Test setup creates the Seerr administrator before adding the indexer.

Earlier measurements on 2026-10-26, before these review fixes, for three movie scenarios, including container startup/cleanup and snapshot transfers:

| Layout | Batch seconds | Saving versus tuned fresh control |
| --- | ---: | ---: |
| Fresh install per scenario, TCP/fast timers | 91.57 | — |
| One fresh install + two configured clones | 72.15 | 21% |
| One fresh install + two cases on the same running apps | 43.51 | 52% |

The final shared batch spent 17.62 seconds on startup/setup, then 6.65, 7.18 and 6.04 seconds on the PG, R and unknown-rating journeys. The latter two preserved one and two earlier requests/titles/files respectively. Container lifecycle overhead was 6.01 seconds. The clone run took 1.80 seconds to export the seed and about 1.55–1.60 seconds to restore each copy; those costs are included above.

The earlier complete default suite passed in 61.78 seconds: the 43.51-second batch plus 18.27 seconds for cached builds, logic/syntax/lint and required isolated packet tests. TCP-only removes the four-second peer-negotiation delay, and short timers remove minute-long scheduling tails, but first-install provisioning still dominates a separate fresh container. Reusing the running household provides the largest measured saving.

These are development-machine timings using host Podman from Fedora Distrobox, not GitHub runner guarantees. Cold image downloads/builds are additional. Repeated future scenarios may have different costs, especially TV, upgrades or restart workflows.

### Review follow-up measurement

`bash tests/run.sh --layout ci` passed in **132.61 seconds** with cached image layers on the same development machine: nine product logic checks, nine harness checks, eight required packet tests, four shared movie cases, the real VPN fault/reconnect check and one configured-clone case. Both containers had empty unexpected-request lists, including final DNS/HTTP verification.

| Section | Seconds |
| --- | ---: |
| Product/test image builds | 4.70 |
| Logic, runtime syntax and shell/UI lint | 1.72 |
| Required isolated packets | 12.05 |
| Shared household startup | 22.95 |
| PG / R / unknown / late-certification cases | 9.09 / 8.96 / 8.91 / 9.92 |
| VPN fault, local access and recovery | 15.23 |
| Cold seed export / restore | 1.60 / 1.65 |
| Clone startup / new request with four prior titles retained | 12.79 / 10.25 |
| Combined shared + clone containers, including lifecycle costs | 113.72 |

This covers more work than the earlier three-film benchmark. Fresh controller health adds observations after each outcome; the late-certification case exercises an actual file move, and CI now pays for one independent configured boot. The timing summary and per-container reports retain exact boundaries; values can overlap or include small unlisted bookkeeping costs and are not additive guarantees.

### Shared invariant checkpoint measurement

The same five-journey CI layout passed in **123.71 seconds**, versus **132.61 seconds** above: **8.91 seconds (6.7%) saved** overall. Its integration containers took **104.50 seconds**, versus **113.72 seconds**: **9.22 seconds (8.1%) saved**. Both runs used cached layers on the same development machine; these are individual observations, not a statistical CI guarantee.

| Section | Previous seconds | Shared-checkpoint seconds |
| --- | ---: | ---: |
| Complete suite | 132.61 | 123.71 |
| Integration containers, including seed/lifecycle work | 113.72 | 104.50 |
| PG / R / unknown journeys | 9.09 / 8.96 / 8.91 | 5.01 / 5.90 / 6.08 |
| Late-certification journey, with controller restart | 9.92 | 10.98 |
| Shared container / configured-clone container | 82.77 / 30.94 | 73.75 / 30.75 |

Ordinary invariant checkpoints took 0.07–0.13 seconds. The restart checkpoint retained its fresh-job wait (1.54 seconds). The clone’s final cleanup barrier took 4.12 seconds; all five cases ended with `stopSharingVerified: true`, and neither container recorded an unexpected invariant event. Full checksum sweeps and the real VPN fault/recovery still ran.

Consolidation alone did not measurably accelerate the suite: the first experimental integration batch took 115.30 seconds because native cleanup overlapped the old health waits. Explicitly triggering another native completion scan was inconsistent and was removed. The retained improvement overlaps pending cleanup with later journeys and enforces one final barrier. The full run passed 11 product logic checks, 12 harness checks and 8 packet checks; a subsequent deadline regression brings the harness count to 13 and verifies that later cases cannot reset an earlier owner’s cleanup budget. The final built-runtime worker passed all 11 product and 13 harness checks; a focused fresh-install/barrier/VPN run passed in 52.10 seconds after placing final file and command checks after the cleanup barrier.

## Adding scenarios

Keep outcome assertions in scenario modules such as [`movie_requests.py`](../tests/journey/movie_requests.py). The small [`journey.py`](../tests/journey/journey.py) driver owns startup, per-scenario budgets and final verification. New scenarios should accept the household, a namespaced catalogue case and background state rather than create new global clients or bootstrap another stack.

Shared operations live in:

| Utility | Responsibility |
| --- | --- |
| [`household.py`](../tests/journey/household.py) | Real Arr/Sonarr/Prowlarr/Seerr/qBittorrent clients, first-run setup, native command completion, engine identities and background preservation |
| [`invariants.py`](../tests/journey/invariants.py) | Shared health/background/cleanup checkpoint, cumulative source evidence and owner-attributed native command failures |
| [`support.py`](../tests/journey/support.py) | Cookie-aware JSON/form requests, bounded polling, independent deadlines, atomic evidence checkpoints and streaming checksums |
| [`torrent_files.py`](../tests/journey/torrent_files.py) | Exact block reads spanning fixture files without storing every film in memory |
| [`container_runtime.py`](../tests/container_runtime.py) | Host CLI execution, named worker cleanup, immutable image IDs and cold seed transfers |

Use `household.wait` for eventual observations and `wait_command` for real native commands. Expected network startup/502–504 failures can be observed again; permanent API errors, malformed responses, failed commands and assertion failures surface immediately. Startup gets 180 seconds, each scenario gets an independent 120 seconds, and host deadlines include those budgets plus bounded diagnostics/shutdown time. Waiting never borrows a later scenario's budget, and nested HTTP requests respect the current wait's remaining time.

Checkpoints update `result.json` and each case report throughout execution, with explicit running/passed/failed states. Failed runs retain the original exception, last completed checkpoint, partial timing summary and read-only native movie/queue/command/history/blocklist/source/torrent/request snapshots and real log tails. Categories distinguish fixture contracts, timeout stages, native command failure, HTTP failure, assertion and background ownership/field regressions. Diagnostic collection has a separate ten-second HTTP budget and cannot replace the original failure. Complete logs remain artifacts; successful console output stays compact. Check workers and household containers both have named, bounded cleanup, including after timeout.

The shared invariant checkpoint verifies all owned titles, including the just-completed journey, once per journey. It also checks all owned torrent/queue/staging cleanup and the native command history for earlier titles, so late failures remain attributable. Configured clones get an initial baseline check; batch completion and VPN recovery retain full checksum sweeps. Background verification batches native API reads and caches file checksums only while device/inode/size/mtime/ctime remain unchanged. Every batch still ends with a full checksum pass. Global setting changes must be explicit and restored by the owning scenario; changes intentionally made to an earlier owned object must also update that object's expected evidence. The current numeric `--scenarios` option repeats movie rating parameters for timing; it does not claim coverage of distinct unimplemented features.

The fixture serves UDP/TCP DNS with explicit A/AAAA/negative replay and records queries; application hostnames are not inserted into `/etc/hosts`. Final evidence requires real Radarr/TMDB lookups over WireGuard. Known unavailable telemetry domains return NXDOMAIN; other unknown DNS/HTTP requests fail the contract.

The external world rejects fixture paths on the wrong hostname and has no generic successful metadata fallback. Small status polls omit the growing request transcript; the full timestamped transcript is written durably as events occur and collected at exit, including after a broken tunnel. Unknown requests are asserted after final verification too. Synthetic source files store the zero-filled Matroska Void as sparse holes, and the peer reads blocks from files. Logical lengths, piece hashes and all bytes transferred through real qBittorrent remain unchanged. New fixture responses should model an observed external contract rather than broaden a catch-all response.

Focused harness regressions cover permanent/transient failures, nested deadlines, durable partial reports, mandatory final checksums, cross-file/empty-file block boundaries, truncated fixtures, incorrect hosts, DNS replay, cumulative invariant rejection and late native-command ownership. A scheduler regression also verifies that recurring jobs cannot starve other due work. Before these review fixes, the reviewed default suite passed in 61.11 seconds with cached layers, including eight product logic tests, eight harness tests, eight packet tests and three real journeys. A configured-clone run also passed. An intentional real-Radarr 404 failed about 65 ms after the request checkpoint and retained the partial case and native failure snapshots.

## Cumulative invariant evidence

Acceptance sets `POMPEY_EVENT_LOG` to an append-only journal in the container’s temporary directory. The shipped code records controller failures before resetting retry counters, controller starts, and policy attention immediately after relevant durable state writes. Normal installations leave this opt-in trace disabled. File locking prevents concurrent producer/read races; controller restarts retain earlier events in the same container. The harness requires the controller-start event, so a disconnected producer cannot silently pass.

`invariant-events.json` preserves setup and household phases. Assertions arm when initialized setup becomes healthy: pre-setup configuration failures remain visible but expected; subsequent controller failures and notices fail even after successful retry or notice removal. Time-dependent notices are also recorded when the normal health sampler observes them. This is not a claim to observe every sub-second service outage or a notice condition that was never persisted or sampled.

At each checkpoint, retained native Arr command history is checked against every owned title and its ownership start time. Earlier cases’ late failures are attributed to their owner. Observed failures are retained even if later native history omits them. Commands purged before any observation remain outside this history-based check; application binaries are not instrumented. Scheduled telemetry/update failures without owned title targets are excluded. Future scenarios intentionally generating policy notices need explicit expectations rather than a blanket exemption.

The harness retains fresh-job waits where lifecycle changes justify them. Ordinary variants use immediate health checks; the actual controller job bodies, production retry policy and health cadence are unchanged. Cleanup is eventual: checkpoints record completed transfers still awaiting native cleanup with a per-title 60-second deadline, reject transfer regressions, and enforce torrent/queue/staging cleanup at the batch barrier. Later journeys overlap legitimate cleanup from earlier ones. No cleanup outcome is skipped; final success requires the same cleanup guarantees for every owned title. Per-checkpoint durations, pending owners and the final barrier duration make those costs visible.

## Scope and expansion

Focused tests remain for paths/symlinks, request ownership/history discontinuities, sharing boundaries and WireGuard parsing. The previous disconnected real-engine suites, fake application HTTP servers, anime catalogue matrix, fake-VPN helper and artifact/runtime download harnesses are removed.

These movie scenarios establish the foundation. Subsequent scenarios must cover TV/season imports, non-admin requests, forward application/database upgrades, all sharing policies and hardlinks, explicit libraries/quality choices, direct grabs and ambiguous imports, request removal/history reset, blocked diagnostics, failure isolation, VPN paste/rotation and startup without a tunnel, NAT-PMP and anime. Basic same-version restart and continued-access/reconnect checks now run. See [the review response](integration-review-response.md) for each comment’s disposition and exact limits. Broader coverage is required before unattended dependency merge/release is enabled. The first journey alone is not evidence that those behaviors have passed.

The automated acceptance suite replaces the manual release gate. Actual Supervisor installation, Ingress forwarding on HAOS, NAS permission/hardlink behavior and Plex discovery/playback remain explicit unautomated boundaries. A local Docker/Podman run does not reproduce them. Recovery relies on HA cold backups; automatic binary/database rollback and old-layout migration are not product guarantees.

## PR completion

Validate locally, push and take at most one non-waiting CI snapshot. Report passed, failed and pending checks accurately, then end the turn. Do not poll or wait for CI unless the user explicitly asks. `CI required` remains the stable aggregate branch-protection check. Candidate discovery is separate from acceptance and promotion.

## Network fault check

After the shared movie batch, UID 1000 probes a fixture listener and DNS on the veth underlay; both must be blocked by the shipped firewall. The live nftables output chain must have drop policy. The remote WireGuard link is then taken down and the actual client peer removed to accelerate native fault detection. Status must report VPN unavailable while local Seerr, Prowlarr, Arr and downloader APIs remain reachable. DNS and underlay access must stay blocked. The production WireGuard service reconnects after restoration; fresh controller health, DNS, a real metadata refresh and prior media are verified again.

This checks the native reconnect loop, not the production 180-second stale-handshake detection delay or initial startup without VPN. It never edits readiness markers or mocks the VPN service. The fault has a separate 90-second budget. Product firewall rules are observed through actual packets rather than compared with their own generator.
