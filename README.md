# Pompey

Pompey runs Seerr, Prowlarr, Radarr, Sonarr and qBittorrent in one Home Assistant OS app. Internet traffic uses a WireGuard VPN. Use Plex, Jellyfin, Emby or another player separately; a media server is optional. The Home Assistant sidebar provides setup, service status and diagnostics. Seerr on port **5055** provides search and media requests; Prowlarr on **9696** manages indexers.

Pompey is experimental software. The current architecture supports fresh amd64 installations with bundled, pinned applications. Migration from earlier installation layouts is not supported. See [release validation and current coverage](docs/testing.md).

## Installation

1. Add `https://github.com/esper256/ha-pompey` to the Home Assistant app store repositories, or copy `pompey/` to `/addons/pompey`.
2. Install Pompey. Home Assistant Supervisor builds the image locally; no separate Docker setup is required.
3. Configure the media and library directories. Services on private networks directly connected to the app container, including the same HAOS host, are detected automatically. For Plex or an indexer on another device, add its network range under **Local network access**, for example `192.168.1.0/24`. Start Pompey and open its sidebar.
4. Paste a full-tunnel WireGuard `.conf` configuration from your VPN provider. It must route all IPv4 traffic. IPv6 internet access remains blocked unless the configuration also supplies an IPv6 tunnel. Provider-supplied shell hooks are removed.
5. To use requests, open **Seerr** from Config and complete Seerr's setup for your chosen media server. Native library/indexer management remains available independently. A media server's local IP may be needed if its hostname cannot be resolved through VPN DNS.
6. Open **Prowlarr** from Config, configure Prowlarr authentication and add your indexers. Pompey does not include an indexer service or subscription.

Restrict ports 5055 and 9696 to your local network. Radarr, Sonarr and qBittorrent listen on localhost. Their web interfaces are always available through authenticated Home Assistant Ingress.

## Storage and seeding

| Setting | Default |
| --- | --- |
| Media directory | `/media/dlna` |
| Movies: general | `Movies/Not Kid Friendly` |
| Movies: lower age ratings | `Movies/Kid Friendly` |
| TV: general | `TV/Not Kid Friendly` |
| TV: lower age ratings | `TV/Kid Friendly` |
| Torrent seeding policy | Stop sharing |
| Active download limit | 8 |
| NAT-PMP gateway | Empty; port forwarding disabled |

Library paths are relative to the media directory. They must not overlap one another or the `downloads` directory. Path traversal and symlinks that escape the configured directory are rejected before directories are created. Restart Pompey after changing Home Assistant environment settings; preferences saved in Ingress apply through normal reconciliation.

The default directory names are retained for compatibility. You can configure different names for your libraries. Automatic routing sends G, PG and PG-13 movies, and TV-Y, TV-Y7, TV-G and TV-PG series, to the libraries for lower age ratings. Other and unavailable ratings use the general libraries. Select a specific library in Seerr to override automatic routing.

Arr manages imports and upgrades. Replaced media files are retained in `downloads/recycle` for seven days. Point your media player or server at the library directories and exclude `downloads`.

Completed managed torrents always stop sharing so Arr can move media into the library. Legacy ratio/day seeding settings no longer change this policy. Pompey reconciles managed per-torrent limits as well as the downloader default; unknown categories are left alone. Hardlink imports are disabled. Keep downloads and libraries on the same mounted filesystem: native renames avoid copying media bytes, including on compatible CIFS shares. Cross-filesystem moves and explicit manual-copy imports may still copy into a final filename; this release does not guarantee atomic publication. Arr removes completed torrents after successful import. Stalled transfers do not count toward the active download limit.

Pompey checks download policies about every 30 seconds. Automatic Arr importing is disabled before qBittorrent starts. Pompey submits a native Move import only after a managed torrent is complete, stopped and associated with native grab history. Arr still performs matching, imports and cleanup: each retained torrent shows its seeding, import or ownership condition. Unknown downloads are not deleted simply because they are complete.

If storage I/O checks fail or exceed eight seconds, Pompey suspends its own maintenance jobs and reports the problem without stopping applications or interrupting native imports. Confirmed mount replacement is treated separately: Pompey stops qBittorrent, Radarr and Sonarr to limit writes to the wrong filesystem. Held services restart only after all checks pass. Home Assistant Logs records transitions without repeating identical failures. These checks do not make native copy imports atomic.

