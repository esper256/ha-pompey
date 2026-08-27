# Pompey

A Home Assistant OS app: one search bar, a short confirmation when we need you, then the title lands in the right library and Plex updates.

[VISION.md](VISION.md) is the whole plan. We do not publish a container image; Supervisor builds this app on your machine.

## App

| Folder | Status |
| --- | --- |
| [`pompey/`](pompey/) | Experimental. Search screen + Proton WireGuard and kill switch. Search is not connected yet. |

## Try it on a Home Assistant OS machine

1. Copy `pompey/` into `/addons`.
2. Settings → Apps → Check for updates.
3. Put a Proton WireGuard config in the app’s config share (or paste the fields).
4. Start **Pompey** and open the UI.

Supervisor builds the Dockerfile locally. That is the only delivery path.
