# One household truth

This is the landing after a season that was already on Plex got grabbed again, and after asking whether that is a one-time glitch or proof the hidden stack is too denormalized to ever run clean.

**Where we land:** it is a real class of bug, not a cosmic ray. It is also not proof that we must replace Radarr and Sonarr next. The product gets fantastic by owning **one** “we have it” record and making the other apps projections of that record — not by adding a sixth database, and not by becoming the matcher tomorrow.

## What you saw

Three programs answered “do we have this show?” from three places:

| Who | What “have it” means |
| --- | --- |
| **Sonarr** | `series.path` plus per-episode `hasFile`. Search and grab if a monitored episode is missing or below cutoff. |
| **Plex** | Whatever folders you added as library locations. A title can appear in Kid Friendly and Not Kid Friendly at once. |
| **Seerr** | Mostly Plex. Available on the poster is “Plex scanned it,” not “Sonarr imported it.” |

Kid vs general is a **root folder** on that Sonarr row (By Rating, then Kid Friendly or Not Kid Friendly). `route-rating` only moves titles still under By Rating, and it uses Arr’s editor (`rootFolderPath` + `moveFiles`) so the database and the files move together.

A file-manager move updates disk and, after a scan, Plex. It does not update `series.path` or episode-file rows. Housekeeping then asks Sonarr’s wanted/missing list. After **0.2.51** that poke is a `SeasonSearch` when two or more holes share a season — so a path mixup becomes an entire-season grab, not eight singles.

Seerr looking available while Sonarr hunts is the expected split, not a Seerr lie.

This is the same failure vanilla Sonarr has if you `mv` a show. We made it more likely by inventing three TV roots and two Plex libraries for one title identity, and by hiding the accountant.

## One-time bug or too many hidden apps?

Neither slogan is right.

**Not one-time.** Any time disk, Sonarr, and Plex disagree, housekeep will try to fill Sonarr’s holes. Hand-moving Kid ↔ Not Kid is the household way to get that disagreement. Leftover-root rehomes (`moveFiles: true`) that fail the same way look identical.

**Not “glitch-free is unrealistic.”** The denormalization is real and we created some of it. It is also bounded. The dangerous bit is not “five binaries in one container.” It is **three independent have-it bits with no reconciler**, plus a housekeep loop that treats Sonarr as gospel.

Replacing Arr would not delete that problem. We would own `hasFile`. Plex would still scan folders. Seerr would still trust Plex unless we changed that too. A rewrite that does not collapse the truths is a new engine with the same split-brain.

## What “fantastic” actually is

The household contract is already in the guide. Make it the only contract:

1. **The Seerr request is the ticket.** Open means “keep looking.” Remove the request (not Clear Data) means “stop.” A **Declined** row is not a household close-out — Seerr’s Arr scan can mark a brand-new auto-approved request as orphaned before Radarr has accepted it. Delete the row to stop; Pompey re-requests a false decline.
2. **One library folder per title.** Kid Friendly *or* Not Kid Friendly *or* By Rating-in-flight. Not two Plex libraries for the same files.
3. **Sonarr’s path is that folder.** Disk moves go through Arr’s editor, or Pompey retargets and rescans when it can see the files on a sibling root.
4. **Plex and Seerr are projections.** They are allowed to lag. They are not allowed to be a second source of truth that we search against.

Everything else (Proton, Prowlarr, Recyclarr, qBittorrent) stays a tool behind that contract.

## Path forward (in this order)

**Now — stop the grab, then reconcile.** If a season is downloading again: remove the Seerr request so cutoff/missing pokes stop; pause or remove the qBittorrent season torrent; leave the good files where they are. **0.2.54** notices the removed request within about 15 seconds, unmonitors the Sonarr row (seasons and `monitorNewItems` too), cancels in-flight `SeasonSearch`, and drops that title from the Arr queue. **0.2.52** points Sonarr at the one sibling Kid / Not Kid / By Rating folder that already has the video (`moveFiles: false`) and posts `RescanSeries` instead of `SeasonSearch`. If both libraries still have files, we do not guess — we refuse to search and log it. Empty the leftover Plex library so the show is in one place.

Do not drag library folders between Kid and Not Kid in the file manager. Pick the root on the Seerr request, or let By Rating sort. Housekeeping still will not guess Kid vs Not Kid from a filename.

**Manual Grab for catalogs Sonarr cannot parse.** Request the title first (that is the folder identity). Then Prowlarr Search → Grab. Those torrents land in `downloads/manual`, not `downloads/complete` and not a library root. Housekeep imports them onto the leftover Arr path even if quality would reject, and does not SeasonSearch while that file is still in the drop. A Grab with no Arr row stays in `manual/` until you request it.

**Next — fewer truths, not fewer logos.** Operator status in the sidebar (roadmap 4) should say “files are on disk, Sonarr path is wrong” instead of only “wanted/missing.” That is product. Stealing search (Pompey → Prowlarr, Arr still imports) is still the right move if query policy is the pain. It does not fix this bug; the accountant was correct given a stale path.

**Not next — replace Arr so we have one database.** That is a new product, larger than today’s glue, and we become the matcher. Keep Arr as the filename accountant until we are willing to own matching bugs. The design note for that cost lives beside this file if we write it; the decision is the same: shape Arr, steal search if needed, do not become the engine to fix a path split.

## What we will not do

- Guess Kid vs Not Kid from a release name so housekeep can “just move it.”
- Treat Seerr available as “Sonarr should stop.” Plex lag would hide real holes.
- Add another SQLite of our own that mirrors Arr, Plex, and Seerr.
- Apply the OUTPUT DROP kill switch on a cloud agent host, or start the torrent client, to reproduce a household hand-move.

The stack is allowed to be several processes. It is not allowed to have several opinions about whether the file is already in the house.
