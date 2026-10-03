# Development roadmap

Updated September 27, 2026. This is an unofficial personal development fork.

The goal remains a full Sonarr-style indexer and download-client catalog, with
comic-appropriate automation and recovery built on Kapowarr v1.3.2. This roadmap
tracks unfinished work and outstanding validation. Completed implementation
sections have been removed; behavior and setup belong in the linked guides.

## Live validation still needed

Use the isolated Synology development deployment, not production data. Local
regression tests do not establish live integration or browser behavior.

### SABnzbd completion and repeat downloads

- [ ] Diagnose the reported completed SABnzbd jobs that remain unimported using
  their Kapowarr Activity errors; the supplied October 3 log excerpt contains
  qBittorrent polling but no SABnzbd import error.
- [ ] Validate queued-issue exclusion on NAS: RSS and automatic searches now
  reserve queued issue ranges, including import-review jobs, across release URLs.

### Torrent workflow and queue recovery

- [ ] Confirm release-to-issue matching and import while originals continue seeding.
- [ ] Validate hash-based repeat suppression during automatic searches and
  qBittorrent category-based placement.
- [ ] Verify restart recovery, mapped paths, ownership checks, queue cleanup,
  and both Copy and Complete seeding modes.
- [ ] Validate the configurable Usenet completion delay (default 30 seconds)
  and broader Retry Import action. The original queue recovery walkthrough was
  user-verified; these later changes still need NAS validation.
- [ ] Validate five-second client polling and Activity progress refresh.

External qBittorrent category discovery and Pack Inbox review/import have been
exercised by the user. Continued seeding and recovery still need explicit checks.
See [torrent setup](docs/torrent-downloads.md) and
[queue recovery](docs/queue-recovery.md).

### Concurrency and refresh fixes

All four fixes are implemented with local regression coverage. Remaining checks:

- [ ] **Scan write locks:** run scans and picker match refreshes alongside
  imports/RSS; confirm staged filesystem checks do not hold database write locks.
- [ ] **Settings cache and review handoff:** confirm Review Files opens the new
  folder and results on the first click, including after concurrent settings writes.
- [ ] **Subscription edits:** confirm polling preserves unsaved weekday choices,
  saving one row preserves other drafts, and failed saves retain the chosen day.
- [ ] **Pack download polling:** confirm delayed responses cannot restore old
  statuses/actions and cleanup controls stay disabled until completion.
- [ ] Verify RSS sync alongside imports and volume adds after the task-history
  and rejected-download transaction fixes. Busy errors should restore the add
  dialog rather than leave it hanging.

RSS now defaults to hourly at minute 0; the old half-hour default migrates to
hourly while custom schedules are preserved. Weekly pack checks are separate.
See [indexer setup](docs/znab-indexers.md) and [Pack Inbox](docs/pack-inbox.md).

### Other integration checks

- [ ] Validate unattended GetComics weekly downloads and restart behavior.
  Manual download, extraction, reviewed import, cleanup and release discovery
  have been exercised. Confirm unnumbered standalone-book matching as well.
- [ ] Validate optional SABnzbd payload cleanup after successful import (off by default).
- [ ] Complete NAS validation of explicit duplicate-file deletion with a retained
  keeper; detection is user-verified. See [duplicate review](docs/duplicate-files.md).
- [ ] Validate automatic-search source priority and sequential fallback;
  manual download preferences are user-verified.
- [ ] Exercise Prowlarr disable/removal reconciliation; selected import and manual
  refresh are user-verified. See [Prowlarr synchronization](docs/prowlarr-sync.md).
- [ ] Validate NZBGet and Transmission end to end, beyond connection tests.

## Remaining development, in priority order

### 1. Comic download preferences and upgrades

- [ ] Define upgrade rules and a stopping point once desired quality is met.
- [ ] Add verified release-group metadata parsing beyond literal title terms.

Automatic replacement of existing issues remains unimplemented. See
[download preferences](docs/download-preferences.md) for current selection rules.

### 2. Recurring pack ingestion

- [ ] Add content identity across moved/renamed files and mount aliases.
- [ ] Extend held-copy recovery to retained or renamed copies before unattended
  imports. Explicit recovery after removing an unbound interrupted copy is
  implemented with local regression tests; Synology validation remains pending.
- [ ] Add scheduled scans and safe unattended ingestion with repeat protection.
- [ ] Extend recurring rules to indexer/category/size matching and downloader routing.
- [ ] Design unknown-series creation as a separate opt-in feature.
- [ ] Extend automated pack handling beyond supported HTTP downloads and ZIP
  extraction: Mega, torrent pack jobs, RAR/7z and multipart handling remain outside
  that flow. External completed torrents can already be reviewed in Pack Inbox.

Imports currently require review. Preserve torrent-managed originals and verify
library copies. See [Pack Inbox setup and behavior](docs/pack-inbox.md).

### 3. Failed-download handling

- [ ] Retain useful failure history and distinguish temporary connection failures.
- [ ] Block unsuitable releases and try another matching release when enabled.
- [ ] Bound retries and avoid repeated downloads of the same failed release.

Preserve existing review safeguards: never automatically replay an uncertain
submission or partially completed import.

### 4. Remaining download clients and configuration

- [ ] Add Deluge, then work through the remaining Sonarr-style client catalog.
- [ ] Maintain an explicit adapter/support matrix as the catalog expands.
- [ ] Make categories, priorities and seeding settings configurable per client.
- [ ] Validate each adapter's submission, status, completion, restart and deletion
  behavior; connection-test success alone is insufficient.

SABnzbd, NZBGet, qBittorrent and Transmission are implemented. The full client
catalog remains the target.

### 5. Health checks and history

- [ ] Report unreachable clients/indexers and inaccessible or unmapped paths.
- [ ] Consolidate selection, rejection, retry and hold reasons into history.
- [ ] Record download/import outcomes with actionable diagnostics.
- [ ] Review credential redaction across broader diagnostics and exports;
  named credentials are already redacted in new logs and log viewing/downloads.

### 6. Prowlarr extensions

- [ ] Consider scheduled refresh, multiple connections and adopting manual entries.
- [ ] Investigate native registration in Prowlarr's Applications menu separately;
  importing indexers does not establish that support.

## Project references

Record implementation separately from live verification. Use focused regression
checks and isolated NAS tests. Upstream submission remains a separate decision.

- [Implementation history and architecture](FORK_PLAN.md)
- [Frontend regression tests](tests/frontend/README.md)
- [Indexer setup](docs/znab-indexers.md)
- [Usenet setup and recovery behavior](docs/usenet-downloads.md)
- [Torrent setup and seeding behavior](docs/torrent-downloads.md)
- [Synology GHCR deployment](docs/synology-ghcr.md)
