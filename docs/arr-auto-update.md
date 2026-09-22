# Runtime ownership and engine releases

Supervisor builds one thin container from `pompey/Dockerfile`. s6 supervises its processes. No nested Docker daemon is involved; Arr’s `UpdateMechanism=Docker` disables self-replacement so Pompey has one update owner.

## Boundaries

| Owner | Responsibility |
| --- | --- |
| `vpn_config.py`, `vpn_firewall.py`, `vpn_routes.py` | Validate provider-neutral configuration, generate the atomic inet firewall, and install tunnel/endpoint/LAN routes |
| WireGuard s6 service | Apply configuration, establish the tunnel, detect lost handshake or changed configuration |
| `engine_manager.py` | Verified artifact staging, supervised service stop/start, transaction journal and database rollback |
| `pompey_controller.py` | Independent scheduled jobs, deadlines, bounded exponential backoff, live health |
| `wire_stack.py` | Reconcile required localhost connections and configuration |
| `media_policy.py` | Respect sharing goals; conservatively submit matched manual grabs |
| `request_policy.py` | Complete stable request snapshots and previously observed ownership |
| `route_rating.py` | Ask Arr to move titles from `downloads/By Rating` into the appropriate library |
| Arr / Prowlarr / Recyclarr | Ordinary imports and upgrades / native source synchronization / quality configuration |

Python sources have `.py` extensions and can be imported normally. Shell scripts are small environment and process entrypoints. There is no global Torznab rewrite proxy. Source-specific capability problems should be fixed at the source adapter, not by deleting identifiers from every search.

The controller serializes mutating policy jobs with `/data/pompey/stack.lock`. Update downloads occur outside that lock; the service-stop/snapshot/swap/validation transaction holds it. Controller jobs and update validation share process-group cleanup, so a timed-out validator cannot leave Recyclarr running during rollback. Health probes continue independently of configuration retries.

## Promoting an engine bundle

1. Run `python3 tools/build_engine_manifest.py /tmp/candidate.json`. This resolves official releases, digests and resource commits; it does not modify the deployed bundle.
2. Run candidate integration tests with `POMPEY_ENGINE_MANIFEST=/tmp/candidate.json`. Both artifact and real API tests must pass. The scheduled CI candidate job exercises this without promoting it.
3. Review version/API changes, copy the candidate into `pompey/rootfs/usr/share/pompey/engines.json`, and bump the add-on release.
4. Run the pinned CI suite and production-layout smoke test, then the manual HAOS checklist. Ship through the normal Supervisor local build.

Caches are keyed by artifact identity and target platform, not only engine name. A cached file from another bundle cannot satisfy the candidate contract. The Home Assistant base image is also pinned by a multi-architecture digest; update it deliberately and rerun the built-runtime smoke. The manifest pins glibc and musl artifacts for amd64 and aarch64, a Seerr image digest, the musl Recyclarr .NET runtime, and TRaSH/config-template Git commits.

## Transaction and recovery

Only changed artifacts are staged. Every downloaded native artifact has its upstream SHA256/SHA512 checked before extraction. Extraction rejects traversal, unsafe links and special files. Seerr exports its pinned image and extracts only its application and Node executable for the add-on’s musl runtime.

The journal records the old identities and manifest before services stop. After all writers stop, Pompey copies the managed configuration trees (including SQLite sidecars), copies old binaries, records the installing phase, and replaces the staged directories. It generates startup configuration while services are stopped, starts all managed services, checks their APIs and validates wiring before committing new identities.

A failed stop or backup never swaps binaries. A failed install, database migration or validation restores the snapshot and restarts the old services. Recovery releases the startup readiness barrier for restored configurations even after a container restart; a failed fresh install leaves it closed. Recovery keeps backups until restoration finishes, so another interruption can retry. Committed journals only need cleanup. Other `/config` content, including the VPN private key, is outside the app-database rollback.

Normal restarts regenerate configuration before releasing the readiness barrier. A failed candidate download may fall back to a complete installed bundle; an incomplete first installation remains unready. Disk exhaustion is an update failure, not a reason to delete the active bundle.

Legacy installations with updater stamps and all required launchers can also use the download-failure fallback, without claiming that the new bundle was installed. Before replacement, Pompey rejects known downgrades using the active manifest, legacy Arr release stamps, or Seerr's package version. Unavailable version metadata does not block installation. This is a small release-version check, not a database compatibility or migration framework. Keep a Home Assistant backup and verify the upgrade manually on the household installation.

## Conservative file handling

Ordinary Arr-category files are never scanned or manually imported by Pompey. Arr decides whether a release is an upgrade and owns replacement and cleanup. Manual Prowlarr grabs use durable receipts in `/data/pompey/state/manual-imports.json`: intent is saved before sending a command, so an ambiguous timeout does not cause a duplicate import. Inspect the Arr command and retained files before clearing a receipt for a deliberate retry.

Request closure requires two complete matching paginated Seerr snapshots. Only stored ownership with the same external title identity permits an unmonitor operation. It never deletes media or resubmits declined requests.

Recyclarr runs with log output and a sized input terminal: its progress renderer otherwise crashes with a zero terminal height under headless supervision, even in log mode. The real integration test exercises this launch path. A zero exit code accompanied by Recyclarr errors is rejected.
