# Home Assistant Supervisor notes

Supervisor builds `pompey/Dockerfile` locally for amd64 and starts one container. The household never runs Docker and Pompey publishes no image. Supervisor supplies `/data/options.json`, persistent `/data`, `/config` and `/media`.

The image contains all pinned applications in `/opt/pompey/engines` and Recyclarr resources in `/opt/pompey/resources`. Startup never fetches engines. Root-owned executables run as the shared `pompey` user (UID/GID 1000). Media directories need compatible permissions; Pompey owns its generated directories without recursively changing ownership of household media.

s6 runs cont-init scripts before services. Initialization installs the atomic fail-closed firewall, generates configuration and assigns mutable paths before engines start. Local applications do not wait for a working tunnel. WireGuard setup and firewall maintenance remain privileged. Bootstrap DNS is used explicitly for endpoint resolution; applications retain VPN DNS and cannot use LAN DNS when the tunnel is down. Local network exceptions come from the `lan_networks` option.

Ingress on port 8099 provides setup, live status and household diagnostics. Seerr is published on 5055 and Prowlarr on 9696. Hidden consoles remain behind Debug and authenticated Ingress. Diagnostics are available without enabling Debug; explicit searches contact sources and show real Arr decisions. Plex is separate.

The controller serializes mutations. Download maintenance, rating routing, profiles and initialized search connections have their own prerequisites. Before Seerr has its first administrator, its public wizard is available and API-key 403 responses are expected. Afterwards both Arr connections, configured Prowlarr apps and an enabled synchronized source are required. Optional sign-in/chrome settings never hide a required miss.

Cold HA backups stop the add-on for a consistent state copy. There is no runtime bundle updater, database rollback manager or supported migration from the old architecture. Version 0.4.0 targets fresh installations. See [testing](../docs/testing.md) for what automated acceptance proves and the remaining hardware boundaries.
