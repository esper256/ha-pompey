# Pompey

Pompey runs Seerr, Prowlarr, Radarr, Sonarr and qBittorrent in one Home Assistant OS app. Internet traffic uses a WireGuard VPN. Use Plex, Jellyfin, Emby or another player separately; a media server is optional. The Home Assistant sidebar provides setup, service status and diagnostics. Seerr on port **5055** provides search and media requests; Prowlarr on **9696** manages indexers.

Pompey is experimental software. The current architecture supports fresh amd64 installations with bundled, pinned applications. Migration from earlier installation layouts is not supported. See [release validation and current coverage](docs/testing.md).

## Installation

1. Add `https://github.com/esper256/ha-pompey` to the Home Assistant app store repositories, or copy `pompey/` to `/addons/pompey`.
2. Install Pompey. Home Assistant Supervisor builds the image locally; no separate Docker setup is required.
3. Configure the media and library directories. Services on private networks directly connected to the app container, including the same HAOS host, are detected automatically. For Plex or an indexer on another device, add its network range under **Local network access**, for example `192.168.1.0/24`. Start Pompey and open its sidebar.
4. Paste a full-tunnel WireGuard `.conf` configuration from your VPN provider. It must route all IPv4 traffic. IPv6 internet access remains blocked unless the configuration also supplies an IPv6 tunnel. Provider-supplied shell hooks are removed.
5. To use requests, select **Search and requests** and complete Seerr's setup for your chosen media server. Native library/indexer management remains available independently. A media server's local IP may be needed if its hostname cannot be resolved through VPN DNS.
6. Select **Manage indexers**, configure Prowlarr authentication and add your indexers. Pompey does not include an indexer service or subscription.

Restrict ports 5055 and 9696 to your local network. Radarr, Sonarr and qBittorrent listen on localhost. Enable **Advanced application interfaces** to access their web interfaces through authenticated Home Assistant Ingress.

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
| Advanced application interfaces | Disabled |

Library paths are relative to the media directory. They must not overlap one another or the `downloads` directory. Path traversal and symlinks that escape the configured directory are rejected before directories are created. Restart Pompey after changing Home Assistant environment settings; preferences saved in Ingress apply through normal reconciliation.

The default directory names are retained for compatibility. You can configure different names for your libraries. Automatic routing sends G, PG and PG-13 movies, and TV-Y, TV-Y7, TV-G and TV-PG series, to the libraries for lower age ratings. Other and unavailable ratings use the general libraries. Select a specific library in Seerr to override automatic routing.

Arr manages imports and upgrades. Replaced media files are retained in `downloads/recycle` for seven days. Point your media player or server at the library directories and exclude `downloads`.

**Stop sharing** stops completed torrents so Arr can move media into the library. **Share to ratio** seeds until the upload/download ratio reaches 1.0. **Share one day** seeds for 24 hours. Continued seeding uses hardlinks where supported, otherwise it requires an additional file copy. Arr removes completed torrents after import and the selected seeding target. Stalled transfers do not count toward the active download limit.

Prowlarr's **Grab** action sends downloads to `downloads/manual`. Pompey submits completed, stopped downloads to Arr only when Arr accepts the match. Unmatched files, rejected upgrades and ambiguous import attempts remain available for review through the advanced application interfaces. Downloads can originate independently of Seerr requests.

## Requests and quality

Recyclarr configures the **Default**, **Max** and **Anime** profiles from the release's pinned quality resources. **Anything** permits lower qualities without automatic upgrades. Required profiles must be available before requests are configured.

For TV, Default and Max prefer complete season packs within the same resolution tier and allow x265 HD releases. Release scores alone do not trigger replacements after the target quality is reached. Max can upgrade a 1080p release to 4K. Anime preferences include season packs and dual audio that includes the original language.

**By Rating** selects automatic routing through the staging directories `downloads/By Rating/Movies` and `downloads/By Rating/TV`. Pompey then moves imported titles to the appropriate library.

Removing or declining a previously observed request stops future monitoring without deleting imported media or active downloads. For TV requests with explicit seasons, overlapping requests and other monitored seasons are preserved. Incomplete request lists do not trigger cancellation. A changed Seerr instance or a large number of missing requests pauses monitoring changes until the request history is reviewed in diagnostics. Content managed directly in Arr is left unchanged.

## VPN and diagnostics

Pompey supports compatible full-tunnel WireGuard configurations without requiring a specific VPN provider. Port forwarding is optional. Configure a numeric **NAT-PMP gateway** only when your provider supports NAT-PMP, using the gateway address supplied by that provider.

