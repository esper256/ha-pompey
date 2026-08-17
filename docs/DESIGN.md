# Arr Stack on Home Assistant OS — Design

Status: accepted for implementation
Scope: one Home Assistant app (addon) that runs an *arr media stack and a Proton VPN client in a **single Docker image / single Supervisor container**

This document records the architecture for `esper256/ha-arr-addons`. It is the source of truth until a later revision supersedes it.

## Verdict: one container is the correct HAOS approach

The goal is: every *arr process that talks to the public internet must egress only through Proton VPN, with a kill switch if the tunnel drops.

On a normal Docker host that is done with Gluetun plus `network_mode: service:gluetun` (or `network_mode: container:<id>`). **Home Assistant OS cannot do that.**

Supervisor starts exactly one container per app. `config.yaml` can set `host_network: true` or leave the container on the Supervisor `hassio` bridge. There is no `network_mode: container:…` option, no Compose file, and no supported way for addon A (Gluetun) to donate its network namespace to addon B (qBittorrent / Sonarr / …).

Rejected alternatives:

| Alternative | Why it fails on HAOS |
| --- | --- |
| Separate Gluetun addon + `network_mode: service:gluetun` on the *arr addons | Supervisor never passes `network_mode: container:…` |
| Gluetun HTTP/SOCKS proxy that *arr apps point at | HTTP proxy does not carry BitTorrent peer traffic (TCP+UDP). Indexer HTTPS could be proxied; torrents cannot |
| `docker_api: true` and run Compose inside the addon | Security rating collapses to 1; nested Docker on HAOS is unsupported and brittle |
| `host_network: true` plus policy routing / iptables on the host | Fragile, fights Supervisor DNS, -1 security, easy to leak or break HA itself |
| One VPN client per *arr addon | Wastes Proton connection slots, N kill switches to get wrong, no shared forwarded port |

So: **jam Gluetun and the *arr processes into one container.** They already share one network namespace. That is exactly what `network_mode: service:gluetun` would have given us, without asking Supervisor for a feature it does not have.

Caveat we are accepting on purpose: Sonarr/Radarr/Prowlarr *could* stay off-VPN and only the download client would strictly need the tunnel. That is the usual homelab split. This project’s threat model is stricter — ISP/path observers should not see indexer queries, metadata lookups, or torrent traffic. Everything in this container treats the VPN as the only internet. Local Home Assistant, LAN media servers, and storage stay reachable via Gluetun outbound-subnet exceptions.

Do **not** put Jellyfin/Plex/Emby in this image. Playback and transcoding do not belong on a VPN kill switch.

## Goals

- Proton VPN (WireGuard preferred, OpenVPN fallback) is up before any *arr process is allowed to use the network.
- If the tunnel dies, Gluetun’s firewall is the kill switch. qBittorrent is also bound to the tunnel interface (defense in depth).
- Users configure the stack from the Home Assistant app options UI (`/data/options.json`), not by editing Compose files.
- One Ingress entry in the HA sidebar, with a launcher that reaches every bundled Web UI.
- Apps talk to each other on `127.0.0.1` (same namespace).
- Downloads and libraries live on `/media` and `/share` so hardlinks can work.
- Published, multi-arch images on GHCR. Supervisor should pull, not compile on a Pi.

## Non-goals (v1)

- Provider-agnostic VPN UI beyond Proton (Gluetun can do others later).
- Docker-in-Docker, Portainer, or letting users inject extra Compose services.
- Bundling a media server.
- Runtime self-update of Sonarr/Radarr/etc. inside a running container.
- Supporting HA Core in a random Docker Compose install (Supervisor APIs, Ingress, and `/data/options.json` are assumed).

## Repository layout

Home Assistant discovers apps by scanning the git repo for folders that contain `config.yaml`, plus a root `repository.yaml`.

