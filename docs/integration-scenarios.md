# Integration acceptance scenarios

This is the numbered list used to select the first expansion. An outcome can be an assertion in a shared scenario rather than a separate installation. Unselected families remain stretch goals; their presence here does not claim passing coverage.

| # | Outcome | First expansion |
| --- | --- | --- |
| 1 | Movie request reaches the correct rating-based library | Existing PG/R/unknown scenarios, exact bytes and subtitles |
| 2 | Single TV episode reaches the correct library | `single-episode` |
| 3 | Complete season pack imports correctly | `season-pack`, two distinct episodes |
| 4 | Selected seasons download only intended episodes | `selected-seasons`, two offered seasons and only season 2 requested |
| 5 | Waiting request completes when content becomes available | `waiting-content`, publish the release and invoke native RSS sync; also carried across restart |
| 6 | Upgrade preserves replaced media in recycle | Stretch |
| 7 | Inferior releases do not replace better files | Stretch |
| 8 | Default and Anything profiles behave as advertised | Default rejects real 480p; Anything requests/imports real 480p without automatic upgrades |
| 9 | Max accepts fallback and upgrades to target | Stretch |
| 10 | Anime prefers season packs and dual audio | `anime-dual-audio`, native scores and actual selected download |
| 11 | Original-language protection | Foreign-only, original and suitable dual-audio alternatives in TV searches |
| 12 | Stop-sharing imports and cleans up torrents | Shared cleanup invariants on every downloaded case; baseline also downloads NFO/text/URL extras and requires their absence throughout media storage while movie/subtitle checksums survive |
| 13 | Ratio sharing retains library access and seeds to goal | Stretch |
| 14 | Time sharing stops after goal | Stretch |
| 15 | Hardlink sharing and copy fallback | Stretch |
| 16 | Download limits and stalled-transfer capacity | Stretch |
| 17 | Direct grabs import accepted matches without Seerr | Stretch |
| 18 | Unmatched/rejected direct grabs remain reviewable | Stretch |
| 19 | Ambiguous import acknowledgement never duplicates submission | Stretch |
| 20 | Unexpected downloads do not block unrelated work | Stretch |
| 21 | Deleted/declined requests stop monitoring without removing media/downloads | Delete completed movie/TV and the resumed partial movie; update expected ledger only after observed closeout. Declining pending requests remains stretch coverage (Seerr rejects decline of approved requests) |
| 22 | Overlapping requests preserve requested seasons | Stretch |
| 23 | Closeout preserves manually managed content | Stretch |
| 24 | History reset/bulk disappearance activates brake | Stretch |
| 25 | Reviewed history rebaseline preserves monitoring | Stretch |
| 26 | Incomplete/unavailable history never cancels content | Stretch |
| 27 | Diagnostics explain unfulfilled requests | Existing happy-path search; broader blocked cases stretch |
| 28 | Source changes update synchronization and health | Stretch |
| 29 | Source outage preserves pending requests and recovers | Stretch |
| 30 | Metadata outage preserves downloads and recovers | Stretch |
| 31 | Storage failures preserve data and recover | Stretch |
| 32 | Maintenance jobs fail independently | Stretch |
| 33 | Local startup works without VPN, with internet blocked | Stretch |
| 34 | VPN loss during download preserves local access and resumes | `vpn-download-loss`, separate destructive network scenario |
| 35 | VPN configuration rotation reconnects without leaking | `vpn-rotation`, paste a new client key and endpoint port through setup |
| 36 | Individual app restarts preserve fulfilment | Stretch |
| 37 | Whole-container restart preserves pending/partial/completed state | `restart-partial`, one clean stop/start of the same container, exact request identities, cached partial bytes and background media |

## Test boundaries

All shipped applications remain real. Only external catalogue, indexer/tracker and peer behavior is controlled. Release alternatives have distinct real torrent hashes and valid payloads; unwanted alternatives are not silently unavailable. Metadata runtime, torrent lengths and actual media agree. The 480p fixture is genuinely 854×480, not renamed 1080p. Its native Arr media probe and imported quality are asserted.

The synthetic Skyhook schema was captured from the public Sonarr metadata response envelope and its content replaced with fictional identifiers, one-minute episodes and empty artwork. TMDB replay includes the explicit anime keyword used by Seerr. XEM replay returns correctly typed empty mappings for these fictional series, following the pinned Sonarr client contract. Absolute/scene episode remapping remains outside these season/episode-number tests. Fixture endpoints remain strict; a new hostname/path fails the fixture contract.

The restart handoff is declared before execution. It is not a retry after failure: the first phase proves a waiting request and an incomplete torrent, performs shared background checks, and exits cleanly. The host validates shutdown, archives the first boot's logs/report/transcript, and starts the same container once. The second phase proves the original requests and cached partial bytes survived before completing them. Synthetic payloads and external credentials are reconstructed identically; actual application databases, downloader resume files and media are preserved. It does not simulate Supervisor or promise HA restore/upgrade compatibility.

Tests assert application workflows and native decisions. They must not directly set monitoring flags, patch engine binaries, clear native databases/cooldowns, force a torrent to stop, or silently accept a lower-priority release to make a test pass. A legitimate product failure stays a failing assertion with evidence, rather than becoming an expected failure or skipped CI requirement.

Focused validation uses names, for example:

```sh
bash tests/run.sh --skip-build --integration-only --only single-episode season-pack selected-seasons anime-dual-audio
bash tests/run.sh --skip-build --integration-only --only waiting-content restart-partial
bash tests/run.sh --skip-build --integration-only --only vpn-download-loss
```

The full CI layout additionally checks the logic/packet suites and a configured clone. Focused runs are diagnosis tools and do not replace that gate. Passing/failing observations for this expansion are recorded in the testing guide and PR; this table describes intended assertions, not an unconditional validation claim.
