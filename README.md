# Pompey

**Pompey** is the Home Assistant app for the whole stack: one sidebar entry, one search, titles land in the right library and Plex updates.

The face of that search is [Seerr](https://seerr.dev/). We do not reinvent it. Pompey’s job is the box around it — Proton, kill switch, hidden engines, no extra consoles. [VISION.md](VISION.md) is the plan.

We do not publish a container image. Supervisor builds this app on your machine. After the tunnel is up, the app fetches the official programs it needs.

## App

| Folder | Status |
| --- | --- |
| [`pompey/`](pompey/) | Experimental. Proton WireGuard + kill switch + runtime fetch of Seerr and hidden engines. |

## Try it on a Home Assistant OS machine

1. Copy `pompey/` into `/addons`.
2. Settings → Apps → Check for updates.
3. Put a Proton WireGuard config in the app’s config share (or paste the fields).
4. Fill Plex address + token and one source (URL plus key).
5. Start **Pompey** and open the UI. First start downloads through the tunnel and can take several minutes.

Supervisor builds the Dockerfile locally. That is the only delivery path.

Tests do not need Home Assistant OS. They supply the same `options.json` Supervisor would write:

```bash
bash tests/run.sh
```

