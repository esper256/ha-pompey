# Arr Stack on Home Assistant OS — Design

Status: infrastructure accepted; **product target revised**
Scope: one Home Assistant app (addon) in a **single Docker image / single Supervisor container**, with Proton WireGuard in-process.

This document is the source of truth for **HAOS constraints, WireGuard, kill switch, Ingress mechanics, and registry publishing**. It assumed the user-facing product was bundled Sonarr/Radarr/Prowlarr/qBittorrent Web UIs. That product assumption is superseded by [PRODUCT.md](PRODUCT.md): one search-to-Plex pipeline; *arr pieces are optional hidden engines, not the UX.

## Verdict: one container is the correct HAOS approach

The goal is: every *arr process that talks to the public internet must egress only through Proton VPN, with a kill switch if the tunnel drops.

On a normal Docker host the usual trick is a Gluetun container plus `network_mode: service:gluetun`. **Home Assistant OS cannot do that.**

Supervisor starts exactly one container per app. `config.yaml` can set `host_network: true` or leave the container on the Supervisor `hassio` bridge. There is no `network_mode: container:…` option, no Compose file, and no supported way for addon A to donate its network namespace to addon B.

Rejected alternatives:

| Alternative | Why it fails on HAOS |
| --- | --- |
| Separate VPN addon + `network_mode: service:gluetun` on the *arr addons | Supervisor never passes `network_mode: container:…` |
| HTTP/SOCKS proxy that *arr apps point at | HTTP proxy does not carry BitTorrent peer traffic (TCP+UDP) |
| `docker_api: true` and run Compose inside the addon | Security rating collapses to 1; nested Docker on HAOS is unsupported |
| `host_network: true` plus policy routing / iptables on the host | Fragile, fights Supervisor DNS, easy to leak or break HA itself |
| One VPN client per *arr addon | Wastes Proton connection slots; N kill switches to get wrong |

So the apps and the VPN client live in **one container**. They already share one network namespace.

Caveat we are accepting on purpose: Sonarr/Radarr/Prowlarr *could* stay off-VPN and only the download client would strictly need the tunnel. This project’s threat model is stricter — ISP/path observers should not see indexer queries, metadata lookups, or torrent traffic. Local Home Assistant, LAN media servers, and storage stay reachable via iptables exceptions.

Do **not** put Jellyfin/Plex/Emby in this image. Playback and transcoding do not belong on a VPN kill switch.

## Verdict: Gluetun is not needed

Gluetun’s reason to exist is **namespace sharing**: it owns `wg0`/`tun0` in *its* container, and other Compose services join that namespace. We already have that by running everything in one container. Putting Gluetun in here would re-implement a sidecar that has nothing to sidecar.

What actually happens with native WireGuard in this container:

1. `wg-quick up wg0` creates **one** interface (`wg0`) in the container’s network namespace.
2. Proton’s config uses `AllowedIPs = 0.0.0.0/0`, so `wg-quick` installs a default route via `wg0`.
3. Every process in the container — nginx, qBittorrent, Sonarr, a shell — uses that route automatically. They do not get their own adapters. They do not need a special “use VPN” setting.
4. qBittorrent can *also* bind its torrent sockets to `wg0`. That is defense in depth, not how the VPN is provided.

So the user’s model is right, with one wording fix: the VPN does not hand each service an adapter. It hands the **namespace** one adapter and a default route. That is enough.

### What Gluetun would still have given us (and how we replace it)

| Gluetun feature | Needed here? | Replacement |
| --- | --- | --- |
| `network_mode: service:gluetun` | No — already one netns | nothing |
| Proton server list / `SERVER_COUNTRIES` | No | User drops the Proton-generated `wg0.conf` (it already has endpoint, peer key, address, DNS) |
| Kill switch firewall | **Yes** | Our iptables OUTPUT chain: drop everything except `wg0`, loopback, HA `172.30.32.0/23`, user LAN, and the UDP handshake to the Proton endpoint. Applied *before* `wg-quick up` so a downed tunnel cannot fall back to `eth0` |
| DNS-over-TLS | Optional | Point `/etc/resolv.conf` at Proton’s tunnel DNS (`10.2.0.1` in their WG configs). If the tunnel is down, DNS fails closed |
| NAT-PMP port forwarding | **Yes** for torrents | `natpmpc` against the Proton WG gateway, then set qBittorrent `listen_port` |
| Health / reconnect | **Yes** | s6: if `wg show wg0` handshake goes stale, exit and halt the download client |
| OpenVPN + 40 other providers | No for v1 | WireGuard + Proton only. OpenVPN can be a later `tun0` path without Gluetun |
| HTTP proxy / Shadowsocks / control server | No | noise |