```text
repository.yaml                 # repo name shown in the HA app store
README.md
docs/DESIGN.md                  # this file
.github/workflows/              # GHCR multi-arch image builds
arr-stack/                      # the one app (more apps can be added later)
  config.yaml                   # Supervisor metadata + options schema
  Dockerfile
  apparmor.txt
  DOCS.md                       # user-facing HA store docs
  CHANGELOG.md
  translations/en.yaml
  rootfs/                       # copied into the image
```

v1 ships a single app slug, `arr-stack`. Optional later apps (for example a VPN-only download client) would each get their own folder.

## Image construction

### Why not `FROM linuxserver/sonarr` as the final image

linuxserver images are excellent *one-app* images. They already include s6, the *arr binary, and a `/config` convention. They do not include Gluetun, the other *arr apps, bashio, or an Ingress nginx. Wrapping one linuxserver image (what alexbelgium does) cannot give us a shared VPN.

### Target image

PID 1 is s6-overlay from `ghcr.io/home-assistant/base` (Alpine). That base already has bashio and the HA label conventions.

Build stages:

1. `FROM qmcgaw/gluetun:v3 AS gluetun` — copy `/gluetun-entrypoint` (the Gluetun binary).
2. Optional later: `FROM lscr.io/linuxserver/{sonarr,radarr,prowlarr,bazarr,qbittorrent}` and `COPY --from=… /app/<name>` so we reuse their packaged bits instead of re-implementing download URLs.
3. Final stage: HA base + nginx + iptables + OpenVPN + WireGuard userspace/kernel tools + the Gluetun binary + s6 service definitions.

v1 skeleton installs Gluetun + nginx + a launcher. *arr binaries are wired in a follow-up once the VPN/Ingress plumbing is proven. The s6 service names and config paths are already reserved.

### Process graph (s6)

```text
cont-init
  00 read /data/options.json via bashio
  10 write Gluetun env into /etc/gluetun.env (never logs secrets)
  20 render nginx from templates (X-Ingress-Path, enabled apps)
  30 prepare /config/<app> data dirs on addon_config
s6 services
  gluetun          # must become healthy on 127.0.0.1:9999
  nginx            # Ingress :8099, optional LAN :8089
  qbittorrent      # waits for gluetun health; binds to tun/wg
  prowlarr         # waits for gluetun health
  sonarr
  radarr
  bazarr
```

Disabled apps (HA option `enable_sonarr: false`, etc.) are not started. Their bits can still live in the image so toggling an app does not require a rebuild.

### Config and data paths

| Host (HAOS) | In container | Purpose |
| --- | --- | --- |
| `/addon_configs/<repo-hash>_arr-stack/` | `/config` | Per-app config.xml / qBittorrent.ini (persistent, user-visible) |
| Supervisor options | `/data/options.json` | HA UI settings (not edited by hand) |
| `/data` (always mapped) | `/data` | Tiny internal state (last forwarded port, generated secrets) |
| `/share` | `/share` | Shared files; good default for downloads if user has no NAS |
| `/media` | `/media` | Libraries. Keep downloads + libraries on the **same filesystem** for hardlinks |
| `/ssl` | `/ssl` | Unused for Ingress; reserved if someone exposes LAN TLS later |

linuxserver images assume `/config` is *that one app’s* config. We will give each app `/config/<app>` (`/config/sonarr`, `/config/radarr`, …) and pass `-data=` / equivalent. That is the main adaptation vs wrapping a stock linuxserver container.

HA timezone is already injected as `TZ`. Do not make the user type it again.

## Home Assistant settings

Supervisor validates options against `schema` in `config.yaml` and writes `/data/options.json`. `run.sh` / cont-init must treat that file as the only user input.

Secrets (`password` schema type) are stored by Supervisor and must never be printed. Gluetun should receive the WireGuard key through an env file with `600` mode, or Gluetun’s `*_SECRETFILE` paths.

### Required before the app can usefully start

