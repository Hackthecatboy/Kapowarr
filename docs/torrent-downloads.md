# Torznab and torrent downloads in the development fork

Current priorities and verification status: [development roadmap](../ROADMAP.md).

Torznab now supports manual downloads, automatic search and RSS through
**qBittorrent 4.3.9+** or **Transmission 4.1+**. GetComics torrent links also use
the managed torrent runner. Deluge and the remaining client catalog are still
planned; this is not the full Sonarr-client release.

The adapter supports both older `Ok.` login/submission responses and
qBittorrent 5.2's HTTP 204 login/removal and structured submission responses.
Login endpoint access denial and rejection of a subsequent API session are
reported separately from an explicit credentials failure.

## Prepare the isolated Container Manager test

The [GHCR setup guide](synology-ghcr.md) describes running the public development
image in Container Manager. The [local build guide](synology-development.md)
remains available. Use a separate database, library and download folder for the test.

1. Enable the torrent client's Web UI/API. For qBittorrent, create a category
   named **kapowarr** and set its save path to the shared torrent download
   location. New submissions enable Automatic Torrent Management and use that
   category path; Kapowarr no longer supplies tags or overrides the save path.
   Connection testing verifies the category exists.
2. Mount the test download directory into both containers. With the supplied
   compose file, Kapowarr sees it as `/app/temp_downloads`. The torrent client
   may use `/downloads`; configure a Remote Path Mapping from its `/downloads/`
   to Kapowarr's `/app/temp_downloads/` in that case. Both paths must refer to
   the same NAS files. Kapowarr and the client need write/read permissions.
3. Add the client under Settings → Download Clients. Enter its base URL and
   Web UI credentials. Transmission's base URL excludes `/transmission/rpc`;
   that suffix is supplied by the adapter. Test and save.
4. Add your Prowlarr per-indexer Torznab endpoint, API key and comic categories
   under Settings → Indexers. See [the indexer guide](znab-indexers.md).
5. For a quick test, select **Copy** under Seeding Handling. This imports a copy
   once the client reports complete content while allowing seeding to continue.
   **Complete** waits until the client stops seeding before importing. Configure
   ratio/time limits in the client itself; the fork does not override them.
6. Search and download a small comic release you are authorized to obtain.
   Confirm the queue shows the client state, the library issue gains its file,
   and the original remains available for seeding. Restart the fork and confirm
   it reconnects without adding a second torrent or importing another copy.

## Discover torrents added directly in qBittorrent

Assign the **kapowarr** category to a torrent in an enabled qBittorrent client.
Open **Activity → Queue**: untracked category members appear alongside managed
jobs with their current status and progress. Discovery refreshes while Activity
is open, with client reads cached for 15 seconds. Existing tracked hashes are
excluded per client. Repeated refreshes do not create database queue entries or
submit anything to qBittorrent.

Completed torrents offer **Review Files**. This rechecks the category and completed
state, maps the content path and opens a Pack Inbox scan. A single-file torrent
scans only that file; a collection scans its own folder, up to the inbox's existing
2,000-file limit. Choose a smaller completed subfolder manually for larger packs.
Select matches and import in Pack Inbox. Unknown series and ambiguous editions
remain for review. Nothing is automatically imported.

Discovered torrents have no delete, blocklist or reorder controls. **Remove All**
affects managed queue entries only. Imported originals stay in qBittorrent for
seeding. To hide a discovered torrent after review, remove/change its category in
qBittorrent; Kapowarr will not change it for you. Imported issues remain protected
by the inbox journal and already-owned checks on subsequent review.

The completed content must be visible beneath Kapowarr's configured download
folder after remote mapping. Missing paths, symlinks, library overlap and managed
pack-folder overlap are rejected. Category membership enables discovery and
explicit review, not ownership-based automatic import or client cleanup.

Local tests cover discovery, repeat suppression, client failures, path boundaries,
manual single-file review and source preservation. Live NAS validation is pending.

## Torrent and lifecycle behavior

