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
   named **kapowarr**. Connection testing verifies the category exists.
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

Every submission has a random ownership token, client tag/label and dedicated
`kapowarr-<token>` save folder. Remote mappings apply before submission and when
reading the client's reported completed path. Both single-file and multi-file
content are supported. Ownership and the dedicated save path are checked before
import or deletion. A torrent already in the client is not adopted or modified.

The journal persists the returned hash and submission phase. Lost submission
responses pause for review rather than being retried. Newly added magnets get a
short visibility grace period; persistently missing jobs are held, not re-added.
Existing queued torrents from older builds lack recorded identity and are held
for review on upgrade. Inspect those in the client instead of blindly requeueing.

Metadata fetches, rechecks, moves, unknown states and incomplete stopped torrents
never trigger import. Successful imports copy supported media to a unique library
subfolder, preserve filenames and use the normal comic scanner. Automatic rename
and conversion are not connected to this copy importer yet. Original torrent
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
They use local fixtures and temporary files. Live qBittorrent/Transmission,
Prowlarr, swarm transfers and Synology checks remain pending.

API references: [qBittorrent Web API](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0)),
[Transmission RPC](https://github.com/transmission/transmission/blob/main/docs/rpc-spec.md).

Release selection filters and ranking: [download preferences](download-preferences.md).
