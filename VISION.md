# Pompey

**Pompey** is the name of the whole stack and of the Home Assistant app. One sidebar entry. The household searches for a movie or TV show, picks the right title, and it shows up in the right library. Nobody using it should need operator jargon.

The name is a wink at Pompey — English for Gnaeus Pompeius Magnus — who cleared the Mediterranean of pirates in about forty days, then settled them as farmers. Same trick: the rough tools keep working behind the wall. The household never visits their ports.

## Do not vibe-code the face

[Seerr](https://seerr.dev/) (the 2026 merger of Overseerr and Jellyseerr) is already the polished search / posters / “is it already on Plex?” / request-and-notify UI. A half day of our own search bar will lose to that, obviously. We use their work as the thing you look at. We do not fork it to add a download console; they have spent years refusing to be that.

Seerr is a **request desk**. Search, pick a title, queue, tell Plex’s friends when it is available. Approved requests go to other teams’ engines, which auto-grab from a quality profile. Interactive “pick this file” was asked for and left to die. Routing a title into Kid Friendly vs General by rating is something their users still write webhook scripts for.

So the honest split:

| Step | Who |
| --- | --- |
| Search, posters, pick the title, see if it is already there | **Seerr** (Ingress) |
| Find a release, download, match episodes, land on the NAS | Hidden engines, fetched at runtime |
| All of that on Proton, one box, no extra sidebars | **Pompey** (this app) |
| “This file is popular but the wrong quality” | Not Seerr. Only if we add a small confirm later. |
| Kid-friendly vs general by rating | Not Seerr. A tiny rule after the request, or we ask. |

The family experience of a glued stack **is** Seerr. That is a better first screen than anything we would sketch. Pompey's job is to make that screen the *only* screen, already wired, on a kill-switched tunnel, without a weekend of operator setup.

## What you see

1. **Search** — Seerr. Type a title or browse.
2. **Pick** — the right movie or show.
3. **Request** — it becomes a job. Auto-approve for the household so there is no ticket queue.
4. **Done** — it shows up in Plex. Kid-friendly vs general is our rule on the way to disk, not a Seerr settings page.

Movies and TV use the same search.

## What you never see

Indexer consoles, quality-profile spreadsheets, calendars, download-client dashboards, or Seerr's "connect Radarr" wizard. Pompey fills those connections. Those tools stay other teams’ moving targets; we download their official binaries and run them.

## How it ships

We do **not** publish a container image to Docker Hub, GHCR, or anywhere else.

Home Assistant OS builds a thin Dockerfile on the machine: WireGuard, kill switch, a little supervisor of our own. No posters, no other teams’ programs in the image.

After the tunnel is up, Pompey downloads official upstream Linux releases onto the config share and starts them. Seerr is the Ingress UI. The rest listen on localhost. We unpack and run. We do not compile their source, and we do not host their bits.

Title art still comes from metadata CDNs at search time.

## Where it runs

One Home Assistant OS app, one container. That is why this is not five community addons glued in the store: Home Assistant OS will not attach those addons to one VPN network namespace, and each addon grows its own sidebar. All internet from this container leaves through Proton WireGuard (`wg0`). If the tunnel is down, internet is dropped. Home LAN (Plex, NAS) is allowed.

Challenge-solver sidecars are not in this starting point.

## This repo

`pompey/` is the Home Assistant app for the whole stack. Right now: Proton handshake, kill switch, and a placeholder screen. Next: fetch and wire Seerr as Ingress, engines on localhost.
