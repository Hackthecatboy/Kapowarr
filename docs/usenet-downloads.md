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
removing the job. A paused path/mapping error can be retried by correcting the
configuration and restarting this development instance.

Imports copy regular supported media into a unique `Kapowarr-<id>-<job-hash>`
subfolder in the volume folder, preserve filenames, and use the existing library
scanner. A range must match its expected library issues before import succeeds.
Existing destinations and symlinked payloads are refused. Original client files
are retained. Automatic renaming and conversion are not connected to this
Usenet import path yet. Per-job speed is currently reported as zero rather than
attributing the client's global speed to each job.

If an import is interrupted, the entry pauses instead of replaying file writes.
Inspect its library subfolder and client payload; recover or remove partial
copies and rescan the library as appropriate. There is no automatic import
rollback or resume button in this slice.

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
