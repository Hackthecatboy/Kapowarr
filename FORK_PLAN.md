# Sonarr-style indexers and download clients

Status: Newznab/Torznab search and settings are implemented. Newznab now connects
to SABnzbd/NZBGet through a durable submission journal and a separate Usenet
worker/import path. Torznab remains search-only. Full-catalog release and live
client/NAS verification are still pending.

## Usenet download checkpoint (September 20, 2026)

- Registered SABnzbd/NZBGet with native settings and category-aware connection
  tests. Adapted queue/history semantics from the reference fork; rebuilt the
  transport and registration against v1.3.2. Attribution is in THIRD_PARTY_NOTICES.
- Added bounded requests without credential-bearing URL logs and explicit
  completion handling; repair/unpack is never inferred complete from progress.
- Stored searched release metadata for Newznab preparation and comic matching.
  Enabled Newznab manual/automatic/RSS downloads; Torznab remains guarded.
- Added migration 52 to 53 for external job ID/submission phase and release
  metadata. Existing jobs survive. New Usenet jobs reconnect after restart;
  uncertain submissions/imports pause for review instead of replaying.
- Fixed queue insertion/commit ordering before external threads start.
- Added completed-directory checks and remote mappings. Imports preserve client
  originals, copy into a unique library subfolder, and verify library issue
  bindings before recording success or cleaning client history.
- Exposed review reasons in queue API/WebSocket/UI. Added 27 regression tests,
  including a real library scan of a temporary CBZ and local HTTP fixtures.
- All 83 tests pass; configured mypy and JavaScript syntax checks pass. DOM checks
  cover SABnzbd/NZBGet forms and switching to/from the existing torrent form.
  Live clients, actual browser rendering, and NAS verification remain pending.

Limits: fixed `kapowarr` category and normal priority, URL-based NZB submission,
copy import preserving filenames, no automatic Usenet rename/conversion yet,
and manual review for interrupted imports. Existing torrent jobs do not use the
new journal yet. See [docs/usenet-downloads.md](docs/usenet-downloads.md).

Next work: live verification of this Usenet slice, then torrent-file/magnet
preparation for Torznab, shared lifecycle integration for existing torrent
clients, and Deluge. Continue the remaining full client catalog afterwards.

## Earlier protocol/search checkpoint (September 20, 2026)

Keep the official v1.3.2 baseline. The reference fork is
`silasfelinus/Kapowarr` at `2a283b1d95ae0ad17d7a77acffab74424b8fb586`,
checked out separately at `/home/andrew/Kapowarr-silas-reference`. Its main
branch predates v1.3.2 and identifies itself as v1.3.1. Do not merge its whole
search framework, unrelated library features, or migrations into this fork.

Implemented `backend/implementations/znab.py`: shared Newznab/Torznab XML and
HTTP operations, using namespace/enclosure handling adapted from that fork.
Includes capability validation, recent-release requests, explicit category
filters, pagination, HTTP/XML error handling, and bounded response bodies.
Attribution and adaptation details are in `THIRD_PARTY_NOTICES.md`.

Validation: all 47 tests pass, including 18 new protocol/parser and local HTTP
fixture tests; configured mypy passes on 73 source files. These checks do not
establish live Prowlarr/indexer compatibility.

That search slice registered Newznab and Torznab providers with comic query
builders, the async search coordinator, and authenticated indexer settings.
Settings include the full API URL, API key, enabled state, and category IDs.
The providers inherit Kapowarr's environment proxy configuration and avoid
forwarding indexer credentials through the DDL FlareSolverr session.

Manual search returns parsed, matched comic releases. At that checkpoint both providers were
explicitly search-only: download buttons are disabled, automatic search/RSS
exclude them, and direct queue submissions are rejected before preparation or
blocklisting. Fixed coordinator removal when several indexers exhaust their
queries in the same iteration. Prowlarr endpoints can be configured manually;
Prowlarr import/synchronization and native application registration remain pending.

Validation for this slice: 9 new integration tests cover authenticated settings,
connection failures, protocol/query routing, matching and duplicate results,
multiple exhausted indexers, discovery dates, and download guards. All 56 tests pass; configured mypy passes on 78 files. Both changed JavaScript
files pass syntax checks, and the settings template renders all three sections.
Live Prowlarr and browser/NAS verification remain pending.

The Usenet checkpoint above subsequently connected Newznab downloads and
SABnzbd/NZBGet. The broader shared lifecycle and remaining client catalog are
still work in progress.
See [docs/znab-indexers.md](docs/znab-indexers.md) for this build's setup and limits.

The requested first release includes the full Sonarr-style download-client
catalog, rather than a release limited to SABnzbd and Deluge. Implementation
can proceed in independently testable changes, but the release target remains
the complete catalog.

## Source baseline

- Kapowarr: Casvt/Kapowarr main, `c191dda`, version 1.3.2.
- Sonarr reference: Sonarr/Sonarr v5-develop, `76c684e`, checked out at
  `/home/andrew/Sonarr-reference`.
- Local branch: `feature/sonarr-integrations`.
- The original repository is the `upstream` remote.
- GitHub fork: `Hackthecatboy/Kapowarr`, configured as the `origin` remote.

## First implementation checkpoint

Synology development deployment is documented in
[docs/synology-development.md](docs/synology-development.md). It builds images
locally from this branch, using `compose.synology.yml`, `Dockerfile.synology`,
and `scripts/synology-update.sh`. No registry publishing is configured. NAS
runtime validation remains pending; the default is an isolated test instance
on port 5657 with its own database, media, downloads, and database backups.

- Added the Usenet protocol identifier without changing existing identifiers.
- Added indexer API-token and category storage, validation, and migration 51
  to 52. Existing GetComics settings are preserved.
