# Home Assistant Supervisor notes

The [household guide](../README.md) covers installation and daily use. Supervisor builds this add-on locally and supplies `/data/options.json`, persistent `/data`, the add-on configuration mount `/config`, and the media share `/media`.

One container owns the tunnel and all engines. It needs `NET_ADMIN` and `/dev/net/tun`. Ingress on port 8099 remains Pompey’s setup and live-status screen. Seerr is published on 5055 and Prowlarr on 9696; Radarr, Sonarr and the downloader WebUI remain on localhost. Debug consoles use authenticated Ingress. Plex is a separate app or LAN server.

The VPN file is `/config/wireguard/wg0.conf`, mode 0600. The setup endpoint atomically saves it; only the WireGuard service writes runtime configuration. The configuration’s DNS addresses replace container resolvers after routes are installed. Provider hooks, wg-quick routing tables and host sysctl/resolvconf mutations are disabled. Numeric Plex LAN addresses avoid dependence on a provider’s local-name resolution.

Persistent engine trees are `/data/engines`; secrets and policy state live below `/data/pompey`. Service readiness files and `health.json` are volatile under `/tmp/pompey`. The controller probes actual APIs rather than treating old readiness markers as health.

Engines wait until their startup configurations exist. First initialization permits Seerr’s public wizard to load before user id 1 exists. After initialization, both Seerr→Radarr and Seerr→Sonarr connections are required. Prowlarr apps and a configured source are required; optional Plex token login and chrome settings must not conceal a required failure.

See [update recovery](../docs/arr-auto-update.md) and the [manual release checklist](../docs/testing.md). A Dockerfile compile or isolated runtime smoke does not reproduce Supervisor, host mounts, Ingress or a household VPN.
