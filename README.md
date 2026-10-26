# Pompey

Pompey runs Seerr, Prowlarr, Radarr, Sonarr and qBittorrent inside one Home Assistant OS add-on, behind a WireGuard VPN. Plex runs separately. The Home Assistant sidebar shows setup and live health; Seerr on port **5055** is the household search interface, and Prowlarr on **9696** manages sources.

This is experimental software. Version 0.4.0 establishes a fresh-install architecture with bundled applications and a real request-to-library acceptance test. Existing-install migration is not supported. See [release confidence and current coverage](docs/testing.md).

## Install

1. Add `https://github.com/esper256/ha-pompey` to the Home Assistant app store repositories, or copy `pompey/` to `/addons/pompey`.
2. Install Pompey. Supervisor builds the image locally for amd64. There is no image to publish and no Docker command for the household to run.
3. Set your media and library folders. If Plex or a source is on your LAN, configure its network under **Local networks** (for example `192.168.1.0/24`). Start Pompey and open its sidebar.
4. Paste a full-tunnel WireGuard `.conf` from your VPN provider. Pompey uses its endpoints, addresses and DNS servers. The file must route all IPv4 traffic; IPv6 internet stays blocked unless the file also supplies an IPv6 tunnel. Provider shell hooks are stripped.
5. Wait for the bundled applications to start. Open search, complete Seerr’s Plex wizard, and supply a numeric LAN address for your separate Plex server. VPN DNS may not resolve local names.
6. Open sources and set Prowlarr’s login. Add an indexer you already use. Pompey does not supply sources.

Keep ports 5055 and 9696 on your LAN. The hidden engines remain on localhost; enabling **Debug** exposes their consoles through authenticated Home Assistant Ingress.

## Storage and sharing

| Option | Default |
| --- | --- |
| Media folder | `/media/dlna` |
| Movies | `Movies/Not Kid Friendly` |
| Kid movies | `Movies/Kid Friendly` |
| TV | `TV/Not Kid Friendly` |
| Kid TV | `TV/Kid Friendly` |
| After download | Stop sharing |
| Simultaneous downloads | 8 |
| NAT-PMP gateway | Empty (disabled) |
| Debug | Off |

Library paths are relative to the media folder. They must not overlap one another or anything under `downloads`. Traversal and escaping symlinks are rejected before creating folders. Restart after changing add-on options.

Arr owns ordinary imports and upgrades. Pompey does not guess that a new download is a duplicate just because the library already contains a title. Older replaced files go into `downloads/recycle` for seven days. Do not add `downloads` to Plex libraries.

**Stop sharing** stops a completed transfer so Arr can move it into the library. **Share to ratio** keeps sharing until ratio 1.0; **Share one day** keeps sharing for 24 hours of seeding. Sharing uses hardlinks where supported and otherwise needs a copy, so a NAS without hardlink support needs space for both copies until the goal is reached. Arr removes its completed client entries after import and the sharing goal. Stalled transfers do not occupy the active-download limit.

Prowlarr’s direct Grab button uses `downloads/manual`. Pompey asks Arr to match finished, stopped files there and submits only accepted matches. Unmatched files, rejected upgrades and ambiguous import attempts stay on disk for review through Debug. Normal imports never use this fallback. Pompey no longer invents episode identities or moves extras into Plex folders.

## Requests and quality

Recyclarr configures **Default** and **Max** from the release’s pinned TRaSH resources. **Anything** permits lower qualities without automatic upgrades. Pompey waits for real profiles instead of creating empty lookalikes.

For TV, Default and Max prefer complete season packs within the same resolution tier and allow compact x265 HD releases. Once the target quality is reached, release scores alone do not trigger replacements. Max can still upgrade a 1080p fallback to 4K.

Choose **By Rating** for automatic kid/general routing, or choose a specific library. Unknown ratings go to general. By Rating is a staging folder under `downloads` (`downloads/By Rating/Movies` and `downloads/By Rating/TV`), then each title moves into the kid or general library. Watch through your separate Plex installation, and do not point Plex at `downloads`.

Removing or declining a request stops future monitoring for a title Pompey previously observed as requested. For TV requests with explicit seasons, only removed requested seasons are unmonitored; overlapping requests and other monitored seasons are preserved. It retains library files and in-flight downloads. An unreadable, truncated or changing request list never triggers cancellation. A changed search instance or bulk disappearance pauses monitoring changes until you review the history in Pompey’s diagnostics. Requests created directly in Arr are left alone, and declined requests are not silently recreated.

## VPN and health

Any compatible full-tunnel WireGuard VPN can be used; a particular provider is not required. Port forwarding is optional: supply the numeric **NAT-PMP gateway** only if your provider supports it. Do not infer it from the DNS server. Some providers require enabling forwarding when generating the configuration.

The firewall is installed as one atomic IPv4/IPv6 transaction. Failure prevents application startup. Applications can start and remain accessible locally while the tunnel is unavailable. Tunnel loss leaves the drop policy in place, with explicit local-network, loopback, VPN endpoint and UI-reply exceptions. Engine DNS never falls back to the household resolver. Pasting a replacement file causes the WireGuard service to reconnect.

The sidebar reports current service health, delayed maintenance, failed library moves and blocked imports. **Why isn’t this available?** collects request, download and import evidence. An explicit search shows Arr’s release rejection reasons; opening diagnostics does not launch searches. Configuration, requests, download policy, rating routing have separate bounded jobs and retry backoff; they continue retrying after a prolonged outage.

## Releases and recovery

Engine versions, artifact checksums, Seerr’s image digest and Recyclarr resource commits live in the checked-in [bundle manifest](pompey/rootfs/usr/share/pompey/engines.json). Updates arrive with a tested Pompey release; runtime does not follow upstream `latest`.

Supervisor builds this add-on locally with all manifest-pinned applications and Recyclarr resources. Startup performs no artifact download or application replacement. Executables are root owned; engines and controller jobs run as one unprivileged user. Writable media directories must permit that user (UID/GID 1000) to create, rename and hardlink files.

Home Assistant takes cold backups: Pompey stops while its state is copied. Use an HA backup to recover from a failed application/database update. Pompey does not maintain binary/database snapshots or a second runtime updater. Image builds need internet access; application internet traffic requires the VPN. See [architecture](docs/arr-auto-update.md) and [testing](docs/testing.md).
