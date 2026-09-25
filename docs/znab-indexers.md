# Newznab and Torznab development setup

For Prowlarr, you can use [import and refresh](prowlarr-sync.md) instead of
entering each indexer endpoint manually.

This build supports configuring and manually searching Newznab (Usenet) and
Torznab (torrents). Newznab downloads are connected to SABnzbd/NZBGet; see
[Usenet setup and limitations](usenet-downloads.md). Torznab downloads now use qBittorrent/Transmission; see
[the torrent setup guide](torrent-downloads.md).
GetComics continues to use its existing download path.
Kapowarr automatically adds and enables GetComics on first startup. To use only
your own indexers, open Settings → Indexers → GetComics, clear Enable and save.

1. Open Settings → Indexers. Add Newznab under Usenet Indexers or Torznab under Torrent Indexers.
2. Enter a title and the full API URL supplied by the indexer. For a Prowlarr
   indexer, use its per-indexer Newznab/Torznab endpoint, including any URL base
   and indexer path; do not enter just the Prowlarr home-page address.
3. Put the API key in the separate API Key field. Do not append query parameters
   or credentials to the URL.
4. Enter comma-separated category IDs. The default is `7030` (Comics); use IDs
   supported by that indexer. An empty field searches all categories.
5. Click Test, then Add. Test checks connectivity, authentication, and advertised
   generic search support; it does not prove the indexer contains comic releases.
6. Open a library volume and run manual search to check results and matching.

Manual search defaults to **Matches only**, hiding rejected results. Uncheck it
to inspect all results and rejection reasons. The choice persists in this browser.

Search tracks missing issues separately for each enabled indexer. A match from
GetComics or another source does not stop other indexers from trying their
fallback queries. If a known NZB is missing, compare its category with the saved
Categories filter; temporarily clearing that filter can help diagnose category
mismatches. Debug logs show each Znab query, category filter and result count,
without logging API keys or download URLs.

To inspect those messages in the application, open System → Logs and set
Record logs to Debug. Repeat the search, then use Refresh or enable auto-refresh
and filter for `Indexer`. Show selects a severity without changing what the server
records. Auto-refresh and display filters persist in this browser; the recording
level is saved on the server. The viewer shows recent lines;
Download Logs includes the full retained log files. Return the log level to Info
when finished. The Categories field is pre-filled with `7030` when adding a
Znab indexer; leaving that value unchanged saves the filter.

Named API keys, passwords, authorization credentials and signed Prowlarr link
tokens are redacted from new console/file logs and from log viewing/downloads.
Older files are sanitized when read; their originals on disk are not rewritten.

Each indexer can have a separate configuration. Edit an entry to change its
settings or delete it. Existing Kapowarr HTTP(S) proxy and bypass settings apply
to requests. Indexer requests do not go through FlareSolverr.

Both protocols can download after configuring a matching client and shared
folders. Search metadata is retained for enqueue matching; run a fresh search
if an older result is unavailable. Enabled indexers also participate in
automatic searches and RSS discovery.

Selected Prowlarr import and manual refresh are now available; see
[the Prowlarr guide](prowlarr-sync.md). Native registration in its Applications
menu is still not implemented. The owner has confirmed successful connection tests for Prowlarr
Newznab/Torznab, qBittorrent and SABnzbd on the NAS. Live search, download and
import verification remain in progress; automated checks use protocol fixtures
and an isolated in-memory database.

Release selection filters and ranking: [download preferences](download-preferences.md).
