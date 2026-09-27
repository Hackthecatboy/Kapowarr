"""Persist direct-import review state without re-fetching expired download links."""
import json

from backend.base.definitions import DownloadClientIdentifier, DownloadService, DownloadState
from backend.implementations.download_clients.base import BaseDirectDownload
from backend.internals.db import get_db


def checkpoint(download):
    snapshot = download.as_dict()
    snapshot.update(files=download.files, filename_body=download.filename_body)
    get_db().execute(
        "UPDATE download_queue SET external_phase='ddl_importing', external_token=? WHERE id=?",
        (json.dumps(snapshot), download.id)).connection.commit()
    return snapshot


class HeldDirectImport(BaseDirectDownload):
    """A review-only completed download; never redownload or delete its payload."""
    def __init__(self, snapshot):
        for key, value in snapshot.items():
            setattr(self, '_' + key, value)
        self.identifier = DownloadClientIdentifier(snapshot['type'])
        self._download_service = DownloadService(snapshot['download_service'])
        self._state = DownloadState.PAUSED_STATE
        self._speed = 0
        self._download_thread = None
        self._covered_issues = None

    @property
    def can_forget(self):
        return True

    @property
    def status_detail(self):
        return ('Import needs review. Files were retained; inspect the library and download folder. '
                'Rescan or use Pack Inbox, then remove this queue entry only.')

    def run(self):
        raise RuntimeError('A held import must not be downloaded again')

    def stop(self, state=DownloadState.CANCELED_STATE):
        # Shutdown/removal must not delete imported library files.
        return

    def as_dict(self):
        result = super().as_dict()
        result.update(can_forget=True, can_retry=False, status_detail=self.status_detail)
        return result
