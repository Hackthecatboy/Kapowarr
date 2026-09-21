"""Usenet worker and conservative completed-file import."""

from hashlib import sha256
from pathlib import Path
from shutil import copy2

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (Constants, DownloadState as DS,
                                      FileConstants)
from backend.base.logging import LOGGER
from backend.features.post_processing import PostProcessingContext
from backend.implementations.file_matching import scan_files
from backend.implementations.managed_job import JobNeedsReview, save_phase
from backend.implementations.volumes import Volume
from backend.internals.db import get_db
from backend.internals.server import (QueueStatusEvent,
                                      RemovedFromQueueEvent, WebSocket)


def import_completed(download):
    source = Path(download.files[0])
    # Copy only regular comic media, preserving originals in the client.
    candidates = []
    for path in source.rglob('*'):
        if path.is_symlink():
            raise JobNeedsReview(
                'Completed folder contains symlinks; inspect it before importing')
        if path.is_file() and path.suffix.lower() in FileConstants.SCANNABLE_EXTENSIONS:
            candidates.append(path)
    if not candidates:
        raise JobNeedsReview('Completed folder contains no supported comic files')
    identity = '{}:{}'.format(download.external_client.id, download.external_id)
    suffix = sha256(identity.encode()).hexdigest()[:16]
    destination = Path(Volume(download.volume_id).vd.folder) / (
        'Kapowarr-{}-{}'.format(download.id, suffix)
    )
    if source.resolve() == destination.resolve() or source.resolve() in destination.resolve().parents:
        raise JobNeedsReview('Library destination overlaps the completed folder')
    if destination.exists():
        raise JobNeedsReview(
            'Import destination already exists; inspect it before retrying')
    save_phase(download.id, 'importing')
    download.phase = 'importing'
    destination.mkdir(parents=True)
    files = []
    for path in candidates:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(path, target)
        files.append(str(target))
    download.files = files
    scan_files(download.volume_id, filepath_filter=files, update_websocket=True)
    cursor = get_db()
    matched_numbers = set()
    for file in files:
        matched_numbers.update(row[0] for row in cursor.execute('''SELECT i.calculated_issue_number FROM files f JOIN issues_files b ON b.file_id = f.id
        JOIN issues i ON i.id = b.issue_id WHERE f.filepath = ? AND i.volume_id = ?''',
                                                                (file, download.volume_id)).fetchall())
    expected = set()
    if download.covered_issues is not None:
        bounds = download.covered_issues if isinstance(download.covered_issues, tuple) else (
            download.covered_issues, download.covered_issues)
        expected = {row[0] for row in cursor.execute(
            'SELECT calculated_issue_number FROM issues WHERE volume_id = ? AND calculated_issue_number BETWEEN ? AND ?', (download.volume_id, *bounds))}
    if not matched_numbers or not expected.issubset(matched_numbers):
        raise JobNeedsReview(
            'Copied files did not match library issues. Inspect the import folder; originals were retained.')
    save_phase(download.id, 'imported')
    download.phase = 'imported'


def run_usenet(handler, download):
    ws = WebSocket()
    started = False
    review = False
    while download.state != DS.SHUTDOWN_STATE:
        try:
            if download.state == DS.CANCELED_STATE:
                # Explicit user cancellation addresses only the tracked remote job.
                download.remove_from_client(delete_files=download.phase != 'imported')
                PostProcessingContext(download).remove_from_queue()
                if download in handler.queue:
                    handler.queue.remove(download)
                ws.emit(RemovedFromQueueEvent(download))
                return
            if review:
                pass
            else:
                if not started:
                    download.run()
                    started = True
                if download.phase != 'imported':
                    download.update_status()
                    if download.state == DS.IMPORTING_STATE:
                        import_completed(download)
                if download.phase == 'imported':
                    # Only successful import permits automatic history cleanup.
                    if handler.settings.sv.delete_completed_downloads:
                        download.remove_from_client(delete_files=False)
                    context = PostProcessingContext(download)
                    download.state = DS.IMPORTING_STATE
                    context.add_to_history()
                    context.remove_from_queue()
                    if download in handler.queue:
                        handler.queue.remove(download)
                    ws.emit(RemovedFromQueueEvent(download))
                    return
                download.error = None
        except JobNeedsReview as error:
            download.error = str(error)
            review = True
            if download.state not in (DS.CANCELED_STATE, DS.SHUTDOWN_STATE):
                download.state = DS.PAUSED_STATE
        except (ClientNotWorking, CredentialInvalid):
            download.error = 'Client connection failed. Check URL, credentials and availability.'
            # Polling/cleanup can retry. A submission with uncertain outcome
            # re-enters submit_once, which holds it instead of submitting twice.
        except Exception:
            LOGGER.error(
                'Usenet job %s needs review after an import or persistence error', download.id)
            download.error = 'Processing failed. Inspect the client and import folder; automatic retry is paused.'
            review = True
            if download.state not in (DS.CANCELED_STATE, DS.SHUTDOWN_STATE):
                download.state = DS.PAUSED_STATE
        ws.emit(QueueStatusEvent(download))
        download.sleep_event.wait(Constants.EXTERNAL_CLIENT_UPDATE_INTERVAL)
        download.sleep_event.clear()
