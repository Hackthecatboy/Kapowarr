# Development roadmap

Updated September 27, 2026. This is an unofficial personal development fork.

The goal remains a full Sonarr-style indexer and download-client catalog, with
comic-appropriate automation and recovery. Keep the Kapowarr v1.3.2 foundation
and selectively adapt useful integrations rather than replacing it with another
fork. The milestones below are priorities, not a claim of feature parity.

## Current checkpoint

- **Verified on the isolated Synology test deployment:** Prowlarr Newznab search
  → SABnzbd download → remote path mapping → library import. The user confirmed
  this working after the single-file completion fix (`122f815`).
- Prowlarr Torznab results and qBittorrent connectivity have been exercised on
  the NAS. A complete torrent import with continued seeding is still unverified; the user
  resolved the Bitmagnet discovery blocker by enabling DHT in qBittorrent.
- SABnzbd, NZBGet, qBittorrent and Transmission adapters are implemented;
  NZBGet and Transmission still need live validation.
- Newznab/Torznab manual search, automatic search and RSS routing are implemented.
  Live unattended automation and recovery still need validation.
- Optional SABnzbd payload cleanup after successful import is implemented, off by default; NAS validation pending.
- Import naming now uses Rename Downloaded Files for Usenet, torrents and Pack Inbox copies.
  Duplicate Files provides SHA-256 and same-issue review for indexed library files.
  Detection is user-verified. Explicit deletion supports identical copies and manually
  reviewed same-issue editions, with a retained keeper and revalidation before deletion;
  comprehensive NAS deletion validation remains pending.
  Five-second client polling and Activity refresh improve progress updates. These changes
  are locally tested; Synology validation remains pending.
- System Logs supports recording-level selection, persistent display filters
  and auto-refresh. New logs and log viewing/downloads redact named credentials.
- Manual search has a persistent Matches only filter. Download-client settings
  use a single scrolling layout with accessible remote mappings.
- Public GHCR development images are deployed through Container Manager.
  Keep testing isolated from production data.

## Next milestones, in priority order

### 1. Queue recovery — user-verified on Synology

- [x] Add Retry Import for recoverable failures, without resubmitting downloads.
- [x] Recheck paths after a remote mapping changes, without requiring a restart.
- [x] Explain the client-reported path, mapped path and failed check clearly.
- [x] Allow removal of paused review entries from Kapowarr's queue while retaining client jobs and files.
- [x] Distinguish safe retries from interrupted/partial imports that need review.

Implemented with regression checks. The user confirmed the recovery walkthrough
working on Synology.
Follow the [queue recovery test](docs/queue-recovery.md). Retry is limited to
pre-import path failures and unexpected exceptions before copying starts; torrent ownership failures still require manual review. A configurable Usenet completion delay (default 30 seconds) and the broader Retry Import action are implemented with regression tests; NAS validation is pending.

Acceptance: correct a bad mapping and import the same tracked job without a
restart, duplicate download or duplicate library copy. Never automatically replay
an uncertain submission or partially completed import.

### 2. Verify the torrent workflow on Synology — in progress

- [x] Observe qBittorrent transfers after resolving the DHT discovery blocker.
- [ ] Confirm the downloaded release matches the intended library issue.
- [ ] Validate hash-based repeat suppression during automatic searches and
  qBittorrent category-based placement (implemented; locally tested).
- [x] Discover external qBittorrent category jobs in Activity with explicit
  completed-file review through Pack Inbox; no automatic import or client changes.
  Locally tested; NAS validation pending.
- [ ] Validate first-click Review Files handoff on Synology. Fixed the scan's
  settings-cache race by publishing the folder at completion and invalidating
  cached settings after commit. Activity now awaits the complete scan response
  before navigation. Stale-cache and interrupted-response regressions pass.
- [ ] Validate RSS sync alongside imports and volume adds on Synology.
  User correlated database contention with RSS; fixed uncommitted task-history
  and rejected-download blocklist writes spanning download preparation/network
  work. Two-connection SQLite regression tests pass. Busy errors still restore
  the add dialog; live concurrency validation remains pending.
- [ ] Verify library import while the original continues seeding.
- [ ] Verify restart recovery, mapped paths, ownership checks and queue cleanup.
- [ ] Verify both Copy and Complete seeding modes.

