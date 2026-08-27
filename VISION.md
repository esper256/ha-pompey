# Find

A Home Assistant app. You search for a movie or TV show. You pick the right title. If we need a decision, we ask in plain language. Then it lands in the right library on your NAS and Plex notices.

Nobody using this should need operator jargon.

## What you see

1. **Search** — type a title.
2. **Pick** — the right movie or show (poster, year, a sentence).
3. **Confirm** — only when we are unsure which file is the good one (for example a popular encode that is the wrong quality, versus the right quality with almost no sources).
4. **Done** — it shows up in the library that matches the rating. Kid-friendly titles go to a kid-friendly library. Unknown ratings ask; they never guess.

Movies and TV use the same search.

## What you never see

Indexer consoles, quality-profile spreadsheets, calendars, or a download-client dashboard. Those tools are other teams’ moving targets. We will reuse their engines behind this UI. They are not the product.

## Where it runs

One Home Assistant OS app, one container. All internet leaves through Proton WireGuard (`wg0`). If the tunnel is down, internet is dropped — metadata lookups included. Your home LAN (Plex, NAS) is allowed so files can land and Plex can be told to scan. There is no split tunnel.

Challenge-solver sidecars are not in this starting point.

## This repo

`find/` is the app: the search screen, a Proton handshake, and a kill switch. Search is not connected to anything yet. That is the next job.