These are required in the sense “the container should refuse to start *arr processes without them.” Schema uses `password?` / `str?` so the UI can be filled in after install; cont-init then fails closed.

| Option | Why |
| --- | --- |
| `vpn_type` | `wireguard` (default) or `openvpn` |
| `wireguard_private_key` | From Proton → WireGuard config → `PrivateKey`. Works for all Proton servers. Generate with **NAT-PMP (Port Forwarding)** enabled if torrenting. |
| **or** `openvpn_user` + `openvpn_password` | Proton *OpenVPN* credentials, not the account password. Append `+pmp` to the username when using OpenVPN port forwarding. |
| `server_countries` | Gluetun server filter, e.g. `Netherlands`. Avoid pinning a single hostname (those disappear from Gluetun’s list). |

### Important (defaults exist, users will change them)

| Option | Default | Why |
| --- | --- | --- |
| `port_forwarding` | `true` | Proton Plus NAT-PMP. Required for usable BitTorrent. Sets `VPN_PORT_FORWARDING=on` and `PORT_FORWARD_ONLY=on` |
| `lan_networks` | RFC1918 list | Gluetun `FIREWALL_OUTBOUND_SUBNETS`. Lets Sonarr talk to a LAN Jellyfin. Always union with Supervisor’s `172.30.32.0/23` |
| `enable_qbittorrent` / `enable_prowlarr` / `enable_sonarr` / `enable_radarr` / `enable_bazarr` | qbit+prowlarr+sonarr+radarr on, bazarr off | One image, optional processes |
| `connection_mode` | `ingress_noauth` | Same three-way switch alexbelgium uses: Ingress with HA auth only, Ingress plus app auth, or no Ingress |
| `log_level` | `info` | Passed to Gluetun and nginx |

### Not HA options (on purpose)

- PUID/PGID: HA apps run as root inside the container. We will not expose linuxserver-style PUID in v1.
- Individual *arr UI passwords in options.json: when `ingress_noauth` is set, HA already authenticated the user. App-level auth is a footgun if the host port is also published; `connection_mode` is how we talk about that.
- Server hostname pin: too brittle; country/region is enough.
- Download path micro-options: document TRaSH-style `/media/...` layout in DOCS.md rather than encoding every folder in Supervisor schema.

### Supervisor `config.yaml` flags this app needs

| Key | Value | Reason |
| --- | --- | --- |
| `init` | `false` | s6-overlay is PID 1 |
| `ingress` | `true` | Sidebar Web UI |
| `ingress_port` | `8099` | nginx |
| `privileged` | `NET_ADMIN` | Tunnel + iptables. Security: -1, offset by Ingress +2 and custom AppArmor +1 |
| `devices` | `/dev/net/tun` | WireGuard/OpenVPN |
| `map` | `addon_config` rw, `share` rw, `media` rw, `ssl` | Persistence + libraries |
| `ports` | optional LAN UI / ignore torrent ports on the host | Peer traffic must use the Proton forwarded port, not a published host UDP port |
| `image` | `ghcr.io/esper256/arr-stack` | Set once GHCR publishing works. Omit for local `/addons` builds |
| `timeout` | `120`+ | VPN handshake |
| `stage` | `experimental` until the kill switch is tested |
| `docker_api` | unset/false | Do not go there |
| `host_network` | `false` | Stay on `hassio` so Ingress and `supervisor` DNS work |
| `apparmor` | custom `apparmor.txt` | tun, s6, nginx, app configs |

## Ingress with multiple Web UIs

Supervisor gives **one** Ingress port and **one** “Open Web UI” button per app. There is no API for five sidebar entries from one container.

Pattern:

