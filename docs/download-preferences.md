# Comic release preferences

Configure **Settings → Download → Release Preferences**, then Save. These are
global search preferences, separate from library conversion/renaming settings.
Defaults preserve existing behavior: no size limits, format preference or terms.

## Selection rules

- **Minimum/Maximum Release Size (MiB):** integer limits for the entire release,
  including multi-issue packs. Zero disables that bound. Exact boundary sizes
  pass. Unknown/zero sizes remain eligible and are explained in manual search.
- **Preferred Formats:** comma-separated `cbz`, `cbr`, `pdf`, best first. Only
  explicit format tokens in the release title count; extensions hidden in URL
  tokens are not evidence of a format. Unknown, ambiguous and unlisted formats
  remain eligible, below known preferred formats.
- **Preferred Release Terms:** literal, case-insensitive whole-term matches in
  the title rank higher. These can be group names such as `Marika-Empire`.
  This matches title text; it is not a verified release-group metadata parser.
- **Excluded Release Terms:** matching titles are rejected. Exclusions win over
  preferences. Terms are comma-separated, not regular expressions.

Eligible matches rank before rejected results, then preferred format order,
then number of preferred terms matched, then Kapowarr's existing relevance
ranking. Automatic issue searches and RSS use the same preferences. Size and
term rejections do not stop other query variants from being attempted.

Manual search displays preference and rejection explanations below the title.
Uncheck **Matches only** to inspect rejected releases. Newznab/Torznab enqueue
checks the current preferences again so a stale result cannot silently bypass
new rules; explicit Force Download retains its override behavior.

## Synology test without another download

1. Update the development container and run manual search for an issue with a
   known result and size. Note the result title and size.
2. Set Maximum Release Size to `1` MiB, save, and repeat that search. A result
   larger than 1 MiB should disappear under Matches only. Uncheck it and confirm
   the maximum-size rejection message.
3. Restore Maximum to `0`. Add a distinctive term from that release title to
   Excluded Release Terms, save and search again. Confirm the exclusion reason.
4. Remove the exclusion and add it to Preferred Release Terms. Confirm the
   preferred-term explanation and ranking among otherwise eligible releases.
5. If results explicitly name formats, set `cbz, cbr` and confirm their ordering.
   If titles omit formats, expect an unknown-format explanation, not a guessed
   preference bonus.
6. Refresh the settings page or restart the container and verify preferences
   persist. Restore your intended settings when finished.

Automated tests cover boundaries, unknown metadata, format order, term matching,
exclusion precedence, ranking, RSS selection, enqueue revalidation/override,
settings persistence and invalid-setting rejection. UI and desktop/mobile layout
checks pass. The user confirmed preferences working on the NAS.

## Upgrade boundary

Automatic searches still fill missing issues only. They do not replace existing
library files because a release scores better. Stored quality information,
upgrade cutoffs and safe replacement/rollback are a separate future slice. This
also avoids interfering with torrent originals that may still be seeding.

## Source order and fallback

Settings → Download → **Source Order** controls source groups. Choose
**Usenet → Torrents → Direct downloads** to make GetComics a fallback, or choose
an order beginning with Direct downloads to prefer it. Indexers within a group
have equal source priority. The default, **Search all sources together**, keeps
the previous behavior.

Automatic search completes one group before querying the next. Only issues
covered by selected, matching, downloadable results are removed from the next
group's search. If earlier groups cover everything, later groups are not queried.
Manual search still queries all enabled sources and ranks matches by source
order before format/term preferences. RSS still checks all enabled sources and
uses the order when choosing between results available in that sync. This does
not wait for a preferred source to publish a future release.

This is search fallback, not failed-download replacement: a selected release
that later fails to download or import does not automatically trigger another
source. Existing owned-issue and partial-pack safeguards still apply.

### Development test

1. Choose Usenet → Torrents → Direct downloads and save; reload to confirm it persists.
2. Manually search an issue available on both NZB and GetComics. Both should
   appear, with matching Usenet results ahead of matching GetComics results.
3. Auto-search a missing issue available on Usenet. Confirm the chosen source
   and, using Debug logs, that no GetComics query was sent for that search.
4. Test a missing issue absent from earlier sources but available on GetComics;
   confirm fallback finds it. Reverse the order to verify GetComics can be preferred.
5. Restore Info logging after testing.
