"""Durable submission boundary shared by managed download integrations."""

from backend.internals.db import get_db


class JobNeedsReview(Exception):
    """No automatic retry is safe at this boundary."""


def submit_once(download):
    cursor = get_db()
    row = cursor.execute('SELECT external_id, external_phase FROM download_queue WHERE id = ?',
                         (download.id,)).fetchone()
    if row is None:
        raise JobNeedsReview('Queue entry is missing')
    job_id, phase = row
    if job_id:
        return job_id, phase
    if phase != 'queued':
        raise JobNeedsReview(
            'Submission outcome is uncertain. Check the client before removing or retrying this entry.')
    # Commit before contacting the client. A crash or timeout after submission
    # must not turn into a second submission on restart.
    changed = cursor.execute("UPDATE download_queue SET external_phase = 'submitting' WHERE id = ? AND external_phase = 'queued' AND external_id IS NULL",
                             (download.id,)).rowcount
    cursor.connection.commit()
    if changed != 1:
        raise JobNeedsReview('Submission is already in progress')
    job_id = download.external_client.add_download(download.download_link, download.download_folder,
                                                   download.title)
    if not isinstance(job_id, str) or not job_id:
        raise JobNeedsReview(
            'Client did not return a job ID. Check its queue before retrying.')
    cursor.execute("UPDATE download_queue SET external_id = ?, external_phase = 'submitted' WHERE id = ?",
                   (job_id, download.id))
    cursor.connection.commit()
    return job_id, 'submitted'


def save_phase(download_id, phase):
    cursor = get_db()
    cursor.execute(
        'UPDATE download_queue SET external_phase = ? WHERE id = ?', (phase, download_id))
    cursor.connection.commit()
