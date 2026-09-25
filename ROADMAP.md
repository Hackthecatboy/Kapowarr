# Development roadmap

Updated September 25, 2026. This is an unofficial personal development fork.

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
  reports testing is blocked by a Prowlarr/Bitmag issue.
- SABnzbd, NZBGet, qBittorrent and Transmission adapters are implemented;
  NZBGet and Transmission still need live validation.
- Newznab/Torznab manual search, automatic search and RSS routing are implemented.
  Live unattended automation and recovery still need validation.
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
pre-import path failures; torrent ownership failures still require manual review.

Acceptance: correct a bad mapping and import the same tracked job without a
restart, duplicate download or duplicate library copy. Never automatically replay
an uncertain submission or partially completed import.

### 2. Verify the torrent workflow on Synology — blocked

- [ ] Download a matching release through Prowlarr and qBittorrent.
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

### 4. Comic download preferences and upgrades — preferences implemented

- [x] Preferred formats and whole-release size limits.
- [x] Literal release-title term preferences/exclusions (including group names)
  and explainable ranking; verified group metadata parsing remains future work.
- [ ] Upgrade rules and a stopping point once the desired quality is met.
- [x] Explain selection/rejection reasons in manual search.

See [download preferences](docs/download-preferences.md) for rules and the
search-only NAS test. Live verification is pending. Automatic replacement of
existing issues remains unimplemented; missing-issue selection is unchanged.

### 5. Failed-download handling

- [ ] Retain useful failure history and distinguish temporary connection failures.
- [ ] Block unsuitable releases and try another matching release when enabled.
- [ ] Bound retries and avoid repeated downloads of the same failed release.
- [ ] Preserve files and require review when completion/import is uncertain.

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
- [ ] Show why a release was selected, rejected, retried or held for review.
- [ ] Record download/import outcomes with actionable diagnostics.
- [ ] Keep credentials out of diagnostics and exported logs.

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
