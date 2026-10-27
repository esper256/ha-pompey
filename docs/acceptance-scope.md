# Acceptance scope

Pompey converts Seerr requests into media in the configured libraries for a separate Plex installation. It preserves unmatched files, applies seeding policies, restricts internet access to the VPN and provides diagnostics for requests that cannot be completed.

The [integration architecture](foundation.md) supports fresh amd64 installations. [Testing](testing.md) documents the implemented scenarios, measured timings, known VPN-resume issue and remaining coverage. Automated integration does not establish actual Supervisor installation, NAS behavior or Plex playback.

Home Assistant cold backups provide recovery. Required automated acceptance is the release gate; release exceptions must be explicit and narrowly scoped.