Successful delivery is durable. Once an imported movie or episode reaches a configured final library, Pompey records delivery and stops its automatic monitoring. Moving or deleting it later does not reopen the work or cause another download. Future TV episodes remain monitored. Seerr continues to show current media-server availability; Pompey does not duplicate that catalogue. **Try another** explicitly reopens the selected content for replacement. Existing successful native imports into final libraries can also establish delivery; incomplete history and staging imports cannot. This policy means automatic quality upgrades stop after delivery, including for Max.

Prowlarr's **Grab** action sends downloads to `downloads/manual`. Pompey submits completed, stopped downloads to Arr only when Arr accepts the match. Unmatched files, rejected upgrades and ambiguous import attempts remain available for review through the application interfaces. Downloads can originate independently of Seerr requests.

## Requests and quality

Recyclarr configures the **Default**, **Max** and **Anime** profiles from the release's pinned quality resources. **Anything** permits lower qualities without automatic upgrades. Required profiles must be available before requests are configured.

For TV, Default and Max prefer complete season packs within the same resolution tier and allow x265 HD releases. Max permits 4K selections. Delivered content is replaced only through an explicit retry. Anime preferences include season packs and dual audio that includes the original language.

**By Rating** selects automatic routing through the staging directories `downloads/By Rating/Movies` and `downloads/By Rating/TV`. Pompey then moves imported titles to the appropriate library.

Removing or declining a previously observed request stops future monitoring without deleting imported media or active downloads. For TV requests with explicit seasons, overlapping requests and other monitored seasons are preserved. Incomplete request lists do not trigger cancellation. A changed Seerr instance or a large number of missing requests pauses monitoring changes until the request history is reviewed in diagnostics. Content managed directly in Arr is left unchanged.

## VPN and diagnostics

Pompey supports compatible full-tunnel WireGuard configurations without requiring a specific VPN provider. Port forwarding is optional. Configure a numeric **NAT-PMP gateway** only when your provider supports NAT-PMP, using the gateway address supplied by that provider.

An atomic IPv4/IPv6 firewall update installs the fail-closed policy before applications start. Local interfaces remain accessible during a VPN outage. Replies from the published web interfaces retain the original container-network route even while the VPN is active and Local network access is empty. Direct private container networks are detected from live routes; additional LAN access is explicitly configured. Internet traffic and application DNS remain restricted to the tunnel. Saving a replacement WireGuard configuration reconnects the VPN.

The sidebar uses **Overview**, **Releases** and **Config** tabs. Overview shows torrents, monitored titles, source availability and VPN traffic. Its metrics link to the corresponding applications; Config lists application versions with the same links. Radarr, Sonarr and qBittorrent links use authenticated Ingress. A countdown ring shows the next dashboard update and becomes a warning when observations stop refreshing. Connection checks appear when setup or a service needs attention; individual indexer retry details stay inside the indexer metric. Unavailable observations retain their last known values and age.

Releases explains missing monitored media and native Arr queues, including work added outside Seerr. Filter by title, attention status or imported titles, then expand a table row to inspect transfer evidence and native import messages directly beneath it. One item is expanded at a time; observations refresh without reordering existing rows. **Inspect releases**, under Technical details, searches configured indexers and shows Arr’s rejection reasons without starting a download. The queue view is limited to the first 100 entries per Arr application; diagnostics warn when more exist.

The **Delivered** view keeps recorded deliveries even if Arr no longer recognizes a file you moved. Missing-title details show Arr's expected folder. **Check library** asks the native application to rescan that folder; it does not search indexers or search other folders. Files moved outside Arr are retained during a retry unless Arr still identifies them in the confirmation. Episode coordinates distinguish TV recovery sequences; older records without coordinates show the original release name.