Gluetun also wants to be PID 1 of its own image: it owns iptables, `/etc/resolv.conf`, and a healthcheck subcommand. Running it under s6 next to nginx and five *arr processes is extra coupling for no namespace benefit.

alexbelgium’s qBittorrent addon already does native OpenVPN/WireGuard inside one container (no Gluetun). We are doing that for the whole stack, with a stricter kill switch and Proton NAT-PMP.

## Goals

- Proton WireGuard is up (handshake observed) before any *arr process is allowed to use the network.
- If the tunnel dies, iptables still drops non-LAN egress. qBittorrent is also bound to `wg0`.
- Users configure the stack from the Home Assistant app options UI (`/data/options.json`) plus an optional Proton `wg0.conf` in addon_config.
- One Ingress entry in the HA sidebar, with a launcher that reaches every bundled Web UI.
- Apps talk to each other on `127.0.0.1` (same namespace).
- Downloads and libraries live on `/media` and `/share` so hardlinks can work.
- Published, multi-arch images on GHCR. Supervisor should pull, not compile on a Pi.

## Non-goals (v1)

- Provider-agnostic VPN UI, Gluetun, or country-based server rotation.
- OpenVPN (Proton WireGuard only until someone needs it).
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

linuxserver images are excellent *one-app* images. They already include s6, the *arr binary, and a `/config` convention. They do not include a VPN, the other *arr apps, bashio, or an Ingress nginx. Wrapping one linuxserver image (what alexbelgium does) cannot give us a shared tunnel.

### Target image

PID 1 is s6-overlay from `ghcr.io/home-assistant/base` (Alpine). That base already has bashio and the HA label conventions.

Build:

1. HA base + nginx + iptables + `wireguard-tools` + `wireguard-go` (userspace fallback) + `libnatpmp` (`natpmpc`).
2. Later: `FROM lscr.io/linuxserver/{sonarr,radarr,prowlarr,bazarr,qbittorrent}` and `COPY --from=… /app/<name>` so we reuse their packaged bits.

No Gluetun stage. Kernel WireGuard on HAOS is preferred; `wireguard-go` uses `/dev/net/tun` if the module is missing.

v1 skeleton brings up WireGuard, the kill switch, NAT-PMP, and an Ingress launcher. *arr binaries land in a follow-up. s6 service names and `/config/<app>` paths are already reserved.

### Process graph (s6)

```text
cont-init
  00 read /data/options.json via bashio; require a Proton WG config
  10 write /etc/wireguard/wg0.conf; apply iptables kill switch (before the tunnel)
  20 render nginx from templates
  30 prepare /config/<app> data dirs on addon_config
s6 services
  wireguard        # wg-quick up; stay healthy on a fresh handshake
  natpmp           # waits for handshake; renews Proton forwarded port
  nginx            # Ingress :8099, optional LAN :8089
  qbittorrent      # waits for handshake; binds to wg0
  prowlarr         # waits for handshake
  sonarr
  radarr
  bazarr
```

Disabled apps (HA option `enable_sonarr: false`, etc.) are not started. Their bits can still live in the image so toggling an app does not require a rebuild.

### Config and data paths

