# Pompey 0.5.0 design proposals

These are planning mockups, not implemented product behavior. All titles, paths,
counts, timestamps and versions are illustrative. The screenshot badge makes that
distinction visible when an image is viewed outside its issue.

`index.html` contains the editable source. Serve this directory with a local HTTP
server and open `/?view=overview`, `diagnostics`, `recovery`, `setup`, `storage`,
`vpn` or `support`. Images in `images/` are browser screenshots of those views.

## Roadmap issues

The [0.5.0 milestone](https://github.com/esper256/ha-pompey/milestone/1) groups
the proposed work. Priorities describe implementation order, not completed work.

| Priority | Issue |
| --- | --- |
| Required | [#89 Automated dependency updates and releases](https://github.com/esper256/ha-pompey/issues/89) |
| Required | [#90 Reliable VPN download recovery](https://github.com/esper256/ha-pompey/issues/90) |
| Required | [#91 Per-request troubleshooting](https://github.com/esper256/ha-pompey/issues/91) |
| Deferred to 0.6.0 | [#92 Targeted recovery actions](https://github.com/esper256/ha-pompey/issues/92) |
| Required | [#93 Setup and connectivity checks](https://github.com/esper256/ha-pompey/issues/93) |
| Next | [#94 Storage failure protection and recovery](https://github.com/esper256/ha-pompey/issues/94) |
| Next | [#95 Focused outage and recovery acceptance](https://github.com/esper256/ha-pompey/issues/95) |
| Polish | [#96 Application status, versions and support logs](https://github.com/esper256/ha-pompey/issues/96) |
| Required | [#97 Terse ecosystem overview](https://github.com/esper256/ha-pompey/issues/97) |

## Accepted implementation refinements

Maintainer review on issues #89–#97 supersedes the original mockups:

- #92 is deferred to 0.6.0; 0.5.0 uses read-only explanations and existing native interfaces.
- Keep the Pompey logo. Bundled application versions belong on Overview; behavior preferences belong in Config. Do not repeat indexer warnings beside the indexer summary.
- Media servers are optional. Plex, Jellyfin/Emby and local playback are valid choices; none is a prerequisite for managing downloads.
- Remove conversation-specific advice, prototype labels and development commentary from product UI. Mockup labels in this documentation are not product strings.
- Storage attention stays an explicit contextual panel/banner on the dashboard, preserving the overview rather than silently replacing it.
- Separate fast PR checks from release acceptance. Add fault scenarios only when their value justifies their runtime and complexity.
- #99 adds an inert executable-payload regression and native downloader exclusion policy. This is extension protection, not an antivirus guarantee.

## Development implementation status

[Current implementation screenshots and release-review notes](release-review.md) show the actual product screens with explicitly synthetic data. The older images below remain design proposals.

The maintainer approved the implementation screenshots and authorized stable 0.5.0 after full release integration and previous-release upgrade acceptance. Remaining gaps below are deferred; they are not release guarantees. Dependency discovery operates on stable main and skips prerelease versions.

The development branch uses Overview, Requests and Config tabs, with Overview selected by default and contextual connection/storage attention visible across all views. It includes the compact overview and shared observations (#97), native workflow tables and explicit release inspection (#91), optional media-server/setup checks (#93), bundled versions and bounded log repetition (#96), native executable exclusions (#99), VPN interface-replacement recovery (#90), and storage service holds/restoration (#94). These are implemented increments; the broader issue checklists remain open until their remaining cases are verified.

Dependency discovery/promotion code and real previous-release upgrade coverage are prepared for #89. Repository settings now permit Actions-created PRs while keeping workflow tokens read-only by default. Main requires an up-to-date GitHub Actions `CI required` check, including for administrators, with no manual review requirement. Release approval uses required automated acceptance only: the current candidate’s release integration and aggregate jobs must pass in the exact workflow attempt, with no manual HAOS gate. An actual GitHub discovery → PR → acceptance → publication cycle remains outstanding; discovery operates on stable main and skips prerelease versions.

For #95, local and draft edits use fast checks only. Submitted PRs use the shared movie/episode/executable cohort and required packet checks, with auto-merge after required CI. Release acceptance adds the configured clone, broader shared scenarios, cold startup without VPN, storage recovery, strict VPN download resumption and actual previous-release forward upgrade. Full indexer/metadata outage families, native history-brake/rebaseline scenarios and unmatched direct-grab cases remain stretch work. Existing focused safety logic checks remain meaningful coverage, not claims that those real-engine scenarios exist.

The storage guard observes periodically and establishes an actual service hold after detection. It cannot promise atomic prevention of fallback writes in the detection gap; interrupted native cleanup remains a separate data-preservation boundary. Setup observations distinguish configuration from application response; configured links alone are not proof of every remote service or browser route. The request explanation uses current native observations and the last explicit search, not a durable history of every pipeline transition.

## Overview principles

- Show current counts with stable definitions: downloading, seeding and stalled
  torrents; monitored series and movies; healthy enabled Prowlarr indexers; and
  open Seerr requests with waiting and attention breakdowns.
- Torrent states must have documented, mutually exclusive buckets. A stalled
  transfer is not also counted as actively downloading.
- A monitored series is not a monitored episode, an incomplete season is not
  automatically a failed request, and library presence is not Plex playback.
- Do not make lifetime request completion totals the headline. Offer completed,
  declined and other history in expanded details. Keep source-specific status
  semantics explicit when computing open requests and needs-attention counts.
- Report unavailable and stale observations with their age. Never replace a
  failed query with zero or count one underlying problem repeatedly.
- Make counts useful for noticing discrepancies and opening the relevant
  details. Avoid displaying only green service-liveness indicators.
- Keep the summary compact, wrapping on narrow screens. Expanded details should
  preserve context without creating a second Arr administration interface.

## Safety and scope

Recovery actions use native application operations and their matching/quality
rules. An uncertain result must be reconciled before repeating a mutation.
Unavailable storage must not cause writes to a fallback local directory; any
claim that affected work is paused needs evidence from the real applications.
UI health claims describe observations, not an in-container reproduction of a
network packet test. The firewall's actual guarantees remain packet-tested.

Keep manual log sharing; there is no diagnostic upload or issue-attachment
service. Screenshots show proposed Pompey interfaces, not edits to upstream
Seerr or other application interfaces.

## Mockups

### Ecosystem overview

![Overview proposal](images/overview.jpg)

The narrow-screen preview uses a 390-pixel iframe in `mobile.html`:

![Narrow-screen overview proposal](images/overview-mobile.jpg)

### Per-request troubleshooting

![Request troubleshooting proposal](images/diagnostics.jpg)

### Targeted recovery

![Recovery proposal](images/recovery.jpg)

### Setup and connectivity

![Setup proposal](images/setup.jpg)

### Storage failures

![Storage proposal](images/storage.jpg)

### VPN recovery

![VPN proposal](images/vpn.jpg)

### Status and support

![Support proposal](images/support.jpg)
