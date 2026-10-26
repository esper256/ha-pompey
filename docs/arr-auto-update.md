# Bundled applications and ownership

Supervisor builds one immutable amd64 image from `pompey/Dockerfile`. `pompey/vendor_bundle.py` runs only during that build: it verifies artifact checksums, extracts safe archive members, exports the digest-pinned Seerr image and checks out the pinned Recyclarr resource commits. Runtime contains neither this installer nor its download tools. `/opt/pompey` is root owned.

`write_engine_configs.py` writes managed settings before s6 starts applications. Engine binaries run as the shared `pompey` identity. Arr self-updates are disabled; updates arrive through the add-on image. `recyclarr_sync.py` reads bundled local resources without GitHub access and configures real Default, Max and Anime profiles. A durable image-identity record is accepted only when the profiles still exist in both Arr services.

The controller has bounded, serialized configuration, download, request and routing jobs. Health probes continue independently. Startup uses the real cont-init-before-services ordering rather than engine-download barriers. Configuration failure clears current prerequisite markers and remains visible; initialized Seerr connections and source synchronization are required.

## Conservative files and requests

Arr owns ordinary imports, upgrades and library moves. Prowlarr direct grabs retain a separate category. Pompey submits only Arr-accepted manual imports and records intent before commands. Unknown or ambiguous files and commands remain reviewable. One bad receipt must not authorize duplicate submission or deletion. Downloads are not assumed to originate from Seerr.

Request closeout uses complete stable snapshots and observed ownership. Unknown seasons are preserved. Search identity changes and large history disappearances pause cancellation until the household explicitly re-baselines while keeping current monitoring. Files and in-flight transfers are retained.

## Updates and recovery

`tools/build_engine_manifest.py` resolves stable releases and resource commits into a candidate manifest. Candidates are built and exercised with the same real-application test. Discovery never changes a running installation. The foundation currently proves one fresh movie journey; automatic merge/release promotion stays disabled until household acceptance coverage expands.

Home Assistant cold backups provide recovery. Pompey deliberately has no runtime package manager, old binary copies, transaction journal, database rollback, downgrade compatibility or legacy-layout migration. Restoring a backup restores the locally built image and app state through HA; ordinary image updates may migrate upstream databases. This foundation does not prove all historical-database upgrades.
