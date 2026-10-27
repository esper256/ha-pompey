# Bundled applications and ownership

Supervisor builds one immutable amd64 image from `pompey/Dockerfile`. `pompey/vendor_bundle.py` runs only during that build: it verifies artifact checksums, extracts safe archive members, exports the digest-pinned Seerr image and checks out the pinned Recyclarr resource commits. Runtime contains neither this installer nor its download tools. `/opt/pompey` is root owned.

`write_engine_configs.py` writes managed settings before s6 starts applications. Engine binaries and controller jobs run as container root for HAOS SMB compatibility. Arr self-updates are disabled; updates arrive through the add-on image. `recyclarr_sync.py` reads bundled local resources without GitHub access and configures real Default, Max and Anime profiles. A durable image-identity record is accepted only when the profiles still exist in both Arr services.

The controller has bounded, serialized configuration, download, request and routing jobs. Health probes continue independently. Startup uses the real cont-init-before-services ordering rather than engine-download barriers. Configuration failure clears current prerequisite markers and remains visible; initialized Seerr connections and source synchronization are required.

## Conservative files and requests

Arr owns ordinary imports, upgrades and library moves. Prowlarr direct grabs retain a separate category. Pompey submits only Arr-accepted manual imports and records intent before commands. Unknown or ambiguous files and commands remain reviewable. One bad receipt must not authorize duplicate submission or deletion. Downloads are not assumed to originate from Seerr.

Request closeout uses complete stable snapshots and observed ownership. Unknown seasons are preserved. Search identity changes and large history disappearances pause cancellation until an administrator explicitly resets the baseline while keeping current monitoring. Files and in-flight transfers are retained.

## Updates and recovery

`tools/build_engine_manifest.py` resolves stable releases and resource commits into a candidate manifest. Candidates are built and exercised with the same real-application test. Discovery never changes a running installation. Integration scenarios cover fresh installation, movie and TV imports, request closeout, restart and network behavior. The daily dependency workflow resolves one component at a time and creates or updates its own bot PR. Unchanged failed candidates are not repeatedly retried. Each dispatch runs release acceptance and the selected previous-release forward upgrade. Promotion verifies bot ownership, exact accepted SHA, a current base, tightly restricted metadata changes, trusted artifact sources, strict required `CI required` protection and the manually reviewed `haos-release` environment. It checks the merged tree matches the accepted tree before tagging and publishing a source release. It never publishes container images or updates a running HA installation.

Activation is separate from implementation: repository settings must permit Actions-created PRs, main must enforce the aggregate check, and `haos-release` must have required reviewers who validate the real HAOS installation. Default workflow tokens remain read-only, with job-specific write permissions; no workflow approves a PR review. Discovery is intentionally inactive on prerelease Pompey versions. `renovate.json` proposes digest-pinned Docker/Actions maintenance with manual merge, but requires Renovate installation/configuration and is not itself an active bot. No candidate publication cycle has been exercised by the local test suite.

Home Assistant cold backups provide recovery. Pompey deliberately has no runtime package manager, old binary copies, transaction journal, database rollback, downgrade compatibility or legacy-layout migration. Restoring a backup restores the locally built image and app state through HA; ordinary image updates may migrate upstream databases. This foundation does not prove all historical-database upgrades.

## Dashboard observation definitions

All observations are read-only. The local setup server shares one five-source cache between the overview and detailed diagnostics, refreshing at most every fifteen seconds for routine overview reads and every two seconds for explicit diagnostic reads. Independent application sources are read concurrently; each HTTP read has a two-second socket timeout and a bounded response size. Failed sources retain their last successful value and timestamp with `available: false`. A failed whole source does not fabricate a zero. These are observation timestamps, not a promise that upstream internal state is current.

| Headline | Native source and definition |
| --- | --- |
| Torrents | qBittorrent `/torrents/info`; disjoint downloading, seeding, stalled, paused/stopped, checking/moving and other buckets. `stalledUP` is seeding; `stalledDL` is stalled, not downloading. |
| Monitored | Radarr movie and Sonarr series collections with `monitored: true`; counts titles, not episodes, imported files or requests. |
| Indexers | Enabled Prowlarr sources by unique source ID; available excludes sources whose native `disabledTill` is in the future. Disabled sources are excluded from the denominator. This reflects native backoff, not a fresh live probe of every source. |
| Requests | Seerr `/request/count`; open is pending approval + approved-and-processing + failed. Failed is the remaining native status partition after pending, approved, declined and completed. Available approved requests and completed requests are not open. Partial series availability stays in processing. |

Detailed diagnostics inspect the most recent 100 requests and first 100 native queue records per Arr application, explicitly noting truncation. Seerr→Arr correlation uses movie TMDB or series TVDB identity and exact native title IDs; queue→torrent correlation uses download hashes. Partial TV files do not establish request fulfilment. Library import does not prove media-server discovery or playback. Request/root causes remain unavailable when their required sources fail. Explicit release searches remain bounded and rate-limited per title; opening the dashboard never initiates a release search.

GitHub API references for automation trust boundaries: [branch data](https://docs.github.com/en/rest/branches/branches#get-a-branch), [protection APIs](https://docs.github.com/en/rest/branches/branch-protection#get-status-checks-protection), [release environments](https://docs.github.com/en/rest/deployments/environments#get-an-environment). The verifier reads the branch rule through GraphQL to avoid an Administration-scoped token. Missing protection or unavailable API evidence refuses promotion.