An atomic IPv4/IPv6 firewall update installs the fail-closed policy before applications start. Local interfaces remain accessible during a VPN outage. Replies from the published web interfaces retain the original container-network route even while the VPN is active and Local network access is empty. Direct private container networks are detected from live routes; additional LAN access is explicitly configured. Internet traffic and application DNS remain restricted to the tunnel. Saving a replacement WireGuard configuration reconnects the VPN.

The sidebar uses **Overview**, **Requests** and **Config** tabs. Overview shows torrents, monitored titles, source availability, VPN traffic and bundled application versions. Connection checks appear when setup or a service needs attention. Unavailable observations retain their last known values and age.

Requests explains missing monitored media and native Arr download/import queues, including work added outside Seerr. Select a table row to inspect transfer speed, connected seeds/peers and native import messages. **Inspect releases** explicitly searches configured indexers and shows Arr’s rejection reasons; it does not start a download. Completed and declined request history stays in Seerr. The queue view is limited to the first 100 entries per Arr application; diagnostics warn when more exist.

**Collect diagnostics** in Requests creates a text snapshot to copy or save for an agent developing Pompey. It includes original request details, paths, server addresses, native application settings/history, download files and tracker messages, recent logs and cached explicit release searches, without anonymization. Application responses and logs can contain credentials. Nothing is uploaded automatically; this report is intended for your development agent rather than a public issue.

The export pages beyond the dashboard's 100-request limit and correlates requests with native Arr IDs and torrent hashes. It includes episode/file details for requested series and explicitly marks unavailable, partial and truncated evidence. Collection reads existing state; it does not search indexers, grab releases or retry downloads. Native debug logs preserve accepted/rejected alternatives while retained. A current zero-seed count does not establish that the release had zero seeds when selected, and a later manual search is not the original candidate list. Decisions made before diagnostic logging was enabled, or after logs have rotated away, cannot be reconstructed completely.

Opening authenticated Ingress records the actual access hostname for Prowlarr's Allowed Hosts setting. Loopback, local interface addresses and explicit user entries remain allowed; no Supervisor subnet is hardcoded and no unrestricted wildcard is added. Open Ingress using a new hostname/IP before using that address for Prowlarr. Native host-filter changes may require an application restart to take effect.

Config saves seeding policy, simultaneous download limits and the Anime dual-audio preference. The controller applies saved preferences on its next successful configuration pass, normally within about a minute. The UI distinguishes saved from applied; changes to profiles can take longer if upstream services are unavailable. Existing HA behavior options are accepted until the first Ingress save, then Ingress preferences take precedence. Media paths, network access and advanced interfaces remain in Home Assistant’s app configuration.

Configuration, requests, download policies and library routing run as separate jobs with bounded execution and retry backoff. They continue retrying after an outage.

## Updates and recovery

Application versions, artifact checksums, Seerr's image digest and quality-resource commits are pinned in the [bundle manifest](pompey/rootfs/usr/share/pompey/engines.json). Updates are delivered through tested Pompey releases. Runtime startup does not download or replace applications.

Applications and maintenance jobs run as root inside the Home Assistant app container so they can write to Supervisor-managed SMB/CIFS media mounts. Container isolation and the Home Assistant AppArmor profile provide containment; shipped applications share a trust boundary. Pompey does not request privileged-container mode or additional capabilities for this storage access.

Startup checks directory listing and file creation, rename and removal as the application identity. A failure logs numeric ownership and permissions for the directory and its parents, the filesystem type and relevant mount permission settings. The share account also needs write access on the server. During operation, detected storage failure pauses downloading and imports until access returns; checks run periodically and cannot prevent every write during the detection interval.

Pompey preserves existing directory ownership and does not recursively change media or application configuration ownership. Root-owned SMB directories with mode `0755` are supported. The SMB account still needs server-side write access, and a read-only, unavailable or full share will fail the storage check. Deleting `downloads` is not a permissions repair and can discard active downloads, manual grabs and recycled media.

Home Assistant's **Logs** view combines Pompey and application output, including qBittorrent's file log. Review copied logs for private information before sharing them in an issue; filtering does not guarantee that every sensitive value has been removed. Pompey does not upload diagnostics automatically.

Home Assistant cold backups stop Pompey while persistent state is copied. Use a backup to recover from an unsuccessful application or database update. Pompey does not provide automatic binary or database rollback. See [architecture](docs/arr-auto-update.md) and [testing](docs/testing.md).

After replacement of the VPN interface, Pompey gracefully reconnects qBittorrent under the maintenance lock. Native resume state preserves partial downloads and user-stopped torrents. Extension filtering excludes common executable/script payloads before transfer; it does not scan media for malware, and skipped files in mixed torrents can share torrent pieces with selected files.
