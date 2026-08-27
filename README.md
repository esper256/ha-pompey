# Pompey

**Pompey** is the Home Assistant app for the whole stack: one sidebar entry, one search, titles land in the right library and Plex updates.

The face of that search is [Seerr](https://seerr.dev/). We do not reinvent it. Pompey’s job is the box around it — Proton, kill switch, hidden engines, no extra consoles. [VISION.md](VISION.md) is the plan.

We do not publish a container image. Supervisor builds this app on your machine.

## App

| Folder | Status |
| --- | --- |
| [`pompey/`](pompey/) | Experimental. Proton WireGuard + kill switch. Seerr is not wired yet. |

## Try it on a Home Assistant OS machine

1. Copy `pompey/` into `/addons`.
2. Settings → Apps → Check for updates.
3. Put a Proton WireGuard config in the app’s config share (or paste the fields).
4. Start **Pompey** and open the UI.

Supervisor builds the Dockerfile locally. That is the only delivery path.
