# Replacing Radarr and Sonarr

This is a design note, not a roadmap item. It exists because Arr search policy is a poor fit for some household titles (old anime season packs, Dual-Audio batches a human finds in one query), and it is fair to ask whether those two .NET processes still earn their keep.

**Recommendation:** do not replace them next. Keep Seerr as the face and Prowlarr as the source console. Shape Arr where an official knob exists (season search, Anime Standard Format Search, Recyclarr scores). If we want more search control than that, take **search** away from Arr first and leave Arr as the matcher/importer. A Seerr-facing fake Arr that also does grab/match/import at today’s quality is a new product, larger than the glue we already wrote.

## What Arr actually does here

The household never opens Radarr or Sonarr unless Debug is on. They still sit on the only path from a Seerr request to a file on the NAS.

```text
Seerr request
    → POST /movie or /series on localhost Arr (profile, root, searchNow)
        → Arr builds indexer queries (Prowlarr Torznab, title-search proxy on :9698)
            → picks a release (quality + custom formats + seeds)
                → qBittorrent category radarr / sonarr
                    → completed-download handling + ManualImport
                        → library path Seerr stored (Kid / Not Kid / By Rating)
                            → Seerr hasFile / radarr-scan / sonarr-scan
                                → Plex
```

Pompey already owns Proton, qBittorrent bind/NAT-PMP, root folders, Max/Default/Anything names, Recyclarr, housekeep after import, and “close the Seerr request → stop upgrades.” Arr owns the middle: **what to ask the indexer, which torrent is that title, which file is that episode.**

`wire-stack` is ~4,600 lines, most of it an Arr HTTP client plus import cleanup. `tests/test_python.py` is ~5,600 lines of fakes for that client. Recyclarr is another 260. That is glue around engines, not a replacement for them.

## Fooling the rest of the system

“Mock them out” really means **implement enough of `/api/v3` that Seerr never notices.** Seerr does not have a Pompey plugin. It has a Radarr server and a Sonarr server (`configure_seerr` in `wire-stack`: host `127.0.0.1`, ports 7878 / 8989, profile names, By Rating directories, `preventSearch: false`).

Seerr’s Arr client (the `server/api/servarr/*.ts` files) needs at least:

| Call | Why Seerr needs it |
| --- | --- |
| `GET /system/status` | Connection test on the settings page |
| `GET /qualityprofile`, `GET /rootfolder` | Max / Default / Anything and Kid / Not Kid / By Rating |
| `GET /movie/lookup?term=tmdb:…`, `GET /series/lookup` | Request add; “already there?” |
| `GET/POST/PUT /movie`, `GET/POST/PUT /series` | The request *is* this write |
| `GET /episode` (Sonarr) | Season/episode monitor and availability |
| `POST /command` (`MoviesSearch`, `EpisodeSearch`, `SeasonSearch`, `SeriesSearch`) | Search-now on add; Seerr also re-searches |
| `GET /queue`, `GET /history` | Request status / “processing” |
| Movie/series `hasFile`, `statistics`, `movieFile` / `episodeFile` | Availability after import |

Prowlarr is a second client: `ApplicationIndexerSync` PUTs Torznab rows onto Arr. Recyclarr is a third: it writes Default/Max custom formats onto the same API. Debug Ingress is a fourth: the real Arr SPA.

A facade that only answers Seerr’s handshake is a few thousand lines and **does not download anything**. A facade that also fools Prowlarr and Recyclarr is more API surface for no household gain — if we own search, we should call Prowlarr’s `/api/v1/search` ourselves and drop Recyclarr. If we keep Debug Arr UIs, we have not replaced Arr.

## What we would gain

These are real, and they are why the question is reasonable.

**Search we can actually write.** Arr’s Anime type has no season packs unless Anime Standard Format Search is on *and* the command is a season search. Episode search will not query `World Trigger Dual` or a batch named `World Trigger S1`. We cannot add that query without fighting ReleaseSearchService. Our own picker can search `World Trigger` once, prefer seeded packs and Dual-Audio in the *query or the ranker*, and stop. That is the World Trigger failure mode.

**Less indexer traffic by default.** One title search plus a pack query is a policy we can state. Arr will keep walking 73 absolute episodes on eight public sources unless upstream changes.