1. nginx listens on `8099`, `allow 172.30.32.2; deny all;`
2. `/` is a launcher (status of VPN + links to each enabled app)
3. `/sonarr`, `/radarr`, `/prowlarr`, `/bazarr`, `/qbittorrent` reverse-proxy to `127.0.0.1:<native-port>`
4. Websockets (SignalR for *arr, qBittorrent) get `Upgrade` headers
5. Each *arr `UrlBase` (or qBittorrent `WebUI\RootFolder`) is set to the short path (`sonarr`, …)
6. nginx `sub_filter` rewrites `/sonarr` → `$http_x_ingress_path/sonarr` because HA Ingress URLs are `/api/hassio_ingress/<token>/…` and *arr apps lowercase `UrlBase` (they cannot store the mixed-case token even if we wanted them to)

This is the alexbelgium nginx trick, extended from one location `/` to several locations plus a launcher. Use `$http_x_ingress_path` at request time rather than baking the token at container start (tokens rotate).

Optional host port (e.g. `8089`) can serve the same nginx without the `172.30.32.2` allowlist for LAN users who do not want Ingress. If that port is published, `connection_mode` must not be `ingress_noauth` unless the user understands they just put unauthenticated *arr UIs on their LAN.

## Guaranteeing Proton-only internet

Layers, all of them:

1. **Gluetun owns the default route and the firewall.** `FIREWALL_ENABLED=on` (Gluetun’s default). No tunnel → no egress except the exceptions below.
2. **Start order.** s6 does not start qBittorrent / *arr until `http://127.0.0.1:9999` (Gluetun health) succeeds. nginx may start earlier and show “VPN connecting”.
3. **Outbound exceptions** (`FIREWALL_OUTBOUND_SUBNETS`): Supervisor `172.30.32.0/23` (Ingress, `supervisor` API) plus user `lan_networks`. Never “allow 0.0.0.0/0”.
4. **DNS.** Gluetun’s DNS-over-TLS. *arr processes must use the resolver Gluetun binds on localhost, not the HA host’s DNS, or lookups bypass the tunnel.
5. **qBittorrent interface binding.** Bind to `tun0` / `wg0` / Gluetun’s `VPN_INTERFACE`. This is alexbelgium’s “app-level” VPN bind. It does not replace the firewall; it catches a misconfiguration where the firewall was turned off.
6. **Proton port forwarding.** `VPN_PORT_FORWARDING=on`, `PORT_FORWARD_ONLY=on`, WireGuard config created with NAT-PMP. Gluetun writes the random port to `/tmp/gluetun/forwarded_port`. A small hook (`VPN_PORT_FORWARDING_UP_COMMAND`) calls qBittorrent’s API and sets `listen_port`. Publishing `6881/udp` on the HA host is the wrong model and will be documented as such.
7. **Health / leak check.** Periodically read `/tmp/gluetun/ip`. If Gluetun becomes unhealthy, s6 takes down the download client first.

Proton specifics we will encode, not leave to folklore:

- WireGuard private key is account-scoped; country filter selects the endpoint.
- Free Proton servers: `FREE_ONLY=on` is incompatible with serious torrenting (no PF). Default is paid + PF.
- OpenVPN username is the OpenVPN-specific one from the Proton dashboard.

## Install and updates

### First install

1. User adds this GitHub repo in Settings → Apps → Repositories.
2. Supervisor reads `repository.yaml` + `arr-stack/config.yaml`.
3. If `image:` is set, Supervisor **pulls** `ghcr.io/esper256/arr-stack:<version>` for the host arch.
4. If `image:` is absent (local `/addons` clone), Supervisor **builds** the Dockerfile on the device. That is acceptable for development and too slow/fragile for real users (HA’s own publishing docs say this).
5. User pastes Proton credentials, starts the app. cont-init writes Gluetun env, starts the tunnel, then starts enabled apps. First-run bootstrap (later milestone) can register qBittorrent on `127.0.0.1` inside Sonarr/Radarr/Prowlarr so the user does not type Docker DNS names that do not exist.

*arr binaries are **baked into the image** at build time. We will not `curl` GitHub releases on every container start. That is slow, non-reproducible, and fights HA’s version pin.