Acceptance: imported comic matches the library issue, original torrent data is
unchanged, and restarting does not submit or import it again.

### 3. Prowlarr synchronization — import and refresh user-verified

- [x] Import selected indexers from a Prowlarr address and API key.
- [x] Update imported indexers without duplicates or overwriting unrelated entries.
- [x] Define category, enable/disable and removed-indexer behavior.
- [x] Keep manual Newznab/Torznab setup available.

One saved connection, read-only preview, selected import and manual refresh are
implemented. The user confirmed import and later-indexer refresh working on Synology.
Advanced disable/removal reconciliation remains separately testable. See
[the setup and validation guide](docs/prowlarr-sync.md).
Scheduled refresh, multiple connections and adopting manual entries remain future
extensions.

Native registration in Prowlarr's Applications menu is a separate integration
question; importing its indexers does not establish that support.

### 4. Comic download preferences and upgrades — preferences user-verified

- [x] Preferred formats and whole-release size limits.
- [x] Literal release-title term preferences/exclusions (including group names)
  and explainable ranking; verified group metadata parsing remains future work.
- [ ] Upgrade rules and a stopping point once the desired quality is met.
- [x] Explain selection/rejection reasons in manual search.
- [x] Configurable source-group priority and sequential automatic-search fallback (including GetComics last or first); NAS validation pending.

See [download preferences](docs/download-preferences.md) for rules and the
search-only NAS test. The user confirmed the preferences working. Automatic replacement of
existing issues remains unimplemented; missing-issue selection is unchanged.

### Pack workflow — manual workflow exercised; unattended ingestion pending

The user requested mixed-series/weekly-pack detection for downloads made outside
Kapowarr, followed by recurring automatic download and ingestion.

1. **Pack inbox (implemented; managed-pack import user-exercised):** completed
   folders are scanned per file, with selectable missing-issue matches and review
   rows for unmatched/ambiguous content. The settling check is 30 seconds. A
   persistent copy journal prevents unsafe replay of interrupted writes. Unnumbered
   standalone books can match a unique eligible single-issue edition; that matching
   extension is locally tested and still needs live confirmation.
2. **GetComics pack downloads (implemented; manual flow user-exercised):** article
   preview, supported HTTP download links, progress, and ZIP extraction feed the
   inbox. **Find / Add Series** opens a floating series search and saves an explicit
   per-file series/issue association, including existing library series.
   This matching flow is locally tested; live validation is pending. Mega, torrent, RAR/7z and multipart
   pack handling remain outside the automated download/extraction flow.
3. **Managed-pack cleanup (implemented; user-exercised):** verified imported
   sources are deleted from managed packs. **Finish Pack** previews and confirms
   deletion of the original archive and remaining extracted files. External inbox
   originals, including torrent-managed files, remain intact.
4. **Past packs and weekly subscriptions (implemented; discovery user-exercised):**
   paginated search, saved label/service rules, a user-selected weekday,
   pause/resume, manual checks and tracked-article repeat protection. Subscriptions
   can download for review; imports remain manual. Unattended scheduled downloads
   and restart behavior still need live validation.
5. **Inbox usability and structure cleanup (complete; locally verified):** compact
   history, collapsible packs, file filters and visible-match selection are in place.
   The five architecture/testing cleanup slices below are complete.
6. **Recurring scans and safe unattended ingestion (pending):** scheduled scans,
   content identity across moved/renamed files, held-copy recovery/review controls,
   and duplicate prevention before enabling automatic import.
7. **Broader recurring pack rules (pending):** indexer/category/size rules and
   downloader routing beyond the implemented GetComics article subscriptions.

Automatic creation of unknown series remains a separate opt-in feature to design.
Ordinary single-series download/import continues alongside this inbox. See
[pack inbox setup and test](docs/pack-inbox.md).

Next pack work should address repeat protection and recovery before unattended
imports. The isolated torrent verification above remains outstanding independently.

### 5. Failed-download handling

- [ ] Retain useful failure history and distinguish temporary connection failures.
- [ ] Block unsuitable releases and try another matching release when enabled.
- [ ] Bound retries and avoid repeated downloads of the same failed release.
- [x] Preserve files and require review when completion/import is uncertain
  (implemented for external downloads and pack imports; broader retry policy pending).

### 6. Remaining download clients and configuration