| Host (HAOS) | In container | Purpose |
| --- | --- | --- |
| `/addon_configs/<repo-hash>_arr-stack/` | `/config` | Per-app config.xml / qBittorrent.ini (persistent, user-visible) |
| `/addon_configs/<repo-hash>_arr-stack/wireguard/` | `/config/wireguard` | Proton `wg0.conf` (preferred credential path) |
| Supervisor options | `/data/options.json` | HA UI settings (not edited by hand) |
| `/data` (always mapped) | `/data` | Tiny internal state (last forwarded port) |
| `/share` | `/share` | Shared files; good default for downloads if user has no NAS |
| `/media` | `/media` | Libraries. Keep downloads + libraries on the **same filesystem** for hardlinks |
| `/ssl` | `/ssl` | Unused for Ingress; reserved if someone exposes LAN TLS later |

linuxserver images assume `/config` is *that one app’s* config. We will give each app `/config/<app>` (`/config/sonarr`, `/config/radarr`, …) and pass `-data=` / equivalent.

HA timezone is already injected as `TZ`. Do not make the user type it again.

## Home Assistant settings

Supervisor validates options against `schema` in `config.yaml` and writes `/data/options.json`. cont-init must treat that file plus `/config/wireguard/` as the only user input.

Secrets (`password` schema type) are stored by Supervisor and must never be printed.

### Required before the app can usefully start

Schema uses optional fields so the UI can be filled in after install; cont-init then fails closed unless **one** of these is present:

| Source | What |
| --- | --- |
| File | `/config/wireguard/<wireguard_config>` — the `.conf` Proton’s dashboard generates (NAT-PMP enabled). Preferred. |
| Options | `wireguard_private_key`, `wireguard_address`, `wireguard_peer_public_key`, `wireguard_endpoint` — the same fields as that file, for people who only have the HA UI |

`wireguard_dns` defaults to `10.2.0.1` (Proton’s tunnel DNS). There is no country picker: the Proton config *is* the server choice. To change country, generate a new Proton config.

### Important (defaults exist, users will change them)

| Option | Default | Why |
| --- | --- | --- |
| `wireguard_config` | `wg0.conf` | Filename under `/config/wireguard/` |
| `port_forwarding` | `true` | Run `natpmpc` on the Proton gateway and push the port into qBittorrent |
| `lan_networks` | RFC1918 list | iptables exceptions. Lets Sonarr talk to a LAN Jellyfin. Always union with Supervisor’s `172.30.32.0/23` |
| `enable_qbittorrent` / `enable_prowlarr` / `enable_sonarr` / `enable_radarr` / `enable_bazarr` | qbit+prowlarr+sonarr+radarr on, bazarr off | One image, optional processes |
| `connection_mode` | `ingress_noauth` | Same three-way switch alexbelgium uses: Ingress with HA auth only, Ingress plus app auth, or no Ingress |
| `log_level` | `info` | nginx / our scripts |

### Not HA options (on purpose)

- PUID/PGID: HA apps run as root inside the container. We will not expose linuxserver-style PUID in v1.
- Individual *arr UI passwords in options.json: when `ingress_noauth` is set, HA already authenticated the user. App-level auth is a footgun if the host port is also published; `connection_mode` is how we talk about that.
- Gluetun-style `SERVER_COUNTRIES`: would need a server database. The Proton file already picked a server.
- Download path micro-options: document TRaSH-style `/media/...` layout in DOCS.md rather than encoding every folder in Supervisor schema.

### Supervisor `config.yaml` flags this app needs

| Key | Value | Reason |
| --- | --- | --- |
| `init` | `false` | s6-overlay is PID 1 |
| `ingress` | `true` | Sidebar Web UI |
| `ingress_port` | `8099` | nginx |
| `privileged` | `NET_ADMIN` | Tunnel + iptables. Security: -1, offset by Ingress +2 and custom AppArmor +1 |
| `devices` | `/dev/net/tun` | userspace WireGuard fallback |
| `map` | `addon_config` rw, `share` rw, `media` rw, `ssl` | Persistence + libraries |
| `ports` | optional LAN UI; do not publish torrent ports on the host | Peer traffic must use the Proton forwarded port on `wg0` |
| `image` | `ghcr.io/esper256/arr-stack` | Set once GHCR publishing works. Omit for local `/addons` builds |
| `timeout` | `120`+ | VPN handshake |
| `stage` | `experimental` until the kill switch is tested |
| `docker_api` | unset/false | Do not go there |
| `host_network` | `false` | Stay on `hassio` so Ingress and `supervisor` DNS work |
| `apparmor` | custom `apparmor.txt` | wg, tun, s6, nginx, app configs |

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

