# Response to the integration-test review

The review assessed commit `0274d2c`. It is useful evidence about the foundation, not a request to implement the entire household suite in this patch. The intentionally small initial feature set and the decision to postpone unattended promotion remain in force.

## Ranked comments

| Review item | Disposition |
| --- | --- |
| 1. Controller/configuration health and ingress | Fixed. Setup and every successful case require fresh successful configuration and all four controller jobs, healthy services, Ready status, no unexpected attention and synchronized sources in both Arr apps. Diagnostics and status are fetched through nginx. Accelerated scheduling exposed starvation; oldest-due-first dispatch now prevents it, with a focused regression. Ready assertions also exposed an unprivileged key-file check: status now uses the non-secret marker published by root networking instead of trying to inspect the private key directory. |
| 2. N-1→N, rollback, candidate matrix and promotion | Deferred in part; rejected in part. Existing-database upgrades are a real gap, distinct from migration out of the old Pompey architecture. Same-candidate clones do not prove upgrades. Before enabling unattended releases, candidate acceptance must include forward upgrades from the currently supported release and preservation of its household ledger. No downgrade-on-migrated-state guarantee or runtime rollback manager is added; HA cold backups remain the recovery plan. Per-engine/resource candidate matrices and transcript comparisons belong to that promotion work, not this foundation patch. Promotion remains disabled. |
| 3. Test-only knobs | Fixed the path gaps: a fourth case imports into staging before a metadata refresh and actual move-with-files; the shipped image now defines the same 30-second service and 3-second kill grace used by tests; every completed journey is checked for unclean engine shutdown; external host lookups now use recorded VPN DNS. TCP-only and disabled DHT/PEX/LSD remain deliberate fixture settings. Same-candidate test seeds retain them; restoring public discovery before export would weaken isolation and does not make those seeds suitable for an upgrade proof. |
| 4. Shipped kill switch and outage | Added actual UID 1000 TCP/DNS underlay probes against listening fixture services, live nftables output-policy inspection, local application availability during a real tunnel fault, unavailable status, blocked DNS/underlay, native reconnect and a real metadata refresh after recovery. Comparing generated rules with their own generator would be a self-referential assertion; detailed packet tests plus live behavior provide stronger evidence. The accelerated fault drops the remote link and removes the actual client peer; it does not claim to prove the production 180-second stale-handshake detection interval. |
| 5. Feature gaps | Valid expansion backlog, deliberately not all implemented now. See the coverage table below. Stop-sharing cleanup is now checked, the existing-state boot runs in CI, and the basic outage path is covered. |
| 6. Shared-state failures and diagnostics | Added typed background ownership/field errors, fixture-contract, timeout/stage, native-command, HTTP and assertion categories, plus bounded native history/blocklist/source/torrent/request snapshots and actual log tails. Fixture evidence is written outside its network namespace so tunnel failures cannot erase it. Keep fail-fast: continuing in tainted shared state would produce ambiguous advisory failures and is unnecessary for this foundation. No scenario retries. |
| 7. Configured clone in CI | Fixed. `--layout ci` runs the fresh/shared batch, cleanly exports its configured state, then boots one clone with a new identity and verifies the retained ledger. Both regular acceptance and candidate acceptance use it. |
| 8. Background/global invariants | Added unchanged monitoring, exact torrent removal for stop-sharing, case-owned queue/staging cleanup, no unexpected attention and failed owned Arr-command checks. Native commands explicitly awaited also fail immediately. Requiring every global queue/folder/command to be empty or successful would contradict the agreed noisy shared household; unrelated scheduled update/telemetry failures are expected in the isolated world. |
| 9. Robustness/timeouts | Engine identities now identify native processes and inspect their descendants, accepting additional workers only with UID 1000 and no capabilities. This also exposed root log-reader children; engine log readers now drop privileges. Packet workers use explicit NET_ADMIN/SYS_ADMIN/NET_RAW capabilities rather than privileged mode (NET_RAW is needed for ping on Podman); Docker network workers also disable its default AppArmor profile for private namespace mounts, without enabling privileged mode. Unknown requests are checked again after final verification. Keep generous hard budgets and recorded timings; historical p95 alerts need representative CI history and are deferred rather than inventing a threshold from one machine. |

