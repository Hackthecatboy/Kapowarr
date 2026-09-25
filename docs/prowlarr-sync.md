# Prowlarr import and refresh

This development slice imports selected indexers from one saved Prowlarr
connection. It uses Prowlarr's [indexer API](https://github.com/Prowlarr/Prowlarr/blob/develop/src/Prowlarr.Api.V1/openapi.json)
and creates ordinary Newznab or Torznab entries in Kapowarr. Synchronization is
one-way and manually triggered; it does not add Kapowarr to Prowlarr's Applications
menu or alter Prowlarr configuration.

## Setup and test in the Synology development project

1. Update the isolated development container to the Prowlarr import build.
2. Open **Settings → Indexers → Prowlarr Import**. Enter Prowlarr's base address
   as reachable from Kapowarr (for example `http://prowlarr:9696`) and the API key
   from Prowlarr Settings → General. Include a reverse-proxy prefix if used;
   do not append `/api/v1`, an indexer ID, query parameters or the API key.
3. Leave Categories at `7030` for comics, choose other supported category IDs,
   or leave blank for all categories. This is a search filter for selected local
   imports; it does not change Prowlarr's category configuration.
4. Click **Preview Indexers**. Preview is read-only and does not save the key.
   New entries are unchecked; existing managed entries are selected for refresh.
   Manual duplicates and unsupported protocols cannot be selected.
5. Select one new test indexer and click **Import / Refresh Selected**. Verify
   the created count and its new card under Torrent or Usenet Indexers. If all
   entries already exist manually, the preview should skip them instead of
   creating duplicates. Test a new indexer without deleting working manual ones.
6. Preview again and refresh the managed entry. Verify Updated increases and no
   duplicate card appears. Run the indexer's normal connection test and a manual
   comic search; preview success only proves access to Prowlarr's configuration.
7. Restart Kapowarr, return to this page, and preview again. The connection and
   ownership should persist. The password field stays blank; a blank field keeps
   the saved key for the same address.

Optional, using only an indexer safe to change in your Prowlarr setup: rename or
disable it there, then preview and refresh here. Verify Kapowarr follows the
change. Changes in Prowlarr can affect other connected applications too.

## Adding indexers later

The connection, API key and category filter are saved after import. Return to
**Settings → Indexers → Prowlarr Import** and click **Refresh Indexer List**.
Leave the key field blank to reuse the saved key. Select any new entries, then
click **Import / Refresh Selected**. Refreshing the list is read-only; it does not
automatically import newly added indexers.

## Ownership and refresh rules

- Imported entries are tracked by Prowlarr indexer ID and local indexer ID.
  Refresh preserves the local ID and existing release references.
- Selected managed entries receive Prowlarr's name (prefixed `Prowlarr:`),
  protocol, enabled state, generated API URL, key and the form's category filter.
  Local edits to these fields will be overwritten when that entry is selected.
- Manual entries with the same normalized endpoint are skipped, never adopted or
  changed. Other manual entries, including GetComics, remain unchanged.
- A managed indexer absent from a successful full Prowlarr snapshot, disabled in
  Prowlarr, or changed to an unsupported protocol is disabled locally even if it
  is not selected. It is not deleted. Reappearing/enabled entries must be selected
  for refresh to re-enable them.
- Other unselected entries keep their settings. Select every managed entry when
  rotating the shared key or changing their category filter.
- Empty selection still saves the connection and reconciles missing/disabled
  managed entries. An empty valid Prowlarr list disables managed entries.
- Failed authentication, redirects, network errors and malformed/oversized
  responses do not change saved settings or disable existing entries. A database
  failure rolls back the whole import.
- Deleting an imported card removes its management record; it may be imported
  again later. Replacing the Prowlarr address is blocked while managed entries
  remain, because a different server can reuse remote IDs. This first slice does
  not support multiple connections, address relocation or an adoption workflow.

The key is stored in the local database, like other indexer credentials. The
connection and preview endpoints do not return it. This feature does not repair
upstream indexer failures such as the current Bitmag problem.

## Validation status

Automated checks cover preview without writes, saved-key reuse/rotation, both
protocols, stable IDs, manual duplicates, selected updates, disable/re-enable,
malformed responses, authentication, migration, rollback and deletion ownership.
Browser checks cover selection, safe labels, stale previews, failures and layout.
The user confirmed Prowlarr import working on the NAS. Later-indexer refresh
and reconciliation still need live verification.
