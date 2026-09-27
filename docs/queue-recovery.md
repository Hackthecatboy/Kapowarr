# Queue recovery and Synology test

Implemented for managed Usenet and torrent jobs. The user confirmed the recovery walkthrough working on Synology. Use the
isolated development project when repeating these checks.

## What the controls do

- **Retry Import** rechecks an existing tracked job after a pre-import path
  failure or an unexpected processing exception before copying began. It never submits another download. The button is only offered for a
  paused path review with a recorded job ID and a submitted phase.
- Saving a remote mapping automatically requests the same recheck for eligible
  paused jobs belonging to that client. Saving does not guarantee import succeeds.
- **Remove from queue only** removes a paused review entry from Kapowarr without
  contacting the client, deleting files, or recording a successful import.
  It does not undo library files from a partial import.
- Existing Remove and Remove-and-blocklist actions retain their cancellation
  behavior. Use the explicitly labeled queue-only action to preserve the job.

Interrupted/partial imports, uncertain submissions, missing jobs and torrent
ownership failures stay held for manual review. They cannot be replayed through
Retry Import. There is no automatic rollback of library copies.

## Recommended test: recover a completed SABnzbd job

1. Update only the isolated Kapowarr development container to the recovery build.
   Record its current SABnzbd mapping. Use one test issue not already imported.
2. Temporarily turn off **Settings → Download → Delete Completed Downloads**
   so SABnzbd history remains available for inspection after successful import.
3. In this Kapowarr instance, change SABnzbd's mapping remote prefix from
   `/Media/downloads/` to `/Media/recovery-test/`, leaving its local path as
   `/app/temp_downloads/`. This intentionally prevents the real completed path
   from matching. Do not change SABnzbd's folders or NAS mounts.
4. Download the test issue once. Wait for SABnzbd to finish. Kapowarr should
   pause with a message showing the client path, mapped path and allowed folder.
   **Retry Import** and **Remove from queue only** should appear.
5. With the mapping still wrong, click **Retry Import** once. It should pause
   again with a path error. SABnzbd should still have the same single job; there
   should be no new library copy or second download.
6. Restore `/Media/downloads/` → `/app/temp_downloads/` and save. Without
   restarting, the paused job should recheck, import and leave the queue.
7. Confirm one imported comic is bound to the intended library issue, the
   original completed file still exists, and SABnzbd has no duplicate job.
8. Restart the development container. The imported job should not return or
   import again. Restore the Delete Completed Downloads preference if desired.

These prefixes describe the current test setup. Other installations must use
their actual client and container paths.

## Optional test: retain a job while removing its queue entry

With another test job paused at the deliberate path error, choose **Remove from
queue only** and confirm. Verify the queue row disappears, SABnzbd still has the
completed job, and the original file remains. Restart and verify the queue row
does not reappear. Restore the correct mapping afterward. Removing tracking does
not create a way to adopt that client job again automatically.

## Automated checks and remaining runtime coverage

Regression tests cover retrying the same external job after a mapping fix,
client-specific wakeup, rejecting uncertain/partial-import retries, retaining
client data on queue-only removal for both workers, and API authentication/action
validation. Existing import-boundary, ownership and restart tests also run.

The qBittorrent download/import/continued-seeding walkthrough remains a separate
roadmap milestone. Do not treat a SABnzbd recovery pass as torrent verification.

## Completion delay and processing retries

Settings → Download → **Usenet Completion Delay** defaults to 30 seconds
(range 0–3600; 0 disables it). The timer starts when Kapowarr first observes
SABnzbd/NZBGet completion. While waiting, Activity displays the remaining delay.
Import checks happen at the normal polling interval, so the actual wait can be
longer. Restarting or explicitly retrying starts a fresh delay. This is a grace
period for completed files to become available, not proof that files are stable.

A generic exception before the import enters its copying phase now offers
**Retry Import** on Activity. It reuses the same remote job; it does not submit
another download. There is no automatic loop for these exceptions. Errors after
copying starts, uncertain submissions and client ownership failures remain held.
The traceback remains available in System → Logs. Saving a mapping automatically
rechecks only path failures, not unrelated processing exceptions.

On the development container, set the delay to 60 seconds and download one test
issue. Check that the queue waits after client completion and imports once.
For the existing bad-mapping test, allow the delay to expire before expecting
Retry Import. Restore the mapping and check that the same job imports after its
new delay. Confirm the completed client file is retained and no duplicate job
was submitted. A successful restart alone does not establish the original cause
of a processing failure; capture its traceback if it recurs.

## Direct-download imports (GetComics and file hosts)

Direct imports now retain a persistent queue checkpoint before moving files. The
queue entry and successful history record are finalized only after import finishes.
If import fails or the server restarts during import, the job is held for review;
it does not fetch the original download URL again. Inspect the download and volume
folders and logs, rescan successfully imported files, then use **Remove from queue
only**. This removes the tracking entry without deleting files. Partial imports
are not automatically replayed; use Library Import or Pack Inbox for recovery.

Existing destination files are preserved using numbered collision names during
placement, extraction and extension-only conversion. Mixed/unmatched archive comics
stop extraction before any member is moved: both the archive and extraction folder
remain for review. A previous extraction folder must be reviewed before another
attempt. This does not automatically enroll mixed packs in Pack Inbox.

Development checks (use expendable fixtures in the test library):

1. Import a file whose destination name already exists. Verify the existing bytes
   are unchanged and the incoming file has a distinct name.
2. Cause a conversion/import failure. Verify the job stays paused after restart,
   even if its download URL expires; inspect and recover the files, then remove
   the queue entry only. Verify that removal leaves the files intact.
3. Extract a ZIP with one matching comic and one other series. Verify neither is
   discarded and the ZIP remains available for manual pack review.
