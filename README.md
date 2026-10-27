# Pompey

Pompey runs Seerr, Prowlarr, Radarr, Sonarr and qBittorrent in one Home Assistant OS app. Internet traffic uses a WireGuard VPN. Plex runs separately. The Home Assistant sidebar provides setup, service status and diagnostics. Seerr on port **5055** provides search and media requests; Prowlarr on **9696** manages indexers.

Pompey is experimental software. Version 0.4.0 supports fresh amd64 installations with bundled, pinned applications. Migration from earlier installation layouts is not supported. See [release validation and current coverage](docs/testing.md).

## Installation

1. Add `https://github.com/esper256/ha-pompey` to the Home Assistant app store repositories, or copy `pompey/` to `/addons/pompey`.
2. Install Pompey. Home Assistant Supervisor builds the image locally; no separate Docker setup is required.
3. Configure the media and library directories. If Plex or an indexer is on your local network, add its network range under **Local network access**, for example `192.168.1.0/24`. Start Pompey and open its sidebar.
4. Paste a full-tunnel WireGuard `.conf` configuration from your VPN provider. It must route all IPv4 traffic. IPv6 internet access remains blocked unless the configuration also supplies an IPv6 tunnel. Provider-supplied shell hooks are removed.
5. Select **Search and requests** and complete Seerr's Plex setup. Use your Plex server's local IP address if its hostname cannot be resolved through VPN DNS.
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

Library paths are relative to the media directory. They must not overlap one another or the `downloads` directory. Path traversal and symlinks that escape the configured directory are rejected before directories are created. Restart Pompey after changing Pompey settings.

The default directory names are retained for compatibility. You can configure different names for your libraries. Automatic routing sends G, PG and PG-13 movies, and TV-Y, TV-Y7, TV-G and TV-PG series, to the libraries for lower age ratings. Other and unavailable ratings use the general libraries. Select a specific library in Seerr to override automatic routing.

Arr manages imports and upgrades. Replaced media files are retained in `downloads/recycle` for seven days. Configure Plex to use the library directories and exclude `downloads`.

**Stop sharing** stops completed torrents so Arr can move media into the library. **Share to ratio** seeds until the upload/download ratio reaches 1.0. **Share one day** seeds for 24 hours. Continued seeding uses hardlinks where supported, otherwise it requires an additional file copy. Arr removes completed torrents after import and the selected seeding target. Stalled transfers do not count toward the active download limit.

Prowlarr's **Grab** action sends downloads to `downloads/manual`. Pompey submits completed, stopped downloads to Arr only when Arr accepts the match. Unmatched files, rejected upgrades and ambiguous import attempts remain available for review through the advanced application interfaces. Downloads can originate independently of Seerr requests.

## Requests and quality

Recyclarr configures the **Default**, **Max** and **Anime** profiles from the release's pinned quality resources. **Anything** permits lower qualities without automatic upgrades. Required profiles must be available before requests are configured.

For TV, Default and Max prefer complete season packs within the same resolution tier and allow x265 HD releases. Release scores alone do not trigger replacements after the target quality is reached. Max can upgrade a 1080p release to 4K. Anime preferences include season packs and dual audio that includes the original language.

**By Rating** selects automatic routing through the staging directories `downloads/By Rating/Movies` and `downloads/By Rating/TV`. Pompey then moves imported titles to the appropriate library.

Removing or declining a previously observed request stops future monitoring without deleting imported media or active downloads. For TV requests with explicit seasons, overlapping requests and other monitored seasons are preserved. Incomplete request lists do not trigger cancellation. A changed Seerr instance or a large number of missing requests pauses monitoring changes until the request history is reviewed in diagnostics. Content managed directly in Arr is left unchanged.

## VPN and diagnostics

Pompey supports compatible full-tunnel WireGuard configurations without requiring a specific VPN provider. Port forwarding is optional. Configure a numeric **NAT-PMP gateway** only when your provider supports NAT-PMP, using the gateway address supplied by that provider.

An atomic IPv4/IPv6 firewall update installs the fail-closed policy before applications start. Local interfaces remain accessible during a VPN outage, while internet traffic and application DNS remain restricted to the tunnel. Saving a replacement WireGuard configuration reconnects the VPN.

The sidebar reports service health, delayed maintenance, library move failures and blocked imports. **Request and download diagnostics** displays current requests, downloads and import messages. **Search releases** performs an explicit indexer search and displays Arr's rejection reasons. Opening diagnostics does not initiate a search.

Configuration, requests, download policies and library routing run as separate jobs with bounded execution and retry backoff. They continue retrying after an outage.

## Updates and recovery

Application versions, artifact checksums, Seerr's image digest and quality-resource commits are pinned in the [bundle manifest](pompey/rootfs/usr/share/pompey/engines.json). Updates are delivered through tested Pompey releases. Runtime startup does not download or replace applications.

Application executables are owned by root. Applications and maintenance jobs run as the unprivileged `pompey` user, UID/GID 1000. Media storage must permit that identity to create, rename and hardlink files.

Home Assistant cold backups stop Pompey while persistent state is copied. Use a backup to recover from an unsuccessful application or database update. Pompey does not provide automatic binary or database rollback. See [architecture](docs/arr-auto-update.md) and [testing](docs/testing.md).

**Known issue in 0.4.0:** a download interrupted by VPN loss can remain stalled after the tunnel recovers. Local access and the firewall remain functional in the reproduced scenario. Release CI permits only this documented resume timeout after verifying the other network and service checks. Automatic upstream dependency promotion remains disabled.
