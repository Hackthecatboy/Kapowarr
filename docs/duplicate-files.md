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

Nothing is deleted, moved or renamed by this scan. It does not inspect archive
pages, so differently compressed archives of the same pages are not called exact
copies. A `(1)` suffix means a naming collision, not proof of identical contents.
Scan the volume in Kapowarr first if you have added files outside the application;
files not indexed in the library are not included.

Scanning is on demand and bounded to 20,000 indexed associations, 2 GiB of hashing,
and a 20-second hashing budget. Limits produce an incomplete report, not an
all-clear. Narrow large scans using the volume ID from `/volumes/<id>`. No hashes
are cached, so subsequent scans recheck current files. Results describe that scan;
files can change afterward. Manual cleanup and automatic import deduplication are
not implemented by this feature.

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
