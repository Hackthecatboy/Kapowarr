# Usenet downloads in the development fork

Current priorities and verification status: [development roadmap](../ROADMAP.md).

This slice connects Newznab search results to SABnzbd and NZBGet. Both clients
have settings, connection tests, URL submission, queue/history polling, job-ID
persistence, remote-path mapping and completed-file import. Torznab is also connected through the separate
[torrent path](torrent-downloads.md). This is not the full Sonarr-client release.

## Set up an isolated test

1. In SABnzbd or NZBGet, create a category named **kapowarr**. Configure repair
   and unpacking, with each job in its own completed directory. The connection
   test requires this category. Category selection and priority are fixed in
   this slice (`kapowarr`, normal priority).
2. Mount that completed-download location into Kapowarr. A completed job must
   resolve to a job directory or supported comic file *inside* Kapowarr's
   configured download folder, not the download folder itself. SABnzbd can
   report the comic file directly; only that file is imported in this case. It must be readable by the container user.
3. If the clients see different paths, add a Remote Path Mapping under Settings
   → Download Clients. For example, map the client's `/completed/` to
   Kapowarr's `/downloads/`. A reported `/completed/Comic/` then resolves to
   `/downloads/Comic/`. These are example container paths, not NAS host paths.
4. Under Usenet Clients, add SABnzbd with its full API key (not its NZB-only key),
   or NZBGet with its username/password. Enter the base URL, including any proxy
   URL prefix. The adapter adds `/api` or `/jsonrpc`. Test and save.
5. Configure a Newznab indexer as described in [the indexer guide](znab-indexers.md).
   The download client must be able to reach the release URL supplied by that
   indexer/Prowlarr. Kapowarr submits that URL; it does not proxy the NZB file.
6. Run a fresh manual search and download one comic in the isolated library.
   The queue shows its client state and any review message. After explicit
   client completion, inspect the resulting library issue and retained original.

Newznab is also available to automatic searches and RSS discovery. Enable
monitoring only for the library you intend this development instance to manage.
When several enabled Usenet clients exist, the existing least-used-client
selection chooses one; this slice does not configure per-indexer routing.

## Import and recovery behavior

Kapowarr stores the indexer's release title and issue information when searching
and checks the comic match again at enqueue. Arbitrary links without stored
metadata are rejected; run a fresh search if an old result is unavailable.

The queue commits a submission marker before contacting the client and stores
the returned job ID. Normal restarts reconnect to that ID. A lost response or
crash during submission leaves an uncertain outcome, which pauses the entry
for review rather than submitting it again. Check the client's queue/history
before removing the Kapowarr entry and attempting another download.

100% downloaded is not completion: repair, extraction, moving and scripts must
finish. Failed/missing jobs, unavailable output folders and unsupported terminal
states require review. Credential and connection failures retry polling without
removing the job. For a pre-import path/mapping error, correct the mapping and save it to trigger
a recheck, or use **Retry Import** in the queue after fixing the mount. No restart
or new submission is needed. **Remove from queue only** is available for paused
review entries and retains the client job and all files. See the
[queue recovery walkthrough](queue-recovery.md).

Imports copy regular supported media into a unique `Kapowarr-<id>-<job-hash>`
subfolder in the volume folder and use the existing library scanner. After matching,
Rename Downloaded Files applies the configured naming format to library copies. A range must match its expected library issues before import succeeds.
Existing destinations and symlinked payloads are refused. Original client files
are retained. Conversion is not connected to this import path yet. Per-job speed
is estimated from downloaded bytes between polls; the first sample is zero.
External jobs poll every five seconds. Activity also refreshes every five seconds
while visible and on WebSocket reconnect, so a missed event can recover.

If an import is interrupted, the entry pauses instead of replaying file writes.
Inspect its library subfolder and client payload; recover or remove partial
copies and rescan the library as appropriate. There is no automatic import
rollback or retry for partially completed imports.

When Delete Completed Downloads is enabled, client history is removed only
*after* a successful library import; original completed payloads remain.
Explicitly removing an unfinished queue entry can delete that tracked remote
job and its data. An uncertain submission without a saved ID cannot be removed
from the remote client automatically; inspect it there. Kapowarr never clears
an entire category or queue.

The later torrent slice also uses the submission journal and copy importer,
with ownership checks and separate seeding handling. See the torrent guide.

## Validation

Automated tests use in-memory SQLite, temporary comic files, the real library
scanner, local HTTP fixture servers, and mocked client responses. They cover
configuration, authentication, submissions, repair/completion/failure states,
restart recovery, import failures, mappings and cleanup boundaries. DOM checks
exercise both client forms and switching back to qBittorrent.

Live SABnzbd/NZBGet, Prowlarr and Synology verification remain pending. Use the
isolated deployment in [the Synology guide](synology-development.md); no image
registry publication is configured.

Protocol references: [SABnzbd API](https://sabnzbd.org/wiki/configuration/4.5/api),
[NZBGet append](https://nzbget.com/documentation/api/append/),
[NZBGet history](https://nzbget.com/documentation/api/history/),
[NZBGet editqueue](https://nzbget.com/documentation/api/editqueue/).

## Optional SABnzbd file cleanup

Settings → Download → **Delete Imported SABnzbd Files** is off by default.
With **Delete Completed Downloads** also enabled, Kapowarr asks SABnzbd to delete
only the tracked completed job and its payload after library matching and optional
renaming have succeeded and the imported phase has been committed. Failed or held
imports never reach this cleanup. The job must still be Completed in SABnzbd's
kapowarr category; otherwise it stays held for review. No local folder deletion is
performed. This uses SABnzbd's history deletion with `del_files=1` ([API guidance](https://github.com/sabnzbd/sabnzbd/issues/1486)).

This does not affect torrents, Pack Inbox, or NZBGet. NZBGet history removal does
not provide the same successful-payload deletion operation. Jobs already removed
from Kapowarr's queue are not swept or retrospectively cleaned. Disable this option
to keep source files, or disable Delete Completed Downloads to keep both history
and files. Explicit cancellation remains a separate action.

Test on the isolated development container with one new missing issue: enable
both options, download/import it, confirm the library comic opens and its issue
is linked, then confirm SABnzbd removed the completed job's payload. Test with the
option off using another issue and confirm its source remains. With a deliberately
incorrect mapping, the held import must retain its source even when cleanup is on.

### Completed downloads awaiting import

RSS and automatic searches exclude issues already covered by a queued job,
including jobs paused for import review. A different release URL does not make
those issues eligible again. Unknown job coverage holds automatic searches for
that volume until the queue entry is resolved or explicitly removed.

If SABnzbd shows Completed but the comic is missing, inspect the corresponding
Kapowarr Activity row for its import error. Keep that row and the completed
payload while checking remote path mappings and file matching; removing the row
makes its missing issues eligible for automatic download again.
