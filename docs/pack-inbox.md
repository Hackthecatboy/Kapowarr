# Pack inbox: mixed-series and weekly folders

Open **Volumes → Pack Inbox** to scan a completed folder, review filename matches,
and copy selected comics into series already in the library. This also works for
packs downloaded outside Kapowarr. It does not require a tracked downloader job.

## First Synology test

Use the isolated development container and a small completed test folder:

1. In File Station, create `/volume1/Media/downloads/pack-inbox` (or use another
   dedicated completed folder). Put individual comic files from two different
   series into a weekly subfolder. Those series/issues must already exist in the
   development library and be missing their files. Include one unmatched file to
   verify review behavior. Copy test files; leave torrent-managed originals alone.
2. Add this line to the development project's existing `volumes:` list:

   ```yaml
   - /volume1/Media/downloads/pack-inbox:/pack-inbox:ro
   ```

   Use your actual NAS path. The read-only source mount is supported; normal
   library mounts must remain writable. Recreate the development container after
   updating its image and Compose configuration.
3. Open **Volumes → Pack Inbox**, enter `/pack-inbox`, and select **Save Folder &
   Scan**. Wait at least 30 seconds after files finish changing before scanning.
4. Inspect the rows. Unique missing-issue matches are selectable. Unknown series,
   ambiguous matches, outer archives and recently modified files stay in review.
   Existing files are marked owned. Nothing is copied during a scan.
5. Select the two matching comics, then **Import Selected Copies**. Verify both
   rows become imported, each issue shows a file in its own library series, and
   the original files still exist. The importer verifies copied bytes by SHA-256.
   Refresh an already-open volume page to see the new issue bindings.
6. Scan again and restart the container. Imported files must stay imported and
   not produce another library copy. Unmatched files remain available for review.

The folder is saved. **Refresh Results** reads saved results; **Save Folder & Scan**
examines the filesystem again. Imports are limited to 100 selected files per
request and scans to 2,000 candidate files. Choose a smaller completed weekly
folder for larger inboxes.

## Matching and preservation

- Each CBZ, CBR, CB7 or PDF filename is matched independently. The weekly folder's
  title/date is not used as a comic identity. Nested completed folders are scanned.
- Matching uses the existing library title/alternate title, annual distinction,
  explicit issue number/range, volume number when present, and publication or
  series year when present. Missing year information can match only if the other
  metadata produces a unique candidate. No series are added automatically.
- Ordinary single issues and explicit issue ranges can match. Special editions,
  unclear numbering and multiple plausible series require review. A range must
  have matching endpoints in the library.
- Only missing issues are imported. A multi-issue file covering an already-owned
  issue is held out; individual missing comic files elsewhere in the same pack
  remain selectable. Monitoring is not a filter for this explicit manual import.
- Copies go to a unique `Pack-Inbox-<token>` subfolder inside the matched volume.
  Exact issue bindings use Kapowarr's existing forced-match flag, so later rescans
  preserve the reviewed association. External inbox originals are never renamed, moved or deleted. Extracted sources
  from Kapowarr-managed packs are cleaned after verified import (see below).
- Inbox and library folders must not overlap. Symlinked content is not imported.
  Changed files and changed/now-owned library matches require a fresh review.
- Download-search preferences do not filter this manual inbox; the files are
  already downloaded and explicitly selected for import.

## Archives and held copies

Outer ZIP/RAR/7z pack archives are listed for review rather than unpacked. Extract
those packs into a completed folder outside the library first, preserving any
original needed for seeding. Loose page-image sets and metadata sidecars are not
imported by this slice. Individual comic archives are copied intact.

The database journals copy progress before writing. If a copy or database update
fails, or the application stops mid-import, the entry stays held/importing. Its
source and any partial library copy are retained. Inspect the displayed destination;
there is no automatic retry, rollback, or review-override button for those entries.
Unmatched filenames can be corrected in a separate inbox copy, or their series
added through the normal library UI, followed by another scan.

Imported and held paths do not automatically reset even if their contents change.
The journal prevents replay for the same source path, including when narrowing the
inbox to its weekly subfolder. Content deduplication across renamed files or alternate
mount aliases and broader recurring-scan recovery remain part of stage 2.

## Scope and verification

Stage 1 is a manually triggered folder inbox with reviewed import. An explicit
GetComics article download flow is now available as described below. Scheduled inbox scans, automatic ingestion and automatic creation of unknown series
remain future work. Article subscriptions can now discover and download new dated
packs; importing is still reviewed.

Filesystem/database tests cover multi-series routing, checksum-preserving copies,
owned/ambiguous/unmatched files, stale previews, changed sources, symlinks, folder
boundaries, held/interrupted copies, repeat scans, authentication and migration.
UI and desktop/mobile layout checks pass. Live Synology pack import is pending.

## Naming imported copies

Settings → Media Management → Rename Downloaded Files also applies to Pack Inbox.
After checksum verification and issue binding, the new library copy is renamed
using the existing naming formats. The journal records its final path. External
inbox sources are untouched; managed-pack sources can then be cleaned. Disable the setting to preserve incoming names. Rename failures stay
held for review and do not replay the copy automatically.

Use Volumes → Duplicate Files to review indexed library duplicates. This does not
change the inbox's rules for already-owned issues or add content deduplication to
recurring pack scans. See [duplicate review](duplicate-files.md).

## Download a GetComics weekly pack

In **Volumes → Pack Inbox → Download a GetComics pack**:

1. Paste the HTTPS `getcomics.org` weekly-pack article URL and enter an existing
   writable inbox folder, such as `/pack-inbox`. For downloading, change the mount
   example above to `/volume1/Media/downloads/pack-inbox:/pack-inbox` (remove `:ro`).
   Library and inbox roots must remain separate.