### Updates

| What changes | How the user gets it |
| --- | --- |
| Sonarr/Radarr/Prowlarr/Bazarr/qBittorrent/Gluetun version | We bump the image, bump `config.yaml` `version` to match the image tag, user clicks Update in HA |
| HA options schema / nginx / VPN logic | Same: new addon version |
| Proton server list | Gluetun `UPDATER_PERIOD` (e.g. 24h) inside the running container — this is server metadata, not app binaries |

Disable each *arr app’s built-in updater (`UpdateMethod=docker` / equivalent). In-app “update now” would mutate a container that Supervisor thinks is still version X.

Do not auto-rebuild images from `latest` linuxserver tags without a human-visible changelog. Pin upstream versions in the Dockerfile (`ARG SONARR_VERSION=…`) and record them in `CHANGELOG.md`.

### Does this require hosting an image on Docker Hub / GHCR?

**Yes, for anyone installing from the GitHub repo as a store repository.**

- Preferred registry: `ghcr.io/esper256/arr-stack` (GitHub Packages, same org as the repo, `GITHUB_TOKEN` can push).
- `config.yaml` `version` **must equal** the image tag Supervisor will pull.
- Publish a multi-arch manifest (`amd64` + `aarch64`) via `home-assistant/builder` GitHub Actions. HA Yellow / Pi are aarch64.
- Docker Hub is optional and unnecessary if GHCR works.

Local build remains supported by leaving `image:` commented for developers who copy the folder into `/addons`.

## Survey: existing *arr stacks on HAOS

### alexbelgium/hassio-addons (the one that matters)

This is the de facto HAOS *arr store: Sonarr NAS, Radarr NAS, Prowlarr NAS, Bazarr NAS, Lidarr, Jackett, qBittorrent, Transmission, Transmission OpenVPN, Unpackerr. Each app is its **own** addon wrapping a **linuxserver** image.

Clever things we should steal:

- **Wrap, don’t fork the *arr apps.** linuxserver already does musl/netcore packaging, s6, and timely bumps. Copy `/app/<name>` (or equivalent) out of those images instead of inventing our own downloader.
- **Ingress nginx + `sub_filter` + `UrlBase`.** *arr apps do not honor `X-Ingress-Path`. alexbelgium sets `UrlBase` to the slug and rewrites HTML/JS so assets load under `/api/hassio_ingress/<token>/…`. Websocket `Upgrade` headers are required for SignalR.
- **`connection_mode`.** `ingress_noauth` (HA is the authenticator; disable *arr auth for local addresses), `ingress_auth`, `noingress_auth`. Documents the “do not port-forward 8989 to the internet” warning.
- **`ingress_entry`.** Single-app addons land the Open Web UI directly on `/sonarr`. We cannot do that for five UIs; we land on a launcher instead and keep the same rewrite trick per location.
- **`addon_config:rw` as `/config`.** Users can see config.xml via the Samba addon. Map `share` + `media` rw so NAS layouts work.
- **Optional OpenVPN/WireGuard inside qBittorrent.** Proof that `NET_ADMIN` + `/dev/net/tun` is allowed on HAOS and that AppArmor must be taught about `tun`. Two bind modes: container-level vs app-level. We will do **both**, but with Gluetun’s firewall as the real kill switch (alexbelgium’s VPN path is much weaker than Gluetun).
- **`env_vars` passthrough.** Escape hatch for linuxserver knobs we do not want to promote into first-class schema.
- **GHCR images named per addon + `updater.json`.** A bot watches upstream tags and opens version bumps. We should pin versions and automate bumps once the image exists; we should not silently float `latest`.
- **SMB/local disk mount scripts.** Useful on HAOS where the host is an appliance. **Not v1** — those scripts want `SYS_ADMIN` + `DAC_READ_SEARCH` (another security hit) and a huge `devices:` list. Prefer HA’s native Settings → Storage mounts into `/media` and `/share`. Revisit SMB if users cannot mount NAS any other way.

