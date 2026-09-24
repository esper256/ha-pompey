# Release confidence

## Automated layers

`bash tests/run.sh` runs the fast suite: configuration validation, HTTP wiring contracts, file-preservation and sharing policy, complete request pagination, update failure/SQLite rollback/crash recovery, actual shell boundaries, VPN rendering, isolated IPv4/IPv6 packet tests, and the wait-screen preview. Packet and live handshake tests skip explicitly when the host lacks the required namespace/kernel capability. CI sets `POMPEY_REQUIRE_NETWORK_TESTS=1` so these cannot silently skip there.

On Linux install Python 3.12+, PyYAML, jq, shellcheck, nftables, iproute2, iputils-ping and wireguard-tools. Use passwordless sudo for isolated namespaces. Never install a kill switch in the host namespace. Only the dedicated peerless qBittorrent harness may launch a torrent client, after verifying a fresh loopback-only network namespace. No test contacts peers; all other downloader tests use HTTP fixtures.

```sh
POMPEY_REAL_ENGINES=1 python3 tests/test_engine_artifacts.py -v
POMPEY_REAL_ENGINES=1 python3 tests/test_arr_integration.py -v
python3 tests/test_seerr_real.py -v
```

Real-engine tests download manifest-pinned artifacts into digest-keyed caches. Arr tests require ffmpeg and the native ICU libraries; they use synthetic audio/video and a fake downloader WebAPI, with public metadata lookup only. They assert incomplete files stay out of the library, completed imports and upgrades are performed by Arr, multi-episode TV files and subtitles import through Sonarr, and the real Recyclarr configuration creates usable profiles. Both glibc and musl artifact layouts are checked. Seerr’s initialization contract uses its official musl image under chroot and requires sudo.

`tests/smoke_runtime.py` runs **inside the built add-on image**, using the production extraction code, app layout and Node executable. CI checks Arr API startup, Seerr’s native sqlite load and the Recyclarr/.NET runtime without booting Supervisor or a download engine. The existing builder separately compiles supported architectures. Weekly candidate tests resolve upstream versions into a separate manifest; they never replace the checked-in release bundle.

Fast HTTP fakes make faults deterministic. Real API tests catch upstream shape and lifecycle differences. Neither proves NAS semantics, actual VPN behavior, Plex discovery or HAOS startup; those are the manual gate below.

Focused regression cases cover restart recovery with a missing readiness marker, validation descendants stopped before rollback, repair of Prowlarr destinations/credentials/sync mode, legacy download fallback, and known-version downgrade rejection. Version checks use metadata fixtures; there is no historical database upgrade test matrix.

## Anime season acceptance

```sh
python3 tests/test_anime_fixtures.py -v
POMPEY_REAL_ENGINES=1 python3 tests/test_anime_integration.py -v
# Turn the documented acceptance gaps into hard failures:
POMPEY_REAL_ENGINES=1 POMPEY_ANIME_STRICT=1 python3 tests/test_anime_integration.py -v
```

The anime suite uses the pinned Sonarr binary, Pompey's actual Recyclarr Default profile, media-management settings and downloader configuration. Two local Torznab mirrors serve the captured World Trigger catalogue; a fake qBittorrent HTTP API records actual automatic grabs. Prowlarr source scraping and torrent peer traffic are outside this test. Public TVDB/SkyHook metadata and pinned profile downloads require internet access. Metadata service failures fail setup rather than silently skipping coverage.

The catalogue contains 203 copied results from seven sources. Titles, sizes, availability, duplicates, multilingual names, manga and ambiguous batches are preserved. Controlled scenarios use the captured 4.9 GiB x265 dual-audio WEBRip and authentic episode rows, with a matched synthetic single-audio pack. They deliberately vary seeds to isolate availability and audio preference. Episode-query routing is simulated, but Sonarr itself parses, scores, rejects and chooses releases. The complete-catalogue probe keeps every captured row. See `tests/fixtures/anime/README.md` for provenance and limits.

Tests cover a live dual pack versus single audio and episodes, zero-seed dual audio rejection, captured-pack eligibility, complete-catalogue request counts, and native completed-download handling of fourteen generated episodes with subtitles and miscellaneous release files. The latter asserts incomplete files stay out of the library, every episode and subtitle imports, download payloads are removed, and repeated download maintenance issues no indexer requests.

The captured-pack and pack-selection checks are required passes. Ordinary-TV cases retain size, language and quality rejection checks. RSS replays after a fourteen-episode import verify that Default and Max do not replace files just to regain pack scores; Max must still accept a real resolution upgrade. Sonarr groups WEB and Blu-ray 1080p as equivalent, prefers season packs through its native custom format, and uses custom-format scoring for repacks. Its shared 1080p size minimum is 5 MiB/minute; upper limits remain guide-backed. Score-only upgrades stop at a cutoff score of zero.