- [ ] Add Deluge, then work through the remaining Sonarr-style client catalog.
- [ ] Maintain an explicit adapter/support matrix as the catalog expands.
- [ ] Make categories, priorities and seeding settings configurable per client.
- [ ] Validate each adapter's submission, status, completion, restart and deletion
  behavior; connection-test success alone is insufficient.

The full client list remains the target; the four implemented adapters are an
intermediate checkpoint, not the finished scope.

### 7. Health checks and history

- [ ] Report unreachable clients/indexers and inaccessible or unmapped paths.
- [ ] Consolidate selection, rejection, retry and hold reasons into history
  (manual-search explanations and queue/inbox review messages already exist).
- [ ] Record download/import outcomes with actionable diagnostics.
- [x] Redact named credentials in new logs and log viewing/downloads.
  Broader diagnostics/export coverage still needs review.

## Validation and project references

For each slice, record implemented behavior separately from live verification.
Use focused regression checks and isolated NAS tests. Preserve client originals,
especially torrent data needed for seeding. Upstream submission remains a separate
future decision.

- [Implementation history and architecture](FORK_PLAN.md)
- [Indexer setup](docs/znab-indexers.md)
- [Usenet setup and recovery behavior](docs/usenet-downloads.md)
- [Torrent setup and seeding behavior](docs/torrent-downloads.md)
- [Synology GHCR deployment](docs/synology-ghcr.md)

## Pack feature structure cleanup

Keep behavior stable while bringing the fork's additions closer to the original
Kapowarr architecture. Complete and verify each slice separately.

1. **Public provider interfaces (complete; locally verified):** pack workflows
   use public GetComics article parsing/link resolution and an HTTP streaming
   context that owns response/session cleanup. Tested with successful downloads,
   truncated payloads and request failures; live provider validation remains
   part of container testing.
2. **Database organization (complete; locally verified):** subscription/release
   persistence uses `PackSubscriptionsDB`, jobs use `PackDownloadsDB`, and import
   journal, matching and cleanup queries use `PackInboxDB`. Workflows retain
   their original commit/rollback boundaries and filesystem recovery ordering;
   the import ownership check still runs inside `BEGIN IMMEDIATE`.
3. **Smaller workflow functions (complete; locally verified):** subscription
   checks now separate schedule claiming, article discovery and pending-release
   processing. Imports separate validation, verified copying, issue binding and
   completion, with the original batch lock, commits and per-file hold handling.
4. **Remaining types and formatting (complete; locally verified):** preview
   tokens and import journal records use named types; progress updates have
   explicit fields. Applied conservative formatting and sorted imports while
   preserving Python 3.8 syntax and existing message strings. Unvalidated API
   input remains permissively typed until runtime validation.
5. **Repeatable frontend tests (complete; locally verified):** Pack Inbox DOM
   regression checks now live in `tests/frontend`, with pinned dependencies,
   [setup instructions](tests/frontend/README.md) and a dedicated CI workflow.
   Sixteen tests cover sequential batch imports, interrupted requests, import selection, history, cleanup confirmation, subscriptions,
   download choices, per-file series selection and review-only category discovery. Browser appearance and live integrations still need
   separate validation.


## Concurrency and refresh follow-up

1. **Scan write locks (implemented; locally tested):** file checks and matching
   are staged before writing the scan journal in a short transaction. Picker
   match refresh uses the same approach. Two-connection SQLite tests verify
   other writes can proceed during file checks; failed publication rolls back
   to previous results. Import revalidation remains in place. NAS validation
   alongside imports/RSS remains pending.
2. **Shared settings cache (implemented; locally tested):** cached settings are
   keyed by connection, SQLite data version and local write count. Transactions
   read directly so uncommitted values and read snapshots cannot enter the shared
   cache; public settings use the same path. Tests cover concurrent commits,
   rollback, delayed old reads and unchanged-read reuse. Existing transaction
   boundaries are preserved. NAS validation remains pending.
3. **Subscription edits (pending):** preserve unsaved weekday changes when an
   already-running polling request returns.
4. **Pack download polling (pending):** prevent older refresh responses from
   replacing newer statuses or restoring obsolete action buttons.

RSS sync defaults to hourly at minute 0. Existing installations using the old
half-hour schedule migrate to hourly; custom schedules are preserved.
