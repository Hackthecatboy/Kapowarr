# Import naming and duplicate review

Settings → Media Management → **Rename Downloaded Files** applies the existing
naming formats to direct downloads and now to verified Usenet, torrent and Pack
Inbox library copies. It is enabled by default. Disable it to retain incoming
names. External client and inbox originals are not renamed, preserving seeding.
Import naming keeps the current volume folder; use the explicit volume rename
workflow to change folder naming. Existing files are not automatically renamed.
Library Import retains its own Rename Files choice.

Volumes → **Duplicate Files** scans indexed library comic archives and PDFs:

- **Identical contents:** same size and SHA-256 hash, even if names differ or end
  in `(1)`. Hard-linked paths to one physical file are labelled separately.
- **Same issue:** multiple registered files bound to an issue that are different
  or not checksum-verified. These may be different releases, editions or overlapping
  packs; this is not an instruction to delete them.
- Missing, changed, symlinked and inaccessible files are reported for review.

Scanning does not delete, move or rename files. Exact groups now offer explicit deletion after review. It does not inspect archive
pages, so differently compressed archives of the same pages are not called exact
copies. A `(1)` suffix means a naming collision, not proof of identical contents.
Scan the volume in Kapowarr first if you have added files outside the application;
files not indexed in the library are not included.

Scanning is on demand and bounded to 20,000 indexed associations, 2 GiB of hashing,
and a 20-second hashing budget. Limits produce an incomplete report, not an
all-clear. Narrow large scans using the volume ID from `/volumes/<id>`. No hashes
are cached, so subsequent scans recheck current files. Results describe that scan;
files can change afterward. Automatic import deduplication is not implemented by this feature.

## Development validation

1. Import one test issue with Rename Downloaded Files enabled. Verify the library
   filename follows the configured pattern, the issue stays linked, and the source
   file name/content are unchanged. Repeat disabled with another missing issue.
2. For Pack Inbox, check the final path in its result and confirm rescanning does
   not import the same entry again.
3. In a test volume, add an identical copy with `(1)` and a different file bound to
   the same issue; rescan the volume. Duplicate Files should report the identical
   pair separately from the other same-issue candidates. Confirm it changes nothing.
4. While a larger SABnzbd or qBittorrent job downloads, leave Activity open. Progress
   should update roughly every five seconds; Usenet speed is an estimate between
   samples. Refresh/reconnect the page and confirm tracking continues without
   duplicate rows. Completion delay and held imports may remain at 100% deliberately.

## Delete verified duplicates

Expand an exact group, select **Keep** beside the copy to retain, check **Delete**
beside individual copies to remove,
and choose **Delete Selected Duplicates**. The confirmation lists the paths to
remove and the path to retain. No deletion boxes are checked by default. This is
permanent deletion of library paths, not a recycle bin operation.

Groups start collapsed. Each file appears in one row, with a shared folder shown
once. Files and groups sort naturally; an unsuffixed copy precedes `(1)`, `(2)`,
and `(10)`. The first copy is the initial keeper; choosing another keeper clears
and disables its Delete checkbox.

Kapowarr rehashes the retained and selected files, checks their paths and current
issue bindings, and refuses stale/changed files, symlinks, cross-volume removal,
or loss of issue/metadata coverage. A volume with queued downloads or an ongoing
Pack Inbox import must finish or resolve those entries first. Different or unverified same-issue files require the manual edition review below. At least the chosen keeper remains;
no client download folders are targeted. Deleting a hard link removes that path,
not the retained path. Multiple linked copies may require a fresh scan after their
filesystem metadata changes.

Previews expire after 15 minutes or a server restart. After deletion or any error,
scan again. Filesystem deletion and database cleanup cannot form one atomic
transaction: if a filesystem/database error occurs, processing stops and reports
partial results; rescan the volume to reconcile missing file records if needed.

Test first with an expendable identical copy in the development library: confirm
only the selected path and its database binding disappear, the keeper still opens,
and its issue remains linked. Then modify a copy after scanning and confirm deletion
is rejected until a new scan. No existing library files are deleted during development
or automated testing; tests use temporary fixtures.

## Choose between different editions

Same-issue groups (such as a CBR and a CBZ of the same issue) also offer Keep and
Delete controls. No keeper or deletion is selected initially. Inspect your files,
choose **Keep**, select the unwanted editions, then **Delete Selected Editions**.
The confirmation explicitly warns that contents have not been verified identical;
format and file size do not establish page quality, completeness or extras.

This is a manual edition decision, not page-level duplicate detection. Unhashed
files are checked against their scan-time filesystem identity, size and timestamps;
previously hashed files are also rehashed. Changed paths, issue bindings, active
imports/downloads and additional issue coverage block deletion. Only selected
library files are deleted; external download originals remain untouched.

Validate with two expendable files of different contents/extensions linked to the
same issue: ensure no keeper is preselected, cancellation changes nothing, and
confirmation removes only the selected edition. A file covering an additional
issue must be refused unless the retained file covers that issue too.

## Leftover download archives

Successful ZIP-to-folder conversion already removes the source ZIP after scanning
and renaming the extracted files. Conversion errors can leave it behind alongside
partially imported comics; inspect System → Logs for `Conversion failed for` and
the archive path before retrying. Extraction-only results are now returned and
rescanned even when the extracted comics need no further format conversion.
This does not sweep old ZIPs or delete external torrent/client originals.
