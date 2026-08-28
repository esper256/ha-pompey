# AGENTS.md

This file is for Cursor cloud agents. Household operators should read [DOCS.md](pompey/DOCS.md). The product plan is [VISION.md](VISION.md).

## Short answer (why this file mentions Docker)

Starting `dockerd` in this Cursor VM is **not** how the household runs Pompey, and it is **not** a stand-in for Home Assistant OS.

In the real world, **Home Assistant Supervisor** is the container host. It builds `pompey/Dockerfile` on the user’s machine and starts **one** addon container. The operator never types `docker`, never starts `dockerd`, and we never publish an image to Docker Hub or GHCR.

In this Cursor VM there is no Supervisor. Starting Docker here is only so an agent can **compile the same Dockerfile** Supervisor will compile (syntax, packages, `COPY` paths). A full `docker run` of that image still dies without bashio + Proton. Prefer the no-Docker tests below for day-to-day agent work.

## Real product vs agent testability

| | Household (HAOS) | Agents in this VM / CI |
| --- | --- | --- |
| Who builds the image | Supervisor, from `/addons/pompey` | Optional `docker build`. GitHub Actions Builder does the same compile with `push: false`. |
| Who starts the container | Supervisor (one container, `NET_ADMIN`, `/dev/net/tun`) | Nobody, unless an agent is checking the Dockerfile. The operator never starts it. |
| `dockerd` | Already the HAOS host. Not something Pompey starts. | Optional, this VM only. Packages persist; the daemon process does not. |
| Config | Supervisor writes `/data/options.json` from the app options UI | We supply [`tests/options.json`](tests/options.json) ourselves |
| VPN / internet | All traffic on Proton `wg0`, else dropped | **Fake `wg0`**: a veth in netns `pompey-dev` that NATs out `eth0`. Same interface name the download engine binds. Not Proton. Set `POMPEY_FAKE_VPN=1`. Do **not** apply the OUTPUT DROP kill switch here (it would kill the agent). Keep host DNS. |
| What the household sees | Wait screen, then Seerr on Ingress | [`tests/preview.py`](tests/preview.py) is the wait screen. Seerr’s image is Alpine/musl; if you unpack it, run it with the host glibc `node`. |
| Engines | musl tarballs after the tunnel is up | glibc (`os=linux`) tarballs on Ubuntu, cached under `~/.cache/pompey/engines`. Tests skip unpacking the torrent client (`POMPEY_SKIP_QBIT=1`). `tests/run.sh` unpacks a Prowlarr-shaped fixture and the real linux-musl Prowlarr `.tar.gz` (cached under `~/.cache/pompey/artifacts`) so HAOS `/tmp` chmod failures and Windows zips are caught without a Supervisor rebuild. |
| Sources / Plex | Operator URL+key and a real Plex | Empty source + empty Plex token. Tests never speak BitTorrent. |

Shipping path: copy `pompey/` into `/addons`. Supervisor builds locally. That is the only delivery path.

## What the real add-on does

1. Supervisor builds `pompey/Dockerfile` (WireGuard, nginx, crane, our scripts). Engines are **not** in that image.
2. Supervisor starts one container and writes `/data/options.json`.
3. Our scripts bring up Proton, apply the kill switch, fetch Seerr + hidden engines onto `/data`, wire them on localhost, then flip Ingress from the wait screen to Seerr.

s6-overlay: `rootfs/etc/cont-init.d/*` once, then `rootfs/etc/services.d/*`.

`wire-stack` must exit non-zero on a required miss (Prowlarr apps, source indexer when a URL is set, Seerr API key from `settings.json`, Seerr→Radarr/Sonarr, qBittorrent category other than 409). s6 retries; the wait screen stays up. Plex login, Seerr local login (real Seerr 403s until the wizard creates a user), and Seerr chrome settings are optional. Do not log-and-continue on a required step — that flipped Ingress to a hollow search UI.

## What agents should run here (no HAOS)

Fast, no Docker. CI unpacks a cached Prowlarr linux-musl tarball (not a torrent client):

```bash
bash tests/run.sh              # options.json + bashio stub + fake engines + Prowlarr unpack + fake-wg0 smoke
python3 tests/preview.py       # wait-screen progress UI at http://127.0.0.1:8099/
```

Realistic stack (still no Proton, still no HAOS). Needs passwordless sudo + `iproute2` for a veth named `wg0`:

```bash
sudo apt-get install -y iproute2 iptables libicu74
bash tests/integration.sh      # The Wild Robot via Radarr TMDB lookup, Prowlarr sync of a fake Torznab source into Radarr/Sonarr, then Prowlarr search until the fake qBittorrent WebUI records the magnet add
```

That downloads official Radarr/Sonarr/Prowlarr into `~/.cache/pompey/engines` on first run. It looks the movie up on TMDB and adds it to Radarr **without searching or downloading from the internet**. A fake Torznab source (`tests/lib/fake_source.py`) answers Prowlarr (movie hit for The Wild Robot; a dummy TV item so Sonarr's indexer test passes). The test fails if Prowlarr does not sync that source into Radarr/Sonarr, then ends when the fake qBittorrent WebUI records `torrents/add` for the movie magnet. Do not start the torrent client, do not wait on peers, and do not add that wait to CI (`tests/run.sh` only). Seerr unpack is skipped unless `POMPEY_SKIP_SEERR=0` and `crane` is on PATH (then use host `node`, not Alpine’s).

Opening `index.html` as a file is only the wait screen.

### Optional: compile the Dockerfile (not a product boot)

If `docker info` fails, start the daemon. This is a quirk of **this cloud box**, not of Pompey:

```bash
sudo dockerd >/tmp/dockerd.log 2>&1 &
```

This VM may need `/etc/docker/daemon.json` with `storage-driver: fuse-overlayfs` and `features.containerd-snapshotter: false`. Do not treat that file as part of the add-on.

```bash
docker build --build-arg BUILD_ARCH=amd64 -t local/pompey:dev pompey/
```

A full `docker run` of that image still dies without bashio + Proton (see `00-banner.sh`) unless you pass `POMPEY_FAKE_VPN=1` and still stub bashio. Do not spend the session trying to fake Supervisor.

To peek at the wait HTML from inside the image, run only nginx and relax Ingress `allow 172.30.32.2`. That is a screenshot helper, not the household search UI.

## Lint

```bash
shellcheck pompey/rootfs/etc/cont-init.d/*.sh \
  pompey/rootfs/etc/services.d/*/run \
  pompey/rootfs/etc/services.d/*/finish \
  pompey/rootfs/usr/local/bin/*
```

Scripts use `#!/command/with-contenv bashio` plus `# shellcheck shell=bash` (`pompey-dev-vpn` is plain bash).
