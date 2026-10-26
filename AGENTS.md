# Agent development instructions

Read [README.md](README.md), [pompey/DOCS.md](pompey/DOCS.md) and [docs/testing.md](docs/testing.md). Product direction is in [VISION.md](VISION.md).

Supervisor builds `pompey/Dockerfile` locally and starts **one** HAOS add-on container. The household never starts Docker, and we never publish images to Docker Hub/GHCR. A local Docker/Podman build checks the Dockerfile; the journey checks the actual built musl runtime. Neither reproduces Supervisor. The automated acceptance suite is the release gate; see docs/foundation.md for this campaign. Do not spend the session faking Supervisor.

Run `bash tests/run.sh` for routine work. Real pinned artifact, Arr import/Recyclarr, and Seerr API tests are described in the testing guide. Runtime Python sources use `.py` suffixes; only lint shell entrypoints with shellcheck. CI must require the isolated packet tests rather than silently skip them.

## PR completion: do not wait for CI

By default, finish a PR after implementation, relevant local validation, and pushing the changes. Mark it ready for review when the work is complete even if CI is pending. Take at most one non-waiting CI status snapshot after the final push, report passed, failed and pending checks accurately with the PR link, then end the turn. Pending CI alone is not unfinished agent work. Do not claim pending checks passed or that the release is validated.

Do not use CI watch commands, repeated polling, sleep loops, background watchers, or scheduled follow-ups to wait for checks. Run the focused real-application journey locally when changing its harness or production integration. Do not duplicate obsolete slow matrices. Documentation-only changes need diff/format review, not runtime tests.

Fix failures already observed within the task's scope, run the relevant local checks, and push the fix without waiting for the new CI run. Revisit later results when the user requests it. Only wait for CI when the user explicitly asks to wait or a requested merge requires checking completion. Keep required CI checks intact; never bypass them to merge or release.

The integration foundation replaces the previous matrices with one real-application journey, then expands household coverage. Build amd64 only. `CI required` remains the stable aggregate check. Dependency promotion and automatic release must wait until the expanded acceptance suite is enabled.

## Runtime constraints

Torrent clients and synthetic peers may run only inside the dedicated integration harness after it verifies a fresh isolated network namespace with no external interface or route. Local WireGuard fixtures may then be created inside that namespace. Never contact external peers. Use synthetic media, one fixture indexer/tracker and metadata replay. Never change the host firewall or default routes. All shipped applications remain real.

The real service order is s6 cont-init then supervised services. Engines wait for the installed fail-closed firewall and generated configuration; a working VPN is not a startup prerequisite. `wire_stack.py` must fail on a required Prowlarr app/source, downloader category, or initialized Seerr→Arr connection failure. Seerr's public setup wizard is permitted before user id 1 exists; its API key legitimately gets 403 then. After initialization both Radarr and Sonarr are required. Plex/local login and optional Seerr chrome settings must not conceal a required miss.

Arr owns normal imports/upgrades; Prowlarr owns native source synchronization. Do not add speculative file deletion, identifier stripping, fake quality profiles, declined-request resurrection or unbounded shell retry loops. Mutating controller jobs share a lock. Supervisor updates the image; HA cold backups provide recovery. Do not restore the runtime engine updater or legacy migration machinery. Release artifacts and resource commits are pinned in the manifest, with candidate discovery separate from promotion.