The aspirational pack-only search budget remains an explicit expected failure in ordinary CI; strict mode fails it. A separate required regression ceiling permits at most 120 catalogue requests per indexer (240 for the two-source fixture), for both interactive and automatic searches. Exceeding that measured baseline fails ordinary CI. Sonarr 4.0.19.2979 made 240 catalogue requests for this season, including 224 episode queries, even when a pack existed. Setup/search errors remain errors. An unexpected pass fails CI so the marker can be removed when upstream behavior improves. The budget is eight catalogue requests per indexer (four ID/name variants with two pages each), including pagination and excluding capability/setup calls.

`POMPEY_ANIME_REPORT` defaults to `/tmp/pompey-anime-report.json`. Reports contain each query, response counts, elapsed time, actual grabs, rejection reasons and custom-format scores; Sonarr logs are copied next to the report. CI uploads these even after failure. Searches retain Sonarr's real throttling, so allow several minutes per scenario. Existing real Arr tests separately cover multi-episode files and upgrades.

## Manual HAOS acceptance

Use a disposable or backed-up installation, a known legal test download, and the household’s actual share. Record the Pompey version and bundle manifest with the result.

1. **Fresh setup:** install through Supervisor, paste a WireGuard file, complete the Plex wizard, configure a source. Confirm Ingress remains available throughout and both movie and TV request connections work after initialization.
2. **Warm restart/offline release fetch:** restart with existing app databases and engines. Confirm configuration is preserved and search returns. An unavailable release endpoint must allow a complete installed bundle to start.
3. **VPN failure and rotation:** interrupt the tunnel, verify public IPv4 and IPv6 cannot leave through the container’s non-VPN interface, and confirm LAN/Ingress access remains. Restore it and paste another valid endpoint; jobs and traffic should recover without reinstalling.
4. **Movie and TV:** request one movie, one episode, a multi-episode/season release, and a higher-quality replacement. Incomplete media must stay out of Plex; finished media and subtitles must land in the intended library. Confirm an existing file is not used as a reason to delete its upgrade.
5. **Sharing/storage:** exercise stop-sharing, ratio 1.0 and one-day policies. Verify below-goal transfers continue seeding. On a non-hardlink share, account for a second copy. After the goal and import, ordinary download entries and payloads should be cleaned by Arr. Replaced files remain recoverable in the recycle folder for seven days.
6. **Manual grabs:** try an accepted Prowlarr grab, a rejected downgrade, an unknown title and extras. Accepted matches import; ambiguous and rejected files remain for review. Inspect the durable receipt before retrying an uncertain command.
7. **Requests and routing:** remove a known request and verify it becomes unmonitored without deleting files. Declined requests remain declined; directly managed Arr titles remain unchanged. Check By Rating, explicit kid/general choices and unknown ratings.
8. **Recovery:** on the disposable installation interrupt a staged update, then restart. Verify the journal restores/reconciles binary and database versions together and services run. Test a deliberately bad candidate health check and confirm rollback. Check free-space behavior with insufficient staging space.
9. **Health and discovery:** stop an engine temporarily, confirm the sidebar reports failure and later recovery, and confirm Plex discovers the imported media. Check Debug off/on and published host ports on the actual Supervisor installation.

A release is accepted only after these household checks pass; a Docker build or green fake suite is insufficient.

## Recovery and cross-service contracts

`tests/test_recovery.py` covers missing/orphaned import commands, per-receipt failure isolation, malformed snapshots, delayed/failed library moves, waiting on the mutation lock, blocked-import notices, and overlapping season requests. Routing fakes deliberately separate request acceptance from file movement.

```sh
POMPEY_REAL_ENGINES=1 python3 tests/test_orchestration.py -v
POMPEY_REAL_ENGINES=1 POMPEY_REAL_QBIT=1 python3 tests/test_qbit_integration.py -v
```

The orchestration suite verifies a real Sonarr move preserves payloads and subtitles, exercises season cancellation with Seerr-shaped paginated HTTP responses, and sends an automatic anime search through real Sonarr and Prowlarr to a local Torznab fixture. The source records every upstream request; the one-source scenario permits at most 120 requests and requires one successful grab. Seerr itself is covered separately by its real API suite.

The downloader suite requires `sudo`, `unshare`, `ip`, ffmpeg and the pinned Sonarr/qBittorrent artifacts. It fetches metadata and artifacts before isolation. Its dedicated worker verifies a different network namespace, loopback as the only interface, and no external routes; it then drops root privileges before starting any engine. It disables discovery and forwarding, uses private trackerless torrent metadata with synthetic local files, and rechecks those files without peers. The suite verifies all sharing configurations across restart, live policy changes, category paths, completion state, real Sonarr imports, subtitle preservation, below-goal retention, and cleanup after stopping sharing. Failure to establish isolation is a failure when this suite is enabled, never permission to run on the host network. It does not test downloading from peers or elapsed real-world seeding goals; deterministic policy tests cover ratio/time boundaries.

Release and candidate CI both require the downloader and orchestration suites. Candidates also run anime selection/import tests and retain their search reports. The existing HAOS/NAS manual gate remains required.