1. **Default route via `wg0`.** Proton `AllowedIPs = 0.0.0.0/0` makes every process use the tunnel without binding to it.
2. **iptables kill switch, independent of the route table.** OUTPUT: accept loopback, `wg0`, conntrack established, HA `172.30.32.0/23`, user `lan_networks`, UDP to the Proton endpoint; drop the rest. IPv6 OUTPUT drop except loopback. Applied in cont-init *before* `wg-quick up`. If `wg0` vanishes, traffic cannot fall back to `eth0`.
3. **Start order.** s6 does not start qBittorrent / *arr until `wg show wg0` shows a fresh handshake. nginx may start earlier and show “VPN connecting”.
4. **DNS.** `/etc/resolv.conf` nameserver is the Proton tunnel DNS. Do not use Supervisor’s recursive DNS for *arr lookups (that can bypass the tunnel). `supervisor` / `hassio` remain in `/etc/hosts` via Supervisor `extra_hosts`.
5. **qBittorrent interface binding.** Bind torrent sockets to `wg0`. Does not replace the firewall; catches “I turned the kill switch off”.
6. **Proton NAT-PMP.** WireGuard config must be generated with **NAT-PMP (Port Forwarding)** enabled. `natpmpc -a 1 0 udp 60 -g <wg DNS/gateway>` (usually `10.2.0.1`) renews the random port; we write it to `/tmp/vpn/forwarded_port` and set qBittorrent `listen_port`. Publishing `6881/udp` on the HA host is the wrong model.
7. **Health.** If the handshake is stale, the WireGuard s6 service exits and takes the download client down with it.

Ingress and LAN Web UIs are *incoming* on `eth0` (INPUT). The kill switch is OUTPUT. HA’s sidebar keeps working while internet egress is forced through Proton.

## Install and updates

### First install

1. User adds this GitHub repo in Settings → Apps → Repositories.
2. Supervisor reads `repository.yaml` + `arr-stack/config.yaml`.
3. If `image:` is set, Supervisor **pulls** `ghcr.io/esper256/arr-stack:<version>` for the host arch.
4. If `image:` is absent (local `/addons` clone), Supervisor **builds** the Dockerfile on the device. That is acceptable for development and too slow/fragile for real users (HA’s own publishing docs say this).
5. User drops a Proton WireGuard config into the addon config share (or pastes the fields), starts the app. cont-init installs `wg0`, applies the kill switch, then starts enabled apps. First-run bootstrap (later milestone) can register qBittorrent on `127.0.0.1` inside Sonarr/Radarr/Prowlarr.

*arr binaries are **baked into the image** at build time. We will not `curl` GitHub releases on every container start.

### Updates

| What changes | How the user gets it |
| --- | --- |
| Sonarr/Radarr/Prowlarr/Bazarr/qBittorrent version | We bump the image, bump `config.yaml` `version` to match the image tag, user clicks Update in HA |
| HA options schema / nginx / VPN scripts | Same: new addon version |
| Proton server | User generates a new WireGuard config in Proton’s dashboard and replaces the file |

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
- **Native OpenVPN/WireGuard inside the qBittorrent addon.** Proof that `NET_ADMIN` + `/dev/net/tun` is allowed on HAOS, that AppArmor must be taught about `tun`, and that Gluetun is not required in a single-container app. Two bind modes: container-level vs app-level. We will do **both**, with iptables as the real kill switch (their VPN path is weaker).
- **WireGuard config as a file in addon_config.** Same credential shape Proton already gives the user.
- **`env_vars` passthrough.** Escape hatch for linuxserver knobs we do not want to promote into first-class schema.
- **GHCR images named per addon + `updater.json`.** A bot watches upstream tags and opens version bumps. We should pin versions and automate bumps once the image exists; we should not silently float `latest`.
- **SMB/local disk mount scripts.** Useful on HAOS where the host is an appliance. **Not v1** — those scripts want `SYS_ADMIN` + `DAC_READ_SEARCH` (another security hit) and a huge `devices:` list. Prefer HA’s native Settings → Storage mounts into `/media` and `/share`.