2. Select **Preview Download Links**. Pick one pack link/mirror, using its group
   and service label. An article may contain alternatives or separate parts;
   do not select every mirror. The preview expires after 15 minutes.
3. Watch progress below the form. This separate pack job has no volume assignment
   and does not appear in the single-series download queue. One pack downloads
   at a time. The same article/link cannot be submitted twice, even after restart.
4. ZIP packs extract into `Pack-<job-id>/ready`, retaining `payload.archive` beside
   that folder. Comic archives within the ZIP are kept intact. After completion,
   wait 30 seconds, select **Scan Pack for Review**, and import reviewed matches
   using the existing inbox controls. No files are automatically imported.

The first slice reuses GetComics link discovery/resolution and HTTP download
providers (GetComics direct links, MediaFire, WeTransfer and Pixeldrain). Mega and
BitTorrent pack jobs are not supported yet. RAR, 7z, multipart and other unsupported
payloads are retained for manual extraction into a separate completed folder.
Archives are not deleted merely by importing; use Finish Pack when done reviewing.

Jobs survive restarts as held records, without automatic re-download. Partial
payloads and failed extractions stay in their job folder; inspect logs before
manual recovery. Inbox scans exclude unfinished/held managed pack folders, including
when scanning a parent folder. Extract or copy reviewed content into a separate
completed inbox folder to recover it. Automatic retry is not provided. Finish Pack can remove the retained job files
after review. Link deduplication does not identify alternate mirrors of the same pack.

Downloads/extraction are limited to 50 GiB and ZIPs to 2,000 entries. Unsafe ZIP
paths, symlinks, duplicate entries and encrypted entries are rejected. Traversal
checks happen before extraction, and existing extraction folders are never reused.

Fixture tests cover article validation, authenticated endpoints, persisted job
submission, duplicate suppression, interrupted jobs, ZIP extraction, unsafe paths,
truncated downloads, source preservation and exclusion of unfinished scans. Live
GetComics hosting behavior still needs verification with a small test pack.

## Add a missing series from review

Unmatched rows offer **Find / Add Series**, opening the existing Add Volume search
in a new tab with the parsed series title. Select the correct series/edition and
root folder there. Return to Pack Inbox and click **Save Folder & Scan** to rerun
matching; Refresh Results alone only reads the previous scan.

A missing match can also mean missing issue metadata or a title/year mismatch in
an existing series. Search shows already-added series; refresh that volume's
metadata instead of adding a duplicate. No series is added automatically.

## Clean imported managed-pack files

After a successful import from a Kapowarr-downloaded pack, its individual extracted
source file is automatically deleted. Cleanup rechecks the source size/timestamp,
current library path and issue bindings, and SHA-256 equality with the final library
copy after naming. Changed/missing copies or filesystem errors retain the source
and show a cleanup-review message while keeping the import marked imported.

Previously imported rows with retained managed sources offer **Delete Imported
Source**. It uses the same verification, so you can clean the files imported before
this update without importing them again. Scan the original pack folder to see its
saved imported rows. Cleanup is per file; unmatched comics, the outer archive and
folders are preserved. External/manual inbox folders remain copy-only to protect
read-only mounts and torrent-managed originals.

## Finish a pack and reclaim space

Each ready or held job offers **Finish Pack / Delete Remaining Files**. It previews
all files and their total size, then asks you to confirm permanent deletion of the
outer archive and every remaining extracted file—including unselected/unmatched
comics. Library copies and imported issue bindings are untouched. Cancel changes
nothing. Changed files invalidate the preview; active downloads, in-progress import
records, symlinks and nested pack jobs block cleanup. A partial cleanup error keeps
the job held; inspect remaining files and preview again. Finished jobs stay in the
history to prevent the same link being downloaded again.

## Find older packs

Expand **Find past packs and subscribe to future packs** and search article title
words such as `weekly pack`. **Older Results** loads subsequent search pages.
Choose **Preview Links**, then select a mirror using the normal download form.
Older packs are never downloaded in bulk just because they appear in search.

## Subscribe to future packs

1. Set the article title words (for example `weekly pack`).
2. Set **Download label contains** to identifying text from the article preview,
   such as `Marvel`, and choose one supported service.
3. Set the writable inbox **root** in the download form—not a prior job's `ready`
   subfolder. Choose automatic download for review, or list for manual download.
4. Choose **Check every** (Monday through Sunday), then click **Subscribe Using
   These Settings**. Checks run once on that weekday, using the server/container
   timezone. Existing subscriptions default to Sunday; change their weekday with
   **Save Day**. **Check Subscriptions Now** checks enabled subscriptions immediately,
   regardless of their scheduled weekday.

The scheduler checks whether subscriptions are due hourly at minute 15, but only
contacts GetComics once on the selected day. The attempt is saved across restarts,
including failed checks; use the manual check button to retry sooner. If the server
is offline throughout that day, the next automatic attempt is the following week.
Manual checks do not consume the scheduled weekly check.

Automatic mode considers dated article titles from the subscription's creation
DATE onward (including releases dated today). It checks at most the first three
search pages each time. All query words must occur in the article title. Older,
undated or future-dated articles remain available for manual review. Exactly one
supported link must match the selected service and label text; zero/multiple matches
are listed for manual choice. A subscription downloads one selected bundle per
article, not every publisher/part/mirror. Split/multipart packs require manual handling.

Already tracked articles—including finished or held pack jobs—are not downloaded
again automatically. If a pack is active, new releases wait for a subsequent check.
Pause/Resume controls stop or resume future checks; they do not cancel an already
started download. Imports always require Pack Inbox review. Recurring automatic
import, automatic retry, and discovering arbitrarily deep history remain outside
this slice. Live subscription discovery still needs a test against current hosting.
