# AGENTS.md

## Cursor Cloud specific instructions

### What this repo is

Pompey is a **Home Assistant OS add-on** (see [VISION.md](VISION.md), [`pompey/`](pompey/)).
The "application" is the add-on image defined by [`pompey/Dockerfile`](pompey/Dockerfile),
which HA Supervisor builds locally on the target machine. There is no package manager,
no lockfile, and no automated test suite. The runtime is s6-overlay: `rootfs/etc/cont-init.d/*`
run once at startup, then `rootfs/etc/services.d/*` (wireguard, natpmp, nginx) are supervised.
The user-facing "face" is nginx serving `pompey/rootfs/usr/share/pompey/index.html` over HA Ingress.

### Toolchain (already installed in the VM snapshot)

- `docker` (CE 29.x) — used to build the add-on image, exactly like CI and HA Supervisor.
- `shellcheck`, `yamllint` — lint the bash scripts and YAML.
- The `ubuntu` user is in the `docker` group (persisted), so `docker` works without `sudo`
  once the daemon is running.

### Start the Docker daemon each session (not persisted across VM restarts)

The docker *packages* persist in the snapshot, but the `dockerd` process does not. Before
building or running, start it (idempotent — skip if `docker info` already works):

```bash
sudo dockerd >/tmp/dockerd.log 2>&1 &   # or run in a tmux session
```

`/etc/docker/daemon.json` is preconfigured for this VM with `storage-driver: fuse-overlayfs`
and `features.containerd-snapshotter: false` (required for Docker 29 + fuse-overlayfs here).
Do not remove that config or builds will fail.

### Lint

```bash
cd pompey
shellcheck rootfs/etc/cont-init.d/*.sh rootfs/etc/services.d/*/run \
  rootfs/etc/services.d/*/finish rootfs/usr/local/bin/wait-for-vpn \
  rootfs/usr/local/bin/vpn-killswitch
```

The scripts use the `#!/command/with-contenv bashio` shebang plus `# shellcheck shell=bash`,
so shellcheck lints them as bash.

### Build (the real build path — same as CI `.github/workflows/builder.yaml`)

```bash
cd pompey
docker build --build-arg BUILD_ARCH=amd64 -t local/pompey:dev .
```

### Run

Fully booting the add-on requires the **HA Supervisor runtime** (bashio, Ingress, and
`/data/options.json`) **and Proton WireGuard credentials** plus `/dev/net/tun`. Without a
WireGuard config, `rootfs/etc/cont-init.d/00-banner.sh` calls `bashio::exit.nok` and the
container halts (this is the intended kill-switch behavior), so you cannot fully boot it
standalone in this VM.

To exercise the user-facing search UI without Supervisor/Proton, run only nginx from the
built image against `/usr/share/pompey`. Note `rootfs/etc/nginx/http.d/ingress.conf` uses a
`%%port%%` placeholder (substituted at runtime by `20-nginx.sh`) and restricts access to the
Supervisor IP `172.30.32.2`, so relax those for a local port:

```bash
docker run -d --name pompey-ui -p 8099:8099 --entrypoint sh local/pompey:dev -c '
  mkdir -p /run/nginx
  sed -e "s/%%port%%/8099/" -e "s/allow 172.30.32.2;/allow all;/" -e "/deny all;/d" \
    /etc/nginx/http.d/ingress.conf > /tmp/i.conf && cp /tmp/i.conf /etc/nginx/http.d/ingress.conf
  exec nginx -g "daemon off;"'
# then: curl http://localhost:8099/
```
