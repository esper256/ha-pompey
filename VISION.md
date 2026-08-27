# Pompey

A Home Assistant app. You search for a movie or TV show. You pick the right title. If we need a decision, we ask in plain language. Then it lands in the right library on your NAS and Plex notices.

Nobody using this should need operator jargon.

The name is a wink at **Pompey** — English for Gnaeus Pompeius Magnus, who in 67 BC cleared the Mediterranean of pirates in about forty days, then **settled them as farmers** instead of making a show of wiping them out. That is the trick here too: the rough tools keep working behind the wall. The household never visits their ports.

## What you see

1. **Search** — type a title.
2. **Pick** — the right movie or show (poster, year, a sentence).
3. **Confirm** — only when we are unsure which file is the good one (for example a popular encode that is the wrong quality, versus the right quality with almost no sources).
4. **Done** — it shows up in the library that matches the rating. Kid-friendly titles go to a kid-friendly library. Unknown ratings ask; they never guess.

Movies and TV use the same search.

## What you never see

Indexer consoles, quality-profile spreadsheets, calendars, or a download-client dashboard. Those tools are other teams’ moving targets. We reuse their engines behind this UI. They are not the product.

## How it ships

We do **not** publish a container image to Docker Hub, GHCR, or anywhere else.

Home Assistant OS builds the Dockerfile on the machine (the usual local-app path). That image stays thin: this search UI, nginx, WireGuard, the kill switch. No posters, no other teams’ programs.

What the app needs later, it **downloads at runtime**, after the tunnel is up:

- Title art and metadata from their CDNs, when you search.
- Official upstream Linux binaries for the hidden engines, unpacked onto the config share and run. We do not compile their source, and we do not host their bits.

## Where it runs

One Home Assistant OS app, one container. All internet leaves through Proton WireGuard (`wg0`). If the tunnel is down, internet is dropped — metadata lookups and those engine downloads included. Your home LAN (Plex, NAS) is allowed so files can land and Plex can be told to scan. There is no split tunnel.

Challenge-solver sidecars are not in this starting point.

## This repo

`pompey/` is the app: the search screen, a Proton handshake, and a kill switch. Search is not connected yet. That is the next job.
