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
   Scan**. Wait at least a minute after files finish changing before scanning.
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
  preserve the reviewed association. Originals are never renamed, moved or deleted.
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

Stage 1 is a manually triggered folder inbox with reviewed import. Scheduled
scans, recurring weekly-pack indexer rules, automatic download/ingestion, archive
unpacking and automatic creation of unknown series are not implemented.

Filesystem/database tests cover multi-series routing, checksum-preserving copies,
owned/ambiguous/unmatched files, stale previews, changed sources, symlinks, folder
boundaries, held/interrupted copies, repeat scans, authentication and migration.
UI and desktop/mobile layout checks pass. Live Synology pack import is pending.