The late-certification case temporarily stops the real controller service, imports into staging, refreshes real Radarr against the changed external metadata, then restarts the controller. An empty certification alone would not hold a title in staging: unknown ratings are intentionally routed to general. This deliberate interruption makes the existing-file branch observable without changing production routing semantics, job bodies or quality profiles. The test verifies the persisted `hadFiles` intent, exact bytes at the destination and no remaining staging payload.

## Coverage still to add

| Household promise | Current position |
| --- | --- |
| TV requests, season packs, kid/general TV routing and season monitoring | Planned; Sonarr setup/source health is checked, imports are not yet covered. |
| Non-admin household requests and automatic approval permissions | Planned; the current wizard/request path uses the first administrator. |
| Sharing to ratio and for one day, copy/hardlink behavior | Planned; stop-sharing torrent/queue/payload cleanup is now asserted. |
| Request deletion/decline, overlapping seasons and history-reset brake | Planned end to end; difficult ownership/history boundaries retain focused logic tests. |
| Explicit libraries and Max/other profile selections | Planned; current films exercise By Rating and the actual default quality profile. |
| Direct grabs, unmatched/rejected releases and ambiguous receipts | Planned end to end; conservative receipt safety retains focused logic tests. |
| Blocked-request diagnostics and rejected-release explanations | Planned; linked happy-path diagnostics and explicit real search already run through nginx. |
| Existing-state restart and reconciliation | Now run in CI through a same-candidate cold clone; not an HA restore or cross-version migration proof. |
| VPN paste/rotation, boot without a tunnel, prolonged outages | Planned; the new fault checks continued local access and native reconnect after startup. |
| NAT-PMP and anime behavior | Planned. Configuring profiles does not prove those journeys. |
| Forward upgrade of existing application databases/configuration | Required before unattended promotion; not proven by fresh installs or same-candidate clones. |

No item is dropped merely because it sounds slow. Ratio/time policies need acceleration at a declared time/ratio boundary with small real payloads; longer-lived behavior needs a separate targeted design. A 24-hour wall-clock wait is not an acceptable default CI implementation. Each added scenario still needs honest assertions about what it accelerates and what it proves.

## Policy and earlier architectural issues

Synthetic peers were explicitly requested and authorized during this campaign. The updated repository constraint records that decision; another approval is unnecessary. All peers remain synthetic and confined to verified isolated namespaces. Host networking is untouched.

The stale AGENTS.md reference to removed real-engine suites is corrected. PR/full-mode removal is intentional: the previous broad disconnected matrices were removed before adding household outcomes. The backlog above makes the temporary coverage reduction explicit.

The recommendation to restore a manual HAOS release gate conflicts with the user's decision. It is not restored. Supervisor installation, actual HAOS ingress/authentication, AppArmor, NAS permissions/hardlinks and Plex playback remain documented unautomated boundaries. Native nginx diagnostics in this test do not establish HA ingress authorization. Unattended dependency promotion and release remain disabled pending expanded acceptance.

Of the earlier 12 issues: 1/2/5/6 gain DNS, process-tree, Ready, clean-stop and CI-clone evidence here; 8 gains same-version existing-state boot only; 12 gains continued local service and reconnect evidence, not cold-start-without-VPN coverage. Issues 3/7/10 retain the explicitly listed missing failure-isolation, closure and direct-grab journeys. Issues 4/9/11 remain resolved by removing runtime swaps, vendoring apps and limiting support to amd64. There is no new downgrade, legacy-migration or HA restore guarantee.

## Validation

`bash tests/run.sh --layout ci` passed locally with host Podman: 9 product logic checks, 9 harness checks, 8 required packet tests, four shared real movie journeys, a real VPN fault/reconnect and one configured-clone journey. Total with cached layers: 132.61 seconds. Both HTTP/DNS unexpected-request lists were empty. The earlier focused fresh/outage/clone check also passed; timing and coverage limits are recorded in [testing.md](testing.md). Docker/HAOS AppArmor and real Supervisor/NAS/Plex behavior are not claimed as locally validated.