The fork accepts HTTP(S) torrent downloads and v1 magnets with hexadecimal or
base32 info hashes. Redirects, including redirects to magnets, are bounded.
Torrent metadata is parsed locally and the exact info-dictionary bytes are
hashed. No magnet-to-torrent conversion website is contacted. Full torrent
metadata (including private tracker information) is uploaded to the configured
client; magnets are passed intact. Pure v2 torrents are not supported yet;
hybrid torrents must include valid v1 metadata. Endpoint cookies and extra
indexer authentication headers are not configured in this slice: use the
Prowlarr/indexer download URL returned in the search result.

qBittorrent uses the **kapowarr** category for placement. Its reported save path
must map to Kapowarr's configured download folder or a subfolder; completed
content must be inside that save path. A recorded submission hash and the category
are required before import or deletion. Category membership alone never adopts a
pre-existing torrent. Tags are not required. Existing tracked jobs are not moved
or retagged by this update and can continue using their previous per-job folders.

Transmission retains its per-job ownership label and dedicated
`kapowarr-<token>` folder. Remote mappings apply to client-reported paths in both
adapters. Both single-file and multi-file content are supported.

Queue admission is serialized and compares resolved torrent hashes against
tracked jobs, including alternate magnet encodings, changed tracker/title fields,
and HTTP mirrors. Known hashes from persisted client IDs survive restart; failed
metadata resolution keeps the existing review handling. Existing duplicate rows
are not automatically deleted. Use **Remove from queue only** on rejected rows to
retain the original client job and its files.

The journal persists the returned hash and submission phase. Lost submission
responses pause for review rather than being retried. Newly added magnets get a
short visibility grace period; persistently missing jobs are held, not re-added.
Existing queued torrents from older builds lack recorded identity and are held
for review on upgrade. Inspect those in the client instead of blindly requeueing.

Metadata fetches, rechecks, moves, unknown states and incomplete stopped torrents
never trigger import. Successful imports copy supported media to a unique library
subfolder and use the normal comic scanner. Rename Downloaded Files then applies
the configured naming formats to library copies. Conversion is not connected to
this copy importer yet. Original torrent
payloads remain intact. In Copy mode, the queue continues tracking seeding after
import; the persisted import phase prevents a second copy after restarting.

Delete Completed Downloads removes the tracked torrent record only after import
and after the client reports that seeding has stopped; original payload files
are retained. Explicit cancellation may remove an owned unfinished torrent and
its data. Unowned or changed jobs are left untouched when the local queue entry
is removed. No operation clears a whole queue or category.

Interrupted imports, inaccessible paths, changed ownership and client failures
are explained in the queue. Review the files before retrying an interrupted
import; there is no automatic rollback. Copies and client payloads are preserved
for that review.

## Verification

Tests cover metadata/redirect parsing, authenticated HTTP uploads and RPC session
challenges, client states, stored search-to-prepper routing, ownership, duplicate
and missing jobs, single-file import, both seeding modes, and restart behavior.
They use local fixtures and temporary files. qBittorrent transfers have been
observed on Synology; category-based placement, repeat suppression under automatic
search, and a complete import while seeding still need live confirmation.
Transmission live validation also remains pending.

API references: [qBittorrent Web API](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0)),
[Transmission RPC](https://github.com/transmission/transmission/blob/main/docs/rpc-spec.md).

Release selection filters and ranking: [download preferences](download-preferences.md).

External jobs poll every five seconds. Activity refreshes every five seconds while
visible and after a WebSocket reconnect; qBittorrent/Transmission supply progress
and speed. This is polling, so the display may lag the client by a polling interval.

### Reviewing tracked collections

Completed qBittorrent downloads in Activity offer **Review Files**, including
those added through Prowlarr and already tracked by Kapowarr. This opens the
original completed payload in Pack Inbox so mixed collections can be matched
across library series. The action is unavailable while the managed import is
copying files. Completion, category and remote path checks run again on click;
review does not remove or modify seeding originals.
