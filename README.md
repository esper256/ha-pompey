# Find

A Home Assistant OS app: one search bar, a short confirmation when we need you, then the title lands in the right library and Plex updates.

[VISION.md](VISION.md) is the whole plan.

## App

| Folder | Status |
| --- | --- |
| [`find/`](find/) | Experimental. Search screen + Proton WireGuard and kill switch. Search is not connected yet. |

## Try it on a Home Assistant OS machine

1. Copy `find/` into `/addons`.
2. Settings → Apps → Check for updates.
3. Put a Proton WireGuard config in the app’s config share (or paste the fields).
4. Start **Find** and open the UI.

Until `find/config.yaml` sets `image:`, Supervisor builds the Dockerfile locally. That is the development path.