Issues they ran into that we must treat as ours:

| Issue | Their world | Our fix |
| --- | --- | --- |
| Cannot put the whole stack behind one Gluetun | One container per addon; Supervisor will not share namespaces. qBittorrent has a bolted-on VPN; Sonarr does not. Gluetun+PF feature request sat for years ([#1661](https://github.com/alexbelgium/hassio-addons/issues/1661)) | One container. Gluetun is a first-class s6 service |
| Proton/Gluetun port forwarding | Not implemented on the qBittorrent addon | Gluetun NAT-PMP + `VPN_PORT_FORWARDING_UP_COMMAND` → qBittorrent `listen_port` |
| *arr `UrlBase` lowercasing vs Ingress tokens | They rewrite around it | Same nginx `sub_filter`, plus `$http_x_ingress_path` so we do not freeze a token |
| Hardlinks fail if downloads and libraries are on different mounts | User education | DOCS.md TRaSH layout; keep both under `/media` when possible |
| Fat `devices:` / `privileged` for USB disks and SMB | Convenience on HAOS | Skip in v1; document HA Storage |
| linuxserver `/config` vs HA `/data/options.json` | `ha_lsio.sh` remaps paths | Never use stock linuxserver as the **final** image; pass explicit `-data=/config/<app>` |
| In-app updaters fighting Supervisor | Package info sets `UpdateMethod=docker` | Keep that; never run `Sonarr.Update` |
| Image size / Pi wear | One moderate image per app, pulled from GHCR | One larger image, still pulled, never built on-device |
| Cross-app wiring | User types `http://a0d7b954-sonarr:8989` style hostnames (repo hash prefixes) | `127.0.0.1` and optional first-run bootstrap |

### Other related projects (not HAOS addons, still informative)

- **binhex `*vpn` images / haugene transmission-openvpn.** Precedent for “VPN client + payload in one image.” We are doing that, with several payloads and Gluetun instead of a bespoke OpenVPN wrapper.
- **hotio / linuxserver Compose stacks.** The correct answer on a NAS. Irrelevant as a deploy mechanism on HAOS; still the source of binaries and folder conventions.
- **abarbarich/arrstack, corelab generators.** Compose + optional Gluetun sidecar for qBittorrent only. Confirms the usual split-tunnel; we are deliberately stricter.
- **martinargalas Arr Stack Card.** HA *dashboard* for already-running *arr APIs. Complementary later, not a substitute for running the apps.
- **Official HA `apps-example`.** Repository shape, builder workflows, `image:` + matching `version`, no `build.yaml` (deprecated Supervisor 2026.04). We follow that, not the 2024-era per-arch `build.yaml` files still sitting in older addon repos.

## Implementation phases

1. **Skeleton (this change).** Repo metadata, design doc, `arr-stack` app with HA options, Gluetun+nginx+launcher, kill-switch start order, GHCR workflow. *arr processes are stubs.
2. **VPN bring-up.** Real Proton WireGuard path, healthcheck, leak test, AppArmor that still allows `tun`.
3. **qBittorrent.** Bind to tunnel, PF hook, Ingress location.
4. **Prowlarr / Sonarr / Radarr.** linuxserver copy stages, UrlBase, localhost download client.
5. **Bazarr + first-run bootstrap + GHCR `image:` in `config.yaml`.**
6. **Hardening.** Automated leak test in CI (container with a mock tunnel), changelog discipline, maybe SMB mounts if needed.

## Open questions (do not block the skeleton)

- Exact linuxserver tag pin vs copying `/app` from their `latest` at build time (lean toward pinned versions).
- Whether Jellyseerr belongs in a later version of this image (it needs LAN access to Jellyfin; VPN is optional).
- Whether to expose Gluetun’s control server to the HA network for a status sensor (bind it to localhost only by default).