**Review import** opens the managing Arr application’s Wanted → Missing page for completed handoffs (choose Manual Import and the displayed download folder), or Activity → Queue for other blocked imports. Use its interactive importer to review file matches and, for TV, episode assignments before confirming replacement of existing files. Pompey does not guess mappings or force imports. **Try another** rejects an active download or an imported release. Review the affected download and library files before confirming; a season pack can affect several episodes. Arr removes the selected files and associated sidecars according to its recycle policy, blocklists the release and performs a native replacement search. Unrelated library files are retained. Pompey refuses ambiguous ownership, missing native history, unhealthy storage or a changed preview. Interrupted operations retain their completed steps for review and are never automatically replayed. Completed handoffs hidden from Arr’s queue retain a native-history-backed recovery path. Uncertain submissions require review and are never automatically resubmitted. Normal background failed-download auto-retry remains disabled.

The compact Recovery section in Releases shows up to five recent retries, collapsed by default. **Diagnostics** beside it opens a dialog with release sequences to copy or save for an agent developing Pompey. Each Try another incident preserves your reason/note, the chosen release, affected file identities, transfer observations, the quality profile, a fresh pre-rejection candidate search and subsequent native selection/import history. The report keeps original titles and paths, without anonymization. Nothing is uploaded automatically. General application errors and logs remain in Home Assistant’s Logs view.

A later candidate search is not the original automatic selection list, and a current zero-seed observation does not establish the seed count when selected. “Imported” records native technical success, not satisfaction with its content. The journal retains up to 50 sequences and 20 attempts per sequence, with 200 native events each; open incidents are observed for 30 days. Old imported sequences may be pruned. The export reports retention limits, pruning and collection errors, and limits included sequence files to 8 MiB. Export valuable sequences before further testing. Collection refreshes native history only; it does not search indexers, grab releases or retry downloads.

Opening authenticated Ingress records the actual access hostname for Prowlarr's Allowed Hosts setting. Loopback, local interface addresses and explicit user entries remain allowed; no Supervisor subnet is hardcoded and no unrestricted wildcard is added. Open Ingress using a new hostname/IP before using that address for Prowlarr. Native host-filter changes may require an application restart to take effect.

Config saves simultaneous download limits and the Anime dual-audio preference. The controller applies saved preferences on its next successful configuration pass, normally within about a minute. The UI distinguishes saved from applied; changes to profiles can take longer if upstream services are unavailable. Existing HA behavior options are accepted until the first Ingress save, then Ingress preferences take precedence. Media paths and network access remain in Home Assistant’s app configuration.

Configuration, requests, download policies and library routing run as separate jobs with bounded execution and retry backoff. They continue retrying after an outage.

## Updates and recovery

Application versions, artifact checksums, Seerr's image digest and quality-resource commits are pinned in the [bundle manifest](pompey/rootfs/usr/share/pompey/engines.json). Updates are delivered through tested Pompey releases. Runtime startup does not download or replace applications.

Applications and maintenance jobs run as root inside the Home Assistant app container so they can write to Supervisor-managed SMB/CIFS media mounts. Container isolation and the Home Assistant AppArmor profile provide containment; shipped applications share a trust boundary. Pompey does not request privileged-container mode or additional capabilities for this storage access.

Startup checks directory listing and file creation, rename and removal as the application identity. A failure logs numeric ownership and permissions for the directory and its parents, the filesystem type and relevant mount permission settings. The share account also needs write access on the server. During operation, storage failures suspend Pompey maintenance; only a confirmed mount replacement stops applications. Checks run periodically and cannot prevent every write during the detection interval.

Pompey preserves existing directory ownership and does not recursively change media or application configuration ownership. Root-owned SMB directories with mode `0755` are supported. The SMB account still needs server-side write access, and a read-only, unavailable or full share will fail the storage check. Deleting `downloads` is not a permissions repair and can discard active downloads, manual grabs and recycled media.

Home Assistant's **Logs** view combines Pompey and application output, including qBittorrent's file log. Review copied logs for private information before sharing them in an issue; filtering does not guarantee that every sensitive value has been removed. Pompey does not upload diagnostics automatically.

Home Assistant cold backups stop Pompey while persistent state is copied. Use a backup to recover from an unsuccessful application or database update. Pompey does not provide automatic binary or database rollback. See [architecture](docs/arr-auto-update.md) and [testing](docs/testing.md).

After replacement of the VPN interface, Pompey gracefully reconnects qBittorrent under the maintenance lock. Native resume state preserves partial downloads and user-stopped torrents. Extension filtering excludes common executable/script payloads before transfer; it does not scan media for malware, and skipped files in mixed torrents can share torrent pieces with selected files.