**RAM and moving parts on HAOS.** Two Servarr processes, two SQLite trees, Recyclarr, daily TRaSH clone, fetch-engines for Radarr/Sonarr, and the brittleness map in [arr-auto-update.md](arr-auto-update.md) (`/api/v3` vs `v4`, ManualImport body, command names, quality name strings). Dropping those binaries is the largest memory win left in the container.

**Kid / quality / language as ours.** Today we express those as Arr roots, Recyclarr trash ids, and a +15 Dual-Audio tie-break. A native engine can make Dual-Audio a search preference, keep “Anything” as “take the seeded file,” and never clone HD-1080p leftovers into Seerr.

**One debug story.** Interactive Search, command queue, wanted/missing — we already wrap those for the app log. A Pompey job list was already roadmap item 4.

None of that requires forking Seerr. The face stays posters and a request.

## What Arr brings that we would have to reimplement

This is the part that makes a “small mock” a lie if the bar is “do not backslide.”

**Release parsing.** Scene / P2P / anime names → title, year, season, episode, absolute episode, group, quality, Dual-Audio, proper. Servarr has years of this. `guessit` and friends miss anime batches, multi-episode packs, and “is this the same show.” Getting this wrong is silent: the wrong movie in the folder, or S01E01 overwritten by a special. Pompey already refuses to guess Kid vs Not Kid from a filename; we lean on Arr’s title record for a reason.

**Which file is which episode.** Season packs, flat folders, `Season 00`, extras we now relocate for Plex, samples, ISO/remux rejects. Housekeep’s ManualImport path is the household bug magnet (`docs/arr-auto-update.md`). Replacing Arr means we own that matcher. Movies are one file. TV is the hard half.

**Metadata graph.** TMDB/TVDB lookup, season counts, anime absolute numbers, XEM scene mapping, specials, “ended” vs airing. Seerr already talks to TMDB for posters; Arr talks to Skyhook/TVDB for the episode list that `hasFile` is counted against. A shim that returns `hasFile: true` after one pack lands still has to know there are 73 episodes so Seerr can show the show as available.

**Choosing among releases.** TRaSH scores, original-audio CF, cutoff, upgrade-until-score, min seeders, indexer priority, rejected hashes, “this is a disk / remux / CAM.” We already chose not to vibe-code that spreadsheet and outsourced it to Recyclarr. A native ranker that is “sort by peers, prefer Dual, prefer 1080p” is *better than Arr on World Trigger* and *worse than Arr on a messy WEB-DL vs encode vs remux movie.* Anything vs Default vs Max has to keep meaning something.

**Indexer dialect.** Newznab vs raw `q=`, caps, category tests, anime categories, ID vs title search (the reason `:9698` exists), 429 backing off, “source failed Prowlarr’s test so it never landed in Arr.” Prowlarr’s search API hides some of this. Not all of it.

**RSS and airing shows.** A weekly episode is a different problem than an old batch. Arr’s RSS is how “the next episode just appeared” happens without another 1,000 queries. We would need a timer and a last-seen, or we only handle catalog requests and fail at currently-airing TV.

**The bugs we already paid for.** Free space on CIFS, `copyUsingHardlinks`, recycle bin on upgrade, qbit v4 `pause` vs v5 `stop`, Seerr 403 until user id 1, ManualImport omitting `hasFile`, extras left in `complete/`. A rewrite starts that meter at zero.

## How big an MVP is

Three different jobs get sold as one “mock.”

| Job | What ships | Python + tests (order of magnitude) | Backslide? |
| --- | --- | --- | --- |
| **A. Facade** | Seerr settings stay green. No grab. | 1.5–3k | Yes. Search is theater. |
| **B. Movies only** | Seerr → our API → Prowlarr search → qbit → rename one file → `hasFile`. The Wild Robot loop. | 4–8k | TV/anime worse or gone. |
| **C. No-backslide household** | B, plus TV seasons, anime packs, specials/extras, By Rating, Max/Default/Anything, Dual-Audio/original, upgrade until the Seerr request closes, airing RSS or an honest “catalog only,” Plex extras we already place. | 12–25k of *ours*, plus a parser we will get wrong | This is the real bar. Larger than today’s `wire-stack` + its tests. We become the engine. |
| **D. Arr parity** | TRaSH-depth scoring, MediaInfo, XEM, Interactive Search, every scene exception. | 50k+ | We have rebuilt Servarr, worse. |

