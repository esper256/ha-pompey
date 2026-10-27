# Home Assistant Supervisor notes

Supervisor builds `pompey/Dockerfile` locally for amd64 and starts one container. Users do not need to run Docker and Pompey publishes no image. Supervisor supplies `/data/options.json`, persistent `/data`, `/config` and `/media`.

The image contains all pinned applications in `/opt/pompey/engines` and Recyclarr resources in `/opt/pompey/resources`. Startup never fetches engines. Applications and controller jobs run as root inside the container for compatibility with Supervisor-managed SMB/CIFS mounts. Container isolation and the Home Assistant AppArmor profile provide containment; applications share a trust boundary. No dedicated application user or ownership rewrite is required. Startup checks real storage operations and preserves existing ownership. Server-side permissions and read-only mounts still apply.

s6 runs cont-init scripts before services. Initialization installs the atomic fail-closed firewall, generates configuration and creates mutable paths before engines start. Local applications do not wait for a working tunnel. WireGuard setup and firewall maintenance remain privileged. Only the short-lived endpoint DNS resolver uses the existing nobody identity (UID/GID 65534), restricting the bootstrap DNS exception to its sockets; applications retain VPN DNS and cannot use LAN DNS when the tunnel is down. Local network exceptions come from the `lan_networks` option.

Ingress on port 8099 provides setup, live status and diagnostics. Seerr is published on 5055 and Prowlarr on 9696. Advanced application interfaces require the corresponding Home Assistant option and authenticated Ingress. Diagnostics are available independently; explicit searches contact sources and show real Arr decisions. Plex is separate.

The controller serializes mutations. Download maintenance, rating routing, profiles and initialized search connections have their own prerequisites. Before Seerr has its first administrator, its public wizard is available and API-key 403 responses are expected. Afterwards both Arr connections, configured Prowlarr apps and an enabled synchronized source are required. Optional sign-in and interface settings do not override required connection failures.

Cold HA backups stop the add-on for a consistent state copy. There is no runtime bundle updater, database rollback manager or supported migration from the old architecture. Version 0.4.1 targets fresh installations. See [testing](../docs/testing.md) for what automated acceptance proves and the remaining hardware boundaries.
