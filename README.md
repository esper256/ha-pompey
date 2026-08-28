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
5. Start **Pompey** and open the UI. First start downloads through the tunnel and can take several minutes. The wait screen shows which step is running (tunnel, download, start, connect), then reloads into search. If it stays on the tunnel step, Proton is not up yet.

Supervisor builds the Dockerfile locally. That is the only delivery path.

This Cursor/cloud VM is not Home Assistant OS, so the addon cannot be installed here. To see the wait screen with a live progress bar:

```bash
python3 tests/preview.py
```

Then open http://127.0.0.1:8099/ . That is the wait UI, not Seerr — Seerr only runs inside the Home Assistant Alpine container after Proton is up.

Tests do not need Home Assistant OS. They supply the same `options.json` Supervisor would write:

```bash
bash tests/run.sh
```

A longer run (`bash tests/integration.sh`) starts a fake `wg0`, downloads the official TV/movie engines, and has Radarr look up *The Wild Robot* on TMDB. It does not start a torrent client and does not wait on a download. That is still not Home Assistant OS and still not Proton.

Agents: [AGENTS.md](AGENTS.md) (what Docker is for in this VM vs what HAOS does).


