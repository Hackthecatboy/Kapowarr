# Sonarr-style indexers and download clients

Status: initial indexer storage foundation implemented and tested locally.
Download-client adapters and usable Torznab/Newznab integration are still pending.

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

This checkpoint does not register partially working providers in the UI and
does not constitute the full-catalog release. Next is the Newznab/Torznab
request/result layer, query builders and download preppers, followed by the
shared external-download lifecycle and adapter implementations.

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