- Fixed non-GetComics updates so unused provider fields are bound correctly.
- Keep category lists as lists during connection testing and serialize them
  only at the database boundary.
- Stopped logging complete indexer settings when adding or updating providers.
- Added nine storage/migration regression tests. All 23 unit tests pass;
  the repository's configured mypy check passes on 70 source files.

That initial checkpoint provided storage only. The later selective adaptation
checkpoint above records the current protocol and search implementation.

When merging future upstream migrations, reconcile migration numbers before
shipping an upgrade; this fork now has a schema change beyond upstream v1.3.2.

Retain upstream attribution and license notices. Follow Kapowarr's Python,
Flask, SQLite, and native frontend conventions. Sonarr is a behavioral and
protocol reference; its TV-specific search and matching rules do not fit comics.

## Download-client target matrix

Every entry below is a release target, not a claim of current support.

| Protocol | Client | Upstream Kapowarr baseline |
| --- | --- | --- |
| Torrent | Aria2 | Missing |
| Torrent | Deluge | Missing |
| Torrent | Download Station | Missing |
| Torrent | Flood | Missing |
| Torrent | Freebox Download | Missing |
| Torrent | Hadouken | Missing |
| Torrent | qBittorrent | Adapter exists; verify expanded lifecycle |
| Torrent | RQBit | Missing |
| Torrent | rTorrent | Missing |
| Torrent | Torrent Blackhole | Missing |
| Torrent | Transmission | Adapter exists; verify expanded lifecycle |
| Torrent | Tribler | Missing |
| Torrent | uTorrent | Missing |
| Torrent | Vuze | Missing |
| Usenet | Download Station | Missing |
| Usenet | NZBGet | Missing |
| Usenet | NZBVortex | Missing |
| Usenet | Pneumatic | Missing; capability differs from managed clients |
| Usenet | SABnzbd | Missing |
| Usenet | Usenet Blackhole | Missing |

Each managed adapter needs connection testing, validation, authentication,
submission, stable job identity, queue and history normalization, completed
output paths, failure reporting, and supported cleanup operations. Expose
capabilities for clients that cannot monitor jobs or provide equivalent control.
Blackhole and Pneumatic integrations must not pretend to have managed-client
queue APIs.

## Indexer target

- Newznab for Usenet and Torznab for torrents, including endpoints served by
  Prowlarr. Support API keys, category selection, capabilities, pagination,
  manual search, automatic search, and recent-release discovery.
- Torrent RSS feeds.
- Prowlarr connection settings and indexer import/synchronization initiated
  by the fork, with explicit ownership rules for synchronized settings.
- Preserve GetComics and existing direct-download services.
- Audit Sonarr's additional direct adapters: BroadcasTheNet, FileList, Fanzub,
  HDBits, IPTorrents, Nyaa, and TorrentLeech. Assess actual comic search support
  and supported API operations before promising equivalent behavior. Do not
  silently substitute TV queries for comic queries.

Native registration in Prowlarr's Applications menu is a separate integration:
Prowlarr must know the application's API. Track that separately from consuming
Prowlarr's Torznab/Newznab endpoints. The open upstream request is
https://github.com/Prowlarr/Prowlarr/issues/2647.

## Architecture work

1. Extend protocol definitions and persisted configuration with Usenet,
   adapter-specific fields, categories/labels, priority, and client capabilities.
   Add migrations that preserve existing installations.
2. Extend indexer validation, API responses, configuration storage, and forms.
   The current manager contains GetComics-specific SQL columns; adding a
   registered adapter alone is insufficient.
3. Implement protocol query builders, result parsers, and download preppers.
   Preserve comic volume/year/issue-range matching and explicit manual overrides.
4. Generalize external-download state and persist submitted job IDs so restart
   recovery reconnects to existing jobs instead of submitting duplicates.
5. Handle torrent files and magnet links directly. The current torrent path
   depends on magnet2torrent.com for metadata; private tracker support needs
   authenticated torrent fetching and client-provided metadata/output paths.
6. Implement managed-client adapters and file-drop clients against the shared
   lifecycle, including bounded network requests and useful connection errors.
7. Extend completed-download handling to Usenet repair/extraction states and
   actual final paths. Reuse and verify remote path mapping. Preserve torrent
   seeding, and restrict cleanup to tracked jobs after successful import.
8. Extend native settings screens for torrent and Usenet clients, indexers,
   credentials, capability-specific options, and connection tests.
9. Package a separate development deployment with its own configuration/database
   and download category. Verify upgrades using a copied database before any
   production rollout.

## Verification and completion criteria

- Protocol fixture tests for authentication failure, malformed responses,
  timeouts, empty queues, active downloads, completion, failure, and missing jobs.
- Integration tests for settings round trips and migrations, search-to-download
  routing, restart recovery, remote paths, imports, and cleanup boundaries.
- UI checks for adding, testing, editing, and deleting every provider type.
- Run the repository's unit suite and relevant type checks.
- Runtime verification with test instances where available; record adapters
  verified only against fixtures separately from adapters verified live.
- A name in the add-client dialog is insufficient to mark an integration done.

## Source pointers

- `backend/base/definitions.py`: protocol enums and provider interfaces.
- `backend/implementations/indexer_client_manager.py`: indexer storage/validation.
- `backend/implementations/external_client_manager.py`: external-client registry.
- `backend/implementations/download_clients/Torrent.py`: torrent lifecycle.
- `backend/implementations/download_preppers/`: release-to-download preparation.
- `backend/features/download_queue.py`: persistence and execution.
- `backend/features/post_processing.py`: completed-file processing.
- `backend/implementations/remote_mapping.py`: remote path mapping.
- `frontend/static/js/settings_download_clients.js`: client configuration UI.
