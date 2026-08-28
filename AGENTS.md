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
| VPN / internet | All traffic on Proton `wg0`, else dropped | No Proton, no `/dev/net/tun` — the real stack will not boot |
| What the household sees | Wait screen, then Seerr on Ingress | [`tests/preview.py`](tests/preview.py) is the wait screen only. Seerr is Alpine/musl and does not run on this Ubuntu VM. |
| Engines | Fetched at runtime onto `/data` after the tunnel is up | Fake HTTP stubs in [`tests/run.sh`](tests/run.sh) |

Shipping path: copy `pompey/` into `/addons`. Supervisor builds locally. That is the only delivery path.

## What the real add-on does

1. Supervisor builds `pompey/Dockerfile` (WireGuard, nginx, crane, our scripts). Engines are **not** in that image.
2. Supervisor starts one container and writes `/data/options.json`.
3. Our scripts bring up Proton, apply the kill switch, fetch Seerr + hidden engines onto `/data`, wire them on localhost, then flip Ingress from the wait screen to Seerr.

s6-overlay: `rootfs/etc/cont-init.d/*` once, then `rootfs/etc/services.d/*`.

## What agents should run here (no HAOS)

Preferred — no Docker, no Supervisor:

```bash
bash tests/run.sh              # options.json + bashio stub + fake engines
python3 tests/preview.py       # wait-screen progress UI at http://127.0.0.1:8099/
```

That is enough to test wiring, status, and the wait screen. It is **not** Seerr and **not** a Proton tunnel. Opening `index.html` as a file is also only the wait screen.

### Optional: compile the Dockerfile (not a product boot)

If `docker info` fails, start the daemon. This is a quirk of **this cloud box**, not of Pompey:

```bash
sudo dockerd >/tmp/dockerd.log 2>&1 &
```

This VM may need `/etc/docker/daemon.json` with `storage-driver: fuse-overlayfs` and `features.containerd-snapshotter: false`. Do not treat that file as part of the add-on.

```bash
docker build --build-arg BUILD_ARCH=amd64 -t local/pompey:dev pompey/
```

A full `docker run` of that image still dies without bashio + Proton (see `00-banner.sh`). Do not spend the session trying to fake Supervisor.

To peek at the wait HTML from inside the image, run only nginx and relax Ingress `allow 172.30.32.2`. That is a screenshot helper, not the household search UI.

## Lint

```bash
shellcheck pompey/rootfs/etc/cont-init.d/*.sh \
  pompey/rootfs/etc/services.d/*/run \
  pompey/rootfs/etc/services.d/*/finish \
  pompey/rootfs/usr/local/bin/*
```

Scripts use `#!/command/with-contenv bashio` plus `# shellcheck shell=bash`.
