# Product direction

Pompey provides a request-to-library workflow for movies and TV series using Seerr and optional, separate media players or servers. It manages the VPN, application configuration, storage policies and service status within one Home Assistant OS app.

Keep the integration smaller than the applications it coordinates. Arr manages release matching, upgrades, imports and library moves. Prowlarr synchronizes indexers. Recyclarr manages quality profiles. Pompey provides storage and seeding policies, conservative request closeout, diagnostics and tested application bundles.

Automatic routing uses age ratings, with unavailable ratings sent to general libraries. Users can select a specific library or quality profile. Search and indexer management are available on local network ports 5055 and 9696. Advanced application interfaces use authenticated Home Assistant Ingress.

Reliability requires preserving media, stopping completed downloads for native moves, blocking internet traffic outside the VPN, recovering from upstream outages and supporting recovery through Home Assistant cold backups. Unknown files and ambiguous imports remain available for review.

Release validation uses focused tests for complex safety logic, isolated packet tests and integration scenarios through the shipped applications. Metadata replay, one indexer, one tracker and a synthetic peer provide controlled external services. Synthetic media uses accurate durations, sizes and hashes with production quality profiles. Automated acceptance is the release gate; actual Supervisor installation, NAS behavior and Plex playback remain unautomated boundaries. Automatic dependency promotion requires broader integration and forward-upgrade coverage.
