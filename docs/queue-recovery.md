# Queue recovery and Synology test

Implemented for managed Usenet and torrent jobs. The user confirmed the recovery walkthrough working on Synology. Use the
isolated development project when repeating these checks.

## What the controls do

- **Retry Import** rechecks an existing tracked job after a pre-import path
  failure. It never submits another download. The button is only offered for a
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