Issues they ran into that we must treat as ours:

| Issue | Their world | Our fix |
| --- | --- | --- |
| Cannot put the whole stack behind one VPN namespace | One container per addon; Supervisor will not share namespaces. Only qBittorrent got a bolted-on VPN. Gluetun+PF feature request sat for years ([#1661](https://github.com/alexbelgium/hassio-addons/issues/1661)) | One container, native `wg0`, shared by every process |
| Proton port forwarding | Not implemented on the qBittorrent addon | `natpmpc` → qBittorrent `listen_port` |
| Weak kill switch | App-level bind and/or OpenVPN inside one app | iptables OUTPUT drop independent of the route table |
| *arr `UrlBase` lowercasing vs Ingress tokens | They rewrite around it | Same nginx `sub_filter`, plus `$http_x_ingress_path` so we do not freeze a token |
| Hardlinks fail if downloads and libraries are on different mounts | User education | DOCS.md TRaSH layout; keep both under `/media` when possible |
| Fat `devices:` / `privileged` for USB disks and SMB | Convenience on HAOS | Skip in v1; document HA Storage |
| linuxserver `/config` vs HA `/data/options.json` | `ha_lsio.sh` remaps paths | Never use stock linuxserver as the **final** image; pass explicit `-data=/config/<app>` |
| In-app updaters fighting Supervisor | Package info sets `UpdateMethod=docker` | Keep that; never run `Sonarr.Update` |
| Image size / Pi wear | One moderate image per app, pulled from GHCR | One larger image, still pulled, never built on-device |
| Cross-app wiring | User types `http://a0d7b954-sonarr:8989` style hostnames (repo hash prefixes) | `127.0.0.1` and optional first-run bootstrap |

### Other related projects (not HAOS addons, still informative)

- **binhex `*vpn` images / haugene transmission-openvpn.** Precedent for “VPN client + payload in one image” using native OpenVPN/WG, not Gluetun. Closest cousin to this design.
- **hotio / linuxserver Compose stacks.** The correct answer on a NAS. Irrelevant as a deploy mechanism on HAOS; still the source of binaries and folder conventions.
- **Gluetun Compose stacks.** Correct when you have many *containers*. Redundant when you have many *processes* in one container.
- **abarbarich/arrstack, corelab generators.** Compose + optional Gluetun sidecar for qBittorrent only. Confirms the usual split-tunnel; we are deliberately stricter.
- **martinargalas Arr Stack Card.** HA *dashboard* for already-running *arr APIs. Complementary later, not a substitute for running the apps.
- **Official HA `apps-example`.** Repository shape, builder workflows, `image:` + matching `version`, no `build.yaml` (deprecated Supervisor 2026.04). We follow that, not the 2024-era per-arch `build.yaml` files still sitting in older addon repos.

## Implementation phases

1. **Skeleton.** Repo metadata, design doc, `arr-stack` app with HA options, WireGuard + kill switch + nginx launcher, GHCR workflow. *arr processes are stubs.
2. **VPN bring-up.** Real Proton `wg-quick`, handshake check, leak test, AppArmor.
3. **qBittorrent.** Bind to `wg0`, NAT-PMP hook, Ingress location.
4. **Prowlarr / Sonarr / Radarr.** linuxserver copy stages, UrlBase, localhost download client.
5. **Bazarr + first-run bootstrap + GHCR `image:` in `config.yaml`.**
6. **Hardening.** Automated leak test in CI (container with a mock `wg0`), changelog discipline, maybe SMB mounts if needed.

## Open questions (do not block the skeleton)

- Exact linuxserver tag pin vs copying `/app` from their `latest` at build time (lean toward pinned versions).
- Whether Jellyseerr belongs in a later version of this image (it needs LAN access to Jellyfin; VPN is optional).
- OpenVPN as a second tunnel type if someone cannot use Proton WireGuard.
- Whether to publish a tiny HA sensor for handshake age / forwarded port (localhost files, not a Gluetun control server).
