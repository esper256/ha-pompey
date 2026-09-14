# Release confidence

## Automated layers

`bash tests/run.sh` runs the fast suite: configuration validation, HTTP wiring contracts, file-preservation and sharing policy, complete request pagination, update failure/SQLite rollback/crash recovery, actual shell boundaries, VPN rendering, isolated IPv4/IPv6 packet tests, and the wait-screen preview. Packet and live handshake tests skip explicitly when the host lacks the required namespace/kernel capability. CI sets `POMPEY_REQUIRE_NETWORK_TESTS=1` so these cannot silently skip there.

On Linux install Python 3.12+, PyYAML, jq, shellcheck, nftables, iproute2, iputils-ping and wireguard-tools. Use passwordless sudo for isolated namespaces. Never install a kill switch in the host namespace. No test launches a torrent client or contacts peers; HTTP downloader fixtures are permitted.

```sh
POMPEY_REAL_ENGINES=1 python3 tests/test_engine_artifacts.py -v
POMPEY_REAL_ENGINES=1 python3 tests/test_arr_integration.py -v
python3 tests/test_seerr_real.py -v
```

Real-engine tests download manifest-pinned artifacts into digest-keyed caches. Arr tests require ffmpeg and the native ICU libraries; they use synthetic audio/video and a fake downloader WebAPI, with public metadata lookup only. They assert incomplete files stay out of the library, completed imports and upgrades are performed by Arr, multi-episode TV files and subtitles import through Sonarr, and the real Recyclarr configuration creates usable profiles. Both glibc and musl artifact layouts are checked. Seerr’s initialization contract uses its official musl image under chroot and requires sudo.

`tests/smoke_runtime.py` runs **inside the built add-on image**, using the production extraction code, app layout and Node executable. CI checks Arr API startup, Seerr’s native sqlite load and the Recyclarr/.NET runtime without booting Supervisor or a download engine. The existing builder separately compiles supported architectures. Weekly candidate tests resolve upstream versions into a separate manifest; they never replace the checked-in release bundle.

Fast HTTP fakes make faults deterministic. Real API tests catch upstream shape and lifecycle differences. Neither proves NAS semantics, actual VPN behavior, Plex discovery or HAOS startup; those are the manual gate below.

Focused regression cases cover restart recovery with a missing readiness marker, validation descendants stopped before rollback, repair of Prowlarr destinations/credentials/sync mode, legacy download fallback, and known-version downgrade rejection. Version checks use metadata fixtures; there is no historical database upgrade test matrix.

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
