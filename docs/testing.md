# Testing and release validation

## Run the suite

Run `bash tests/run.sh`. It builds the amd64 product image once, adds an isolated test-tool directory, runs focused logic and required packet tests, then executes the fifteen named integration scenarios, starting with a fresh installation and retaining shared state. Docker or Podman is required. In Distrobox the runner detects `distrobox-host-exec podman`; override with `POMPEY_CONTAINER_COMMAND` when needed. Use `--skip-build --image IMAGE` to test an already-built product image.

The checked-in synthetic media fixture is compressed and verified on extraction; CI does not need FFmpeg. Test tools have their own dynamic loader and libraries under `/test-tools`; installing them does not upgrade a shipped runtime library. The image's `/init`, cont-init, service scripts, controller job bodies and actual pinned applications run in the scenario. Tests set qBittorrent to TCP-only because the synthetic peer supports TCP, and set all four controller intervals to two seconds. Production defaults, job deadlines, failure backoff, locks and quality profiles are unchanged. No application API is faked and no application binary is patched.

The runner uses `--network none`, verifies loopback and no external route before starting anything, then creates an internal veth underlay and a local WireGuard pair between two namespaces. A separate unprivileged fixture process serves metadata replay, one Torznab source, one tracker and a controlled synthetic BitTorrent peer. All fixture traffic stays inside the isolated container. The host firewall and default routes are never modified. Namespace creation requires NET_ADMIN and SYS_ADMIN in the isolated test container; applications and the controller run as root, matching production. The external fixture process alone drops to UID/GID 65534. Docker network-test containers also use `apparmor=unconfined` because the namespace bind mounts are denied by Docker’s default profile ([Docker documentation](https://docs.docker.com/engine/security/apparmor/)); this does not exercise the product’s HAOS AppArmor profile. Packet probes additionally require NET_RAW for ping. Packet checks run only in namespaces created for the test and are required, not silently skipped in CI.

Use `bash tests/run.sh --layout ci --suite pr` for the focused PR cohort: a fresh movie request, a TV episode, executable-file exclusion, and one configured-clone control. Every cohort still runs the logic, runtime syntax/lint and required isolated packet checks. `--suite release` (the default) exercises all fifteen shared scenarios plus a configured clone. Main, scheduled and dependency-candidate runs use the release cohort; ordinary PRs use the focused cohort.

Release acceptance also builds the actual previous stable product image and runs `--upgrade-from IMAGE`. That image creates a completed movie, a waiting request and an incomplete torrent, then stops cleanly. The candidate starts with those real application databases, configuration and media; it must preserve request/indexer identities, cached partial bytes and completed files, then fulfil the pending requests. A previously unseen metadata lookup proves the candidate's DNS/TLS path instead of relying on cached catalogue data. This is forward-upgrade coverage for the selected release, not a guarantee for every historic layout or database. The fallback baseline is the immutable shipped 0.4.2 source revision while the newest public tag is older than that installation.

The strict VPN download-resumption scenario is required in the release cohort. There is no accepted-timeout exception. The product reopens qBittorrent connections after a recovered VPN interface replacement, preserving native partial downloads and user-stopped torrents.

## The first integration scenario

Focused storage checks run real filesystem operations as root against root-owned `0755` directories containing files left by an earlier UID 1000 installation, reject read-only storage, and verify that diagnostics omit mount credentials. Startup probes listing, creation, rename and removal without changing existing files. Integration batches also require qBittorrent file-log output in the container stream shown by Home Assistant. These checks do not reproduce a real Supervisor-managed SMB mount or establish NAS compatibility.

The real Seerr wizard creates its first administrator against external Plex account replay. The test adds an indexer through real Prowlarr. The production controller installs profiles and configures both Arr applications. A Seerr movie request triggers real Radarr search through Prowlarr and a real qBittorrent transfer. The fixture withholds the last torrent piece while an Arr maintenance check confirms no incomplete import. Releasing the piece lets qBittorrent verify it and Arr import. The controller routes the PG film into the library for lower age ratings. The test verifies exact media bytes, subtitles, all five engine process identities and the controller identity and diagnostics with a real release search.

The four movie scenarios cover PG → library for lower age ratings, R → general library, unknown rating → general library, and a late-certification staging import followed by a real move-with-files. The first scenario proves an empty fresh installation. Subsequent scenarios retain earlier titles, requests, files and maintenance activity as background noise. Assertions select exact scenario IDs/hashes/prefixes and also verify that earlier requests, monitoring, library files and subtitles remain intact. No automatic retries hide interactions between scenarios.

The synthetic film lasts one minute. Its metadata, container runtime, catalogue size, torrent lengths and hashes agree. The real 1080p profile applies; a valid padded Matroska payload is approximately 16 MiB. The fixture implements the necessary external contracts and fails on unknown requests. Update/telemetry endpoints deliberately return an explicit unavailable response.

Artifacts under `.test-results/` include per-scenario prefixes/IDs, stage timings, bytes, paths, identities, preserved-background-title counts and timestamped external HTTP/DNS requests. Setup, controller restart, VPN recovery and batch completion require fresh successful configuration/controller jobs. Each ordinary scenario instead checks current healthy services/jobs, Ready status, no unexpected attention and sources in both Arr apps at one shared invariant checkpoint. Stop-sharing removes the case torrent and queue entry and leaves no owned staging payload; failed case-owned native commands are rejected. Diagnostics run through nginx. Each scenario has its own torrent and media checksum; its run/case identifier occupies a reserved Matroska Void without changing duration or size. Native Arr probing and hash-verified transfers still apply. The runner bounds each batch and stops on any failure.

### State reuse and timing

The default `bash tests/run.sh` uses `--layout shared --scenarios 15`: one fresh install, then the named movie, TV, request-closeout, same-container restart and VPN scenarios in [scenario catalogue](integration-scenarios.md). This avoids repeated startup, configuration and shutdown while preserving realistic background state. Scenarios currently run sequentially; global settings are deliberately shared, and a scenario changing them must declare its changes and either restore them or carry the new expected state into subsequent checks. VPN rotation deliberately persists the replacement configuration into the cold clone. Destructive/restart scenarios can choose a separate configured clone.

`--layout cloned` first runs the complete fresh-install scenario, waits for the container to stop, and exports `/data`, `/config` and `/media` as tar streams. Every completed scenario is checked for an unclean engine shutdown; an unclean shutdown rejects the snapshot. The shipped image sets the same 30-second service grace and 3-second kill grace as acceptance, within Supervisor’s 300-second stop timeout. Each additional scenario imports a copy into a new container before startup. The clone retains the first title as background noise, then verifies that title/request/files while exercising a new identity. The seed is created using the current candidate during the current batch, never checked in or reused across engine versions. This is test state reuse, not a product database-recovery or migration guarantee. Every new container independently verifies network isolation and builds its local WireGuard fixture before starting clients.

CI uses `--layout ci`: the fifteen shared scenarios plus one new movie in a cold configured clone, verifying the retained movie/TV files, request states and monitoring ledger. It exercises a second boot and reconciliation, with explicit test-only downloader settings retained; the separate `--upgrade-from` pass supplies cross-version coverage.

Use `--layout fresh` for a fresh-install control per scenario. Use `--runs 3` to repeat independent batches for timing; this never retries a failure. All layouts share one product/test image build and one set of logic/packet checks within a command. For focused measurements after the full suite has passed:

```sh
bash tests/run.sh --skip-build --integration-only --layout shared
bash tests/run.sh --skip-build --integration-only --layout cloned
bash tests/run.sh --skip-build --integration-only --layout fresh
```

Every run prints section durations and writes `.test-results/timing-summary.json`. Each container retains `result.json`, `timing.json`, a complete log, a per-case report and the external request transcript. Snapshot export/import and container lifecycle overhead are separated from startup and scenario work. Elapsed stages are observation boundaries; application work can overlap them.

Before these changes, three independent fresh scenarios took 91.44 seconds excluding shared checks, with a 30.48-second mean per container. A TCP-only experiment reduced the tracker-to-peer handshake delay from four seconds to milliseconds. Earlier long runs also exposed a first-admin/configuration race as well as the rating-routing polling delay; the short intervals remove the one-minute scheduling waits. Test setup creates the Seerr administrator before adding the indexer.

Earlier measurements on 2026-10-26, before these review fixes, for three movie scenarios, including container startup/cleanup and snapshot transfers:

| Layout | Batch seconds | Saving versus tuned fresh control |
| --- | ---: | ---: |
| Fresh install per scenario, TCP/fast timers | 91.57 | — |
| One fresh install + two configured clones | 72.15 | 21% |
| One fresh install + two cases on the same running apps | 43.51 | 52% |

The final shared batch spent 17.62 seconds on startup/setup, then 6.65, 7.18 and 6.04 seconds on the PG, R and unknown-rating scenarios. The latter two preserved one and two earlier requests/titles/files respectively. Container lifecycle overhead was 6.01 seconds. The clone run took 1.80 seconds to export the seed and about 1.55–1.60 seconds to restore each copy; those costs are included above.

The earlier complete default suite passed in 61.78 seconds: the 43.51-second batch plus 18.27 seconds for cached builds, logic/syntax/lint and required isolated packet tests. TCP-only removes the four-second peer-negotiation delay, and short timers remove minute-long scheduling tails, but first-install provisioning still dominates a separate fresh container. Reusing the running application stack provides the largest measured saving.

These are development-machine timings using host Podman from Fedora Distrobox, not GitHub runner guarantees. Cold image downloads/builds are additional. Repeated future scenarios may have different costs, especially TV, upgrades or restart workflows.

### Review follow-up measurement

`bash tests/run.sh --layout ci` passed in **132.61 seconds** with cached image layers on the same development machine: nine product logic checks, nine harness checks, eight required packet tests, four shared movie cases, the real VPN fault/reconnect check and one configured-clone case. Both containers had empty unexpected-request lists, including final DNS/HTTP verification.

| Section | Seconds |
| --- | ---: |
| Product/test image builds | 4.70 |
| Logic, runtime syntax and shell/UI lint | 1.72 |
| Required isolated packets | 12.05 |
| Shared application startup | 22.95 |
| PG / R / unknown / late-certification cases | 9.09 / 8.96 / 8.91 / 9.92 |
| VPN fault, local access and recovery | 15.23 |
| Cold seed export / restore | 1.60 / 1.65 |
| Clone startup / new request with four prior titles retained | 12.79 / 10.25 |
| Combined shared + clone containers, including lifecycle costs | 113.72 |

This covers more work than the earlier three-film benchmark. Fresh controller health adds observations after each outcome; the late-certification case exercises an actual file move, and CI now pays for one independent configured boot. The timing summary and per-container reports retain exact boundaries; values can overlap or include small unlisted bookkeeping costs and are not additive guarantees.

### Shared invariant checkpoint measurement

The same five-scenario CI layout passed in **123.71 seconds**, versus **132.61 seconds** above: **8.91 seconds (6.7%) saved** overall. Its integration containers took **104.50 seconds**, versus **113.72 seconds**: **9.22 seconds (8.1%) saved**. Both runs used cached layers on the same development machine; these are individual observations, not a statistical CI guarantee.

| Section | Previous seconds | Shared-checkpoint seconds |
| --- | ---: | ---: |
| Complete suite | 132.61 | 123.71 |
| Integration containers, including seed/lifecycle work | 113.72 | 104.50 |
| PG / R / unknown scenarios | 9.09 / 8.96 / 8.91 | 5.01 / 5.90 / 6.08 |
| Late-certification scenario, with controller restart | 9.92 | 10.98 |
| Shared container / configured-clone container | 82.77 / 30.94 | 73.75 / 30.75 |

Ordinary invariant checkpoints took 0.07–0.13 seconds. The restart checkpoint retained its fresh-job wait (1.54 seconds). The clone’s final cleanup barrier took 4.12 seconds; all five cases ended with `stopSharingVerified: true`, and neither container recorded an unexpected invariant event. Full checksum sweeps and the real VPN fault/recovery still ran.

Consolidation alone did not measurably accelerate the suite: the first experimental integration batch took 115.30 seconds because native cleanup overlapped the old health waits. Explicitly triggering another native completion scan was inconsistent and was removed. The retained improvement overlaps pending cleanup with later scenarios and enforces one final barrier. The full run passed 11 product logic checks, 12 harness checks and 8 packet checks; a subsequent deadline regression brings the harness count to 13 and verifies that later cases cannot reset an earlier owner’s cleanup budget. The final built-runtime worker passed all 11 product and 13 harness checks; a focused fresh-install/barrier/VPN run passed in 52.10 seconds after placing final file and command checks after the cleanup barrier.

## 0.5.0 fault and safety coverage

`executable-payload` advertises an ordinary acceptable release whose only member is inert fixture data named `.mkv.EXE`. Real Seerr, Radarr and Prowlarr select it; the shipped qBittorrent filter assigns zero priority before transfer. After publishing the fixture bytes and observing successful maintenance, the test requires zero peer blocks, zero downloaded/completed bytes, no executable materialized in media and no import. This proves native filename exclusion, not antivirus scanning or whole-torrent rejection before selection. Mixed torrents can have pieces spanning wanted and excluded files; zero-byte exclusion is asserted for the executable-only case.

`storage-recovery` establishes an at-rest library and finished cleanup, then temporarily overlays a read-only mount inside the isolated test container. The real guard must stop qBittorrent, Radarr and Sonarr, retain local request/setup access, leave the replacement empty, and recover after unmount while prior file checksums survive. The cumulative recorder permits only explicitly named job failures during this declared fault window; it retains those events with their expected-fault reason. Native command failures remain failures. The test does not reproduce SMB server semantics, promise atomic prevention during the fifteen-second detection interval, or prove cleanup interrupted midway through native torrent removal.

`cold-start-offline` disables the external fixture VPN before the first `/init`. Real local applications and published interfaces must work while internet traffic and DNS remain blocked. Only then is the fixture restored. Subsequent planned restarts start with the VPN available, and the initial cold-start evidence remains in the cumulative report.

## Adding scenarios

Keep outcome assertions in scenario modules such as [`movie_requests.py`](../tests/journey/movie_requests.py). The small [`journey.py`](../tests/journey/journey.py) driver owns startup, per-scenario budgets and final verification. New scenarios should accept the application stack, a namespaced catalogue case and background state rather than create new global clients or bootstrap another stack.

Shared operations live in:

| Utility | Responsibility |
| --- | --- |
| [`applications.py`](../tests/journey/applications.py) | Real Arr/Sonarr/Prowlarr/Seerr/qBittorrent clients, first-run setup, native command completion, engine identities and background preservation |
| [`invariants.py`](../tests/journey/invariants.py) | Shared health/background/cleanup checkpoint, cumulative source evidence and owner-attributed native command failures |
| [`support.py`](../tests/journey/support.py) | Cookie-aware JSON/form requests, bounded polling, independent deadlines, atomic evidence checkpoints and streaming checksums |
| [`torrent_files.py`](../tests/journey/torrent_files.py) | Exact block reads spanning fixture files without storing every film in memory |
| [`container_runtime.py`](../tests/container_runtime.py) | Host CLI execution, named worker cleanup, immutable image IDs and cold seed transfers |

Use `client.wait` for eventual observations and `wait_command` for real native commands. Expected network startup/502–504 failures can be observed again; permanent API errors, malformed responses, failed commands and assertion failures surface immediately. Startup gets 180 seconds, each scenario gets an independent 120 seconds, and host deadlines include those budgets plus bounded diagnostics/shutdown time. Waiting never borrows a later scenario's budget, and nested HTTP requests respect the current wait's remaining time.

Checkpoints update `result.json` and each case report throughout execution, with explicit running/passed/failed states. Failed runs retain the original exception, last completed checkpoint, partial timing summary and read-only native movie/queue/command/history/blocklist/source/torrent/request snapshots and real log tails. Categories distinguish fixture contracts, timeout stages, native command failure, HTTP failure, assertion and background ownership/field regressions. Diagnostic collection has a separate ten-second HTTP budget and cannot replace the original failure. Complete logs remain artifacts; successful console output stays compact. Check workers and test containers both have named, bounded cleanup, including after timeout.

The shared invariant checkpoint verifies all owned titles, including the just-completed scenario, once per scenario. It also checks all owned torrent/queue/staging cleanup and the native command history for earlier titles, so late failures remain attributable. Configured clones get an initial baseline check; batch completion and VPN recovery retain full checksum sweeps. Background verification batches native API reads and caches file checksums only while device/inode/size/mtime/ctime remain unchanged. Every batch still ends with a full checksum pass. Global setting changes must be explicit and either restored or deliberately carried forward as new expected state; changes intentionally made to an earlier owned object must also update that object's expected evidence. The first twelve numeric cases map to named integration scenarios; additional identities are movie controls. Use `--only NAME ...` with the shared or CI layout for focused validation. The CI layout still adds its configured-clone check after the selected shared cohort succeeds.

The fixture serves UDP/TCP DNS with explicit A/AAAA/negative replay and records queries; application hostnames are not inserted into `/etc/hosts`. Final evidence requires real Radarr/TMDB lookups over WireGuard. Known unavailable telemetry domains return NXDOMAIN; other unknown DNS/HTTP requests fail the contract.

The external world rejects fixture paths on the wrong hostname and has no generic successful metadata fallback. Small status polls omit the growing request transcript; the full timestamped transcript is written durably as events occur and collected at exit, including after a broken tunnel. Unknown requests are asserted after final verification too. Synthetic source files store the zero-filled Matroska Void as sparse holes, and the peer reads blocks from files. Logical lengths, piece hashes and all bytes transferred through real qBittorrent remain unchanged. New fixture responses should model an observed external contract rather than broaden a catch-all response.

Focused harness regressions cover permanent/transient failures, nested deadlines, durable partial reports, mandatory final checksums, cross-file/empty-file block boundaries, truncated fixtures, incorrect hosts, DNS replay, cumulative invariant rejection and late native-command ownership. A scheduler regression also verifies that recurring jobs cannot starve other due work. Before these review fixes, the reviewed default suite passed in 61.11 seconds with cached layers, including eight product logic tests, eight harness tests, eight packet tests and three real scenarios. A configured-clone run also passed. An intentional real-Radarr 404 failed about 65 ms after the request checkpoint and retained the partial case and native failure snapshots.

## Cumulative invariant evidence

Acceptance sets `POMPEY_EVENT_LOG` to an append-only journal in the container’s temporary directory. The shipped code records controller failures before resetting retry counters, controller starts, and policy attention immediately after relevant durable state writes. Normal installations leave this opt-in trace disabled. File locking prevents concurrent producer/read races; controller restarts retain earlier events in the same container. The harness requires the controller-start event, so a disconnected producer cannot silently pass.

`invariant-events.json` preserves setup and runtime phases. Assertions arm when initialized setup becomes healthy: pre-setup configuration failures remain visible but expected; subsequent controller failures and notices fail even after successful retry or notice removal. Time-dependent notices are also recorded when the normal health sampler observes them. This is not a claim to observe every sub-second service outage or a notice condition that was never persisted or sampled.

At each checkpoint, retained native Arr command history is checked against every owned title and its ownership start time. Earlier cases’ late failures are attributed to their owner. Observed failures are retained even if later native history omits them. Commands purged before any observation remain outside this history-based check; application binaries are not instrumented. Scheduled telemetry/update failures without owned title targets are excluded. Future scenarios intentionally generating policy notices need explicit expectations rather than a blanket exemption.

The harness retains fresh-job waits where lifecycle changes justify them. Ordinary variants use immediate health checks; the actual controller job bodies, production retry policy and health cadence are unchanged. Cleanup is eventual: checkpoints record completed transfers still awaiting native cleanup with a per-title 60-second deadline, reject transfer regressions, and enforce torrent/queue/staging cleanup at the batch barrier. Later scenarios overlap legitimate cleanup from earlier ones. No cleanup outcome is skipped; final success requires the same cleanup guarantees for every owned title. Per-checkpoint durations, pending owners and the final barrier duration make those costs visible.

## Scope and expansion

Focused tests remain for paths/symlinks, request ownership/history discontinuities, sharing boundaries and WireGuard parsing. The previous disconnected real-engine suites, fake application HTTP servers, anime catalogue matrix, fake-VPN helper and artifact/runtime download harnesses are removed.

The first expansion covers TV episodes, season packs, selected seasons, genuine 480p through Anything, anime pack/dual-audio preference, original-language decisions, waiting-request RSS fulfilment, request deletion, same-container restart and VPN changes. [The numbered catalogue](integration-scenarios.md) distinguishes implemented assertions from stretch goals. Pending-request decline, non-admin permissions, other sharing policies/hardlinks, Max, direct grabs, history-reset safeguards, general source/metadata outages, failure isolation and NAT-PMP remain stretch coverage. Release acceptance additionally covers cold startup without a tunnel, storage loss/restoration and a selected forward upgrade. Broader coverage and passing acceptance are required before unattended dependency merge/release is enabled.

Required automated acceptance complements the manual HAOS release gate. Dependency publication requires approval through the protected `haos-release` environment; it refuses to publish if reviewer protection is absent. Actual Supervisor installation, Ingress forwarding on HAOS, NAS permission/hardlink behavior and Plex discovery/playback remain explicit unautomated boundaries. A local Docker/Podman run does not reproduce them. Recovery relies on HA cold backups; automatic binary/database rollback and old-layout migration are not product guarantees.

## PR completion

Validate locally, push and take at most one non-waiting CI snapshot. Report passed, failed and pending checks accurately, then end the turn. Do not poll or wait for CI unless the user explicitly asks. `CI required` remains the stable aggregate branch-protection check. Candidate discovery is separate from acceptance and promotion.

## Network fault check

In the dedicated VPN scenarios, the root application identity probes a fixture listener and DNS on the veth underlay; both must be blocked by the shipped firewall. The live nftables output chain must have drop policy. The remote WireGuard link is then taken down and the actual client peer removed to accelerate native fault detection. Status must report VPN unavailable while local Seerr, Prowlarr, Arr and downloader APIs remain reachable. DNS and underlay access must stay blocked. The production WireGuard service reconnects after restoration; fresh controller health, DNS, a real metadata refresh and prior media are verified again.

This checks the native reconnect loop, not the production 180-second stale-handshake detection delay of initial startup without VPN; the separate cold-start scenario covers that boundary. It never edits readiness markers or mocks the VPN service. The fault has a nested 90-second budget bounded by its scenario deadline. The download-loss scenario additionally requires the original incomplete torrent to finish after recovery, without a forced resume or reannounce. Product firewall rules are observed through actual packets rather than compared with their own generator.


## First integration coverage expansion

The synthetic TV catalogue provides valid Skyhook/TMDB responses and real one-minute episode containers. Packs contain two distinct episode files and subtitles. Anime releases include actual Japanese/English audio tracks; foreign-only alternatives have matching tagged tracks and complete downloadable payloads. Native Sonarr scores, chosen release, imported episode coordinates, audio stream counts, routing and exact bytes are asserted. Default excludes the real 480p quality; Anything imports it with native 854×480 media information. Production quality profiles remain intact.

One waiting movie and one partial download span a declared clean stop/start of the same container, while completed movie/TV media remain background state. The original request IDs and partial bytes must survive. After restart, the test publishes the missing release and invokes native RSS sync. Deleting requests uses Seerr's API, then observes Pompey stopping monitoring while preserving partial downloads and completed movie/TV files. Seerr cannot decline an already approved request (HTTP 409); a future decline test must create a genuinely pending request rather than edit its database.

Rotation submits a new WireGuard key and endpoint through setup, requires native reconnection and real engine metadata queries, and probes the firewall for underlay leaks. In-flight download resumption is the separate `vpn-download-loss` scenario. Assertions remain active when they reveal a product bug; focused passing subsets do not establish full acceptance.

### Expansion measurement (2026-10-26)

The focused eleven-scenario shared cohort plus one configured-clone control passed in **265.70 seconds**, including cached builds, 11 product logic checks, 13 harness checks and 8 required isolated packet tests. The command was:

```sh
bash tests/run.sh --layout ci --only movie-pg movie-r anything-480p late-certification waiting-content single-episode season-pack selected-seasons anime-dual-audio restart-partial vpn-rotation
```

| Section | Seconds |
| --- | ---: |
| Shared cohort, including same-container restart, seed export and lifecycle | 212.96 |
| Initial application setup | 23.97 |
| PG / R / Anything 480p | 7.22 / 6.51 / 6.16 |
| Late-certification move | 11.41 |
| Single episode / season pack / selected seasons | 7.65 / 5.96 / 5.82 |
| Anime dual-audio pack | 24.33 |
| Waiting request / partial-download restart, elapsed across handoff | 84.87 / 78.10 |
| VPN rotation | 15.51 |
| Configured clone, including lifecycle and restore | 32.56 |
| Clone application setup / new movie | 13.88 / 5.91 |
| Combined integration batch | 245.52 |

Waiting and partial-restart durations include elapsed work by other cases and the clean stop/start, so those rows overlap and must not be summed. The native downloader's reconnect timing remains visible rather than being hidden by a forced resume. Ordinary checkpoints remained under 0.3 seconds; the clone's final cleanup barrier took 4.34 seconds. Full final sweeps verified 13 movie/episode files in the shared application stack and 14 in the clone, with subtitles and native ownership links. Both external unknown-request lists and cumulative unexpected invariant lists were empty.

This focused command deliberately omits the independently diagnosed `vpn-download-loss` assertion. It is not a passing full acceptance claim; default shared and CI layouts retain that scenario without skipping or quarantining it. The old five-scenario benchmark above has less coverage and is not a direct speed comparison. GitHub runner timings and HAOS behavior remain unmeasured.


### Historical 0.4.0 failing assertion

`bash tests/run.sh --only vpn-download-loss` reproduced a failure after VPN loss during an incomplete transfer. Native tunnel/DNS recovery took 11.81 seconds and all six service health flags returned true. qBittorrent still reported `stalledDL`, 99.815% complete, one seed and zero download speed when the normal 60-second completion wait expired. The torrent retained 16,777,216 of 16,808,281 payload bytes. External unknown requests and cumulative invariant failures were empty. This is an unresolved download-resumption failure under the test budget, not an established root-cause diagnosis or proof that it never recovers. Investigate native reconnection/request timing and the peer interaction before changing production behavior or tuning a test timeout.

Failure evidence includes native torrent properties/trackers, application logs, and controlled-peer state. The assertion remains in default acceptance. It is not marked expected-failure, retried, bypassed by a manual resume/reannounce, or treated as passing because health recovered. Broader unattended release acceptance therefore remains blocked.

A second focused reproduction with peer snapshots failed at the same byte boundary. The fixture recorded the final-piece release and handed all 16,808,281 payload bytes to its TCP socket; qBittorrent remained at 16,777,216 bytes. A successful socket write is not evidence of client receipt. The saved peer state rules out an accidentally unreleased fixture gate, while leaving connection/timeout behavior for diagnosis. No test workaround or production fix is included in this expansion.


## Unwanted release-file cleanup

The baseline `movie-pg` torrent includes three additional namespaced files: release notes (`.nfo`), a README (`.txt`) and an internet shortcut (`.url`). The test reads qBittorrent's real file list before completion and requires all three to be included with nonzero download priority. Full native torrent completion and the existing peer-byte checks then cover the complete payload, rather than a fixture containing only wanted files or a client configured to skip extras.

The shared cleanup checkpoint requires those names to disappear everywhere below the existing media root, including download storage, recycle storage and the Plex library. The baseline library folder must contain exactly the expected movie and subtitle, so renaming unwanted files cannot evade the check. It uses the existing per-title cleanup deadline and final barrier. Exact movie/subtitle checksums and ownership links must still survive; later scenarios and the configured clone keep checking the same ledger, so reappearing leftovers fail too. No deletion is performed by the test. This covers extras from a successfully imported, owned download under **Stop sharing**; active seeding, unknown files and ambiguous/manual downloads retain their separate safety boundaries.

Focused validation: `bash tests/run.sh --layout ci --only movie-pg` runs the real fresh-install download and configured-clone preservation check, together with the required logic and packet checks. No extra installation or standalone scenario is added to the default suite.

This focused CI-layout run passed locally in **92.75 seconds** (73.54 seconds integration), including 11 product logic, 13 harness and 8 required packet checks. Both the fresh-install and configured-clone scenarios passed with empty pending-cleanup, unexpected-request and unexpected-invariant lists. The baseline reported `unwantedFilesPurged: true`; its retained movie/subtitle and absence of extras were verified again after the clone imported a second movie. This does not resolve or revalidate the separate VPN download-resumption failure described above.


## Historical 0.4.0 release acceptance

The following describes the 0.4.0 policy only; 0.5.0 removes the exception and requires strict resume success. The VPN download-resumption issue was accepted as nonblocking for the manual 0.4.0 release. PR/main acceptance runs `--layout ci --exclude vpn-download-loss`, preserving every other default scenario, the configured clone, logic checks and required packet tests. The same job then runs the unchanged strict VPN-resume scenario through a narrow known-issue classifier with its failure evidence retained. Only the download-completion timeout is accepted, and only with successful outage/firewall recovery, currently healthy services/jobs, clean shutdown, no unknown fixture requests and no unexpected cumulative controller events. Every other VPN-scenario failure remains fatal to required CI. `required-timing-summary.json` preserves the required cohort's result separately from that advisory run. The stable `CI required` aggregate remains required. Default local runs and candidate evaluation still include the strict assertion; unattended promotion remains disabled. Earlier descriptions of this failure blocking release refer to the policy before this explicit release decision.


## Published interfaces and same-host Plex

The fixture adds an ordinary default route whose gateway is confined to its isolated namespace. A client at a different routed address opens real Seerr and Prowlarr HTTP endpoints with `lan_networks` empty, while direct access to Ingress must still receive 403. The baseline configures real Seerr to use the fixture Plex server at the container gateway and queries its library endpoint; this proves automatically detected container-network access without a configured internal HAOS IP. Cold clones and VPN outage checks retain published-interface assertions.

Packet regressions reproduce TCP replies being lost to a more-specific tunnel route, then verify the production return-route policy for IPv4 and IPv6, all published ports and repeated installation. New outbound connections using a published source port must remain blocked. Automatic connected-network detection separately permits a neighboring service while blocking application DNS. Internet-leak probes use a non-connected fixture address with a temporary test-only underlay route, since directly connected private container services are intentionally permitted. All routes and listeners remain inside the verified isolated namespaces.

## 0.5.0 development acceptance measurement

`bash tests/run.sh --layout ci --suite release --upgrade-from localhost/pompey:released-0.4.2` passed locally in **448.07 seconds** with cached image layers. It included required isolated packets, all fifteen shared scenarios, one configured-clone control and a separate released-image → candidate handoff. The actual released 0.4.2 source image seeded completed media, indexer configuration, a waiting request and a partial transfer. The candidate preserved those identities/bytes and fulfilled both pending requests. No timeout exception or forced test resume was used.

| Section | Seconds |
| --- | ---: |
| Builds, syntax/logic and required packets | 32.44 |
| Shared cohort including clean restart, storage and VPN faults | 286.82 |
| Configured-clone control including restore/lifecycle | 34.73 |
| Released-image seed including snapshot | 50.85 |
| Candidate upgrade boot and completion including restore/lifecycle | 42.57 |

Stage durations overlap for requests carried across a restart. The initial upgrade timing display incorrectly subtracted both boots' work from one candidate container and printed a negative lifecycle overhead; its wall duration and product assertions were valid. The reporter now keeps current-boot startup/elapsed measurements separately from cumulative request durations. Later focused validation covers that correction and the stale-data storage assertion. These are individual development-machine timings, not GitHub runner or HAOS performance guarantees. Actual Supervisor/SMB installation remains a manual release boundary.

The follow-up movie/episode/executable/storage cohort plus clone and released-image upgrade passed in **288.54 seconds**. The storage assertion verified that stopped Arr applications retained last known monitored counts with unavailable flags and timestamps. The upgraded candidate reported **14.93 seconds** startup and **6.22 seconds** lifecycle overhead, separated correctly from earlier request work. This run was broader than the normal PR cohort because it specifically revalidated storage and upgrade reporting.

The final standard PR cohort, `bash tests/run.sh --layout ci --suite pr`, passed in **115.79 seconds**: **86.25 seconds** for fresh movie/episode/executable cases plus the configured clone, with **29.54 seconds** for cached builds, logic/runtime checks and required packets. The executable case took **5.30 seconds** in the shared stack. This final run includes structured-diagnostic/log credential filtering and the latest product source; no further runtime changes followed it.
