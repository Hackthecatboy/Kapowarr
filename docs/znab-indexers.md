# Newznab and Torznab development setup

This build supports configuring and manually searching Newznab (Usenet) and
Torznab (torrents). Newznab downloads are connected to SABnzbd/NZBGet; see
[Usenet setup and limitations](usenet-downloads.md). Torznab downloads now use qBittorrent/Transmission; see
[the torrent setup guide](torrent-downloads.md).
GetComics continues to use its existing download path.

1. Open Settings → Indexers. Add Newznab under Usenet or Torznab under Torrents.
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

Each indexer can have a separate configuration. Edit an entry to change its
settings or delete it. Existing Kapowarr HTTP(S) proxy and bypass settings apply
to requests. Indexer requests do not go through FlareSolverr.

Both protocols can download after configuring a matching client and shared
folders. Search metadata is retained for enqueue matching; run a fresh search
if an older result is unavailable. Enabled indexers also participate in
automatic searches and RSS discovery.

Prowlarr import/synchronization and an entry in its Applications menu are not
implemented. Live Prowlarr/indexer and Synology verification are still pending;
automated checks use protocol fixtures and an isolated in-memory database.
