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
