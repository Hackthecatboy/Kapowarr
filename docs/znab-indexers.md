# Newznab and Torznab development setup

This build supports configuring and manually searching Newznab (Usenet) and
Torznab (torrents). Downloads from these providers are not connected yet.
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

Search results have disabled download/force-download buttons with a search-only
explanation. Automatic searches and RSS downloads exclude these providers until
the download pipeline is implemented. Direct API attempts to enqueue these
results also fail without adding them to the queue or blocklist.

Prowlarr import/synchronization and an entry in its Applications menu are not
implemented. Live Prowlarr/indexer and Synology verification are still pending;
automated checks use protocol fixtures and an isolated in-memory database.