Those are line counts, not calendar guesses. C is the only MVP that matches “enough to not backslide on what we already have.” It is not a weekend shim. It is a second hidden product beside Seerr and Prowlarr.

What we could delete if C worked: Radarr, Sonarr, Recyclarr, `prowlarr-arr-proxy`, most of `fetch-engines` for those three, Debug Arr Ingress, and a large fraction of `wire-stack`’s Arr client. What we would keep: Seerr, Prowlarr, qBittorrent, Proton, housekeep-for-qbit, `route-rating` (or fold it in).

## A smaller move if search is the pain

Arr is a bad **query planner** for old anime and a good **filename accountant**. Those can be split.

1. Leave Radarr/Sonarr running.
2. Set Seerr `preventSearch: true` (or ignore Arr’s automatic search).
3. On an open request, Pompey calls **Prowlarr search** with household queries (title, title+season, title+Dual when Default/Max).
4. Pick a release with a short, documented ranker.
5. Add it to qBittorrent with the Arr category.
6. Let Arr’s completed-download handling and ManualImport do what they already do.

That is roughly 1–3k lines on top of housekeep, no Seerr fork, no fake `/api/v3`, and it is the same escape hatch as Debug Interactive Search — automated. Failure mode is “our ranker picked a bad torrent” (Arr still rejects unparsable names) rather than “we imported episode 7 as episode 1.”

Season-pack search via official knobs (**0.2.51**) is the still-smaller move and should be the first thing the household tries on World Trigger.

## Can we reuse Servarr’s scene parser?

There is no NuGet, no shared `Servarr.Parser` package, and no blessed extract. The regex engine lives in `NzbDrone.Core/Parser/Parser.cs` inside each app. Sonarr’s copy is TV/anime (absolute numbers, pre-substitutions). Radarr’s copy is movies (year, edition). They have already diverged. `ParsingService` — the part that turns a parse into “this is episode 7 of this series” — needs the series database and XEM scene maps. Servarr have said they will not adopt guessit; their own test suite is the product.

| Path | What you actually get | Cost vs reward |
| --- | --- | --- |
| **`GET /api/v3/parse?title=`** while Arr is still running | Official parser + series match, one HTTP call. GPL does not touch Pompey (MIT). | **This is the library.** It is the “steal search, keep Arr as importer” move. |
| **Vendor `Parser.cs` into a C# sidecar** | Title/season/quality/group only. Still write matching ourselves. Recyclarr already proved a musl .NET binary can live in this container. | Permanent fork of a file that moves on `develop`. Sonarr **and** Radarr copies. GPL-3 on that sidecar (and a lawyer’s question if we statically link it into the add-on). More complexity than the reward unless we have already committed to dropping Arr. |
| **Port the regexes to Python** | We own every scene exception from that day on. | The test suite is the value. We will drift. Worse than a sidecar. |
| **guessit / similar** | Fine on `Show.S01E01.1080p.WEB`. Weak on anime batches and multi-episode packs. | Servarr already rejected this as not covering their cases. Fine for a movie-only experiment, not World Trigger. |
| **pythonnet in-process** | Theoretically load their DLL from Python. | Musl HAOS, s6, two runtimes in one process. Do not. |

So: **use the parser as a service, not as a library.** If Arr is still up, `/parse` plus ManualImport is how we borrow their filename accountant without copying C#. If Arr is gone, extracting `Parser.cs` saves some of job C’s “parser we will get wrong,” but not matching, not scene maps, and not the update clock we were trying to delete. That is only worth it after we have already decided to become the engine.

## Decision

| Question | Answer |
| --- | --- |
| Benefits of replacing Arr? | Search policy, less indexer spam, RAM, fewer update clocks, language/pack rules we cannot express today. |
| What we lose? | Parser, episode matching, metadata graph, TRaSH-depth choice, RSS, and every import edge we already burned on. |
| MVP that does not backslide? | Job C: tens of thousands of lines, a new engine, not a mock. |
| Do it now? | No. Shape Arr; if that is not enough, steal **search** and keep Arr as the importer. |

Revisit only if official knobs plus an optional Prowlarr-driven search still cannot land an old dual-audio season without Debug, *and* we are willing to own matching bugs that Arr already absorbed.
