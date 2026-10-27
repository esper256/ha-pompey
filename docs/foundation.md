# Integration architecture

## Runtime

Home Assistant Supervisor builds one amd64 image containing checksum-pinned applications and Recyclarr resources. Startup does not download or replace applications. The supported installation target is a fresh amd64 setup. Home Assistant cold backups provide recovery; legacy-layout migration, downgrade compatibility and automatic binary/database rollback are outside the supported scope.

Applications and controller jobs run as root inside the container for compatibility with Supervisor-managed SMB/CIFS storage. Container isolation and Home Assistant AppArmor provide containment; applications share a trust boundary. Startup preserves existing media and configuration ownership. Initialization installs the fail-closed firewall before applications start. Local application access does not require an active tunnel. Application internet traffic, including DNS and metadata requests, requires the VPN. Bootstrap DNS is limited to the short-lived endpoint resolver running as nobody (UID/GID 65534); ordinary root application DNS cannot use that exception. WireGuard configuration is provider neutral.

Arr manages normal imports and upgrades. Prowlarr direct downloads use a separate category, with persisted import intent and review of ambiguous results. Unknown files remain available for review, and unrelated maintenance jobs continue independently. Request closeout preserves files and monitoring when request history is incomplete or appears to have been reset.

## Integration testing

Tests run the shipped applications and orchestration. External services are controlled fixtures: metadata replay, one Torznab indexer, one tracker and a synthetic BitTorrent peer. Synthetic media has accurate durations, file sizes and piece hashes. Production quality profiles remain active.

Before starting a downloader or peer, the harness verifies a fresh isolated network namespace with no external interface or route. It then creates a local WireGuard fixture inside that environment. Application startup waits for fixture HTTP, DNS and peer services to be listening. Tests do not contact public torrent peers or change host firewall rules or routes.

One scenario verifies an empty installation. Additional scenarios use unique content identifiers on the same application stack and verify that previously imported media, requests and monitoring remain intact. Cold configured clones support independent startup checks. Reports separate setup, search, transfer, import and lifecycle costs and retain failures without scenario retries. Focused safety-logic tests and isolated packet tests remain required.

## Release validation

Required automated CI is the release gate. Full release acceptance includes strict VPN transfer resumption, storage and cold-start faults, configured clones and a real previous-release upgrade. The historical 0.4.0 VPN timeout exception is not carried forward into current release acceptance.

Upstream candidates are built and tested using their actual binaries and configuration. Dependency discovery serializes candidates and trusted-main release orchestration verifies the tested merge tree before protected merge and publication. Actual Supervisor installation, Home Assistant Ingress authorization, NAS behavior and media-player playback remain unautomated boundaries. See [testing](testing.md) and [update architecture](arr-auto-update.md).
