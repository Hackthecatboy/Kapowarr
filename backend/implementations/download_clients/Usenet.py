"""Managed Usenet jobs: no network access during queue reconstruction."""

from pathlib import Path
from threading import Event

from backend.base.custom_exceptions import IssueNotFound
from backend.base.definitions import (DownloadClientIdentifier, DownloadState,
                                      DownloadType, ExternalDownload)
from backend.implementations.download_client_manager import DownloadClients
from backend.implementations.download_clients.base import BaseDirectDownload
from backend.implementations.external_client_manager import ExternalClients
from backend.implementations.managed_job import JobNeedsReview, submit_once
from backend.implementations.remote_mapping import RemoteMappings
from backend.implementations.volumes import Volume
from backend.internals.settings import Settings


@DownloadClients.register_client(DownloadClientIdentifier.USENET)
class UsenetDownload(ExternalDownload, BaseDirectDownload):
    @property
    def external_client(self):
        return self._external_client

    @external_client.setter
    def external_client(self, value):
        self._external_client = value

    @property
    def external_id(self):
        return self._external_id

    @property
    def sleep_event(self):
        return self._sleep_event

    def __init__(self, download_link, volume_id, covered_issues, download_service,
                 source_name, web_link, web_title, web_sub_title, forced_match=False,
                 external_client=None):
        self._download_link = self._pure_link = download_link
        self._volume_id, self._covered_issues = volume_id, covered_issues
        self._download_service, self._source_name = download_service, source_name
        self._web_link, self._web_title, self._web_sub_title = web_link, web_title, web_sub_title
        self._id = self._issue_id = self._download_thread = None
        self._external_id = None
        self.phase = 'queued'
        self.error = None
        self._state = DownloadState.QUEUED_STATE
        self._progress = self._speed = 0.0
        self._size = -1
        self._download_folder = Settings().sv.download_folder
        self._files = ['']
        # Human-readable only; never used to construct a filesystem path.
        self._title = self._filename_body = web_title or 'Usenet release'
        self._sleep_event = Event()
        self._external_client = external_client or ExternalClients.get_least_used_client(
            DownloadType.USENET)
        if isinstance(covered_issues, float):
            try:
                self._issue_id = Volume(
                    volume_id).get_issue_from_number(covered_issues).id
            except IssueNotFound:
                if not forced_match:
                    raise

    def run(self):
        self._external_id, self.phase = submit_once(self)
        if self.phase == 'importing':
            raise JobNeedsReview(
                'Import was interrupted. Inspect library files before removing this entry; the client payload was retained.')

    def update_status(self):
        info = self.external_client.get_download(self.external_id)
        if not info:
            raise JobNeedsReview(
                'Tracked job is missing from the client. Check queue and history; it will not be resubmitted.')
        self._progress, self._speed, self._size = info['progress'], info['speed'], info['size']
        if self.state in (DownloadState.CANCELED_STATE, DownloadState.SHUTDOWN_STATE):
            return
        if info['state'] == DownloadState.FAILED_STATE:
            raise JobNeedsReview(
                'Client reports a failed or unsupported terminal state. Inspect its history; files were retained.')
        if info['state'] == DownloadState.IMPORTING_STATE:
            storage = info.get('storage')
            if not isinstance(storage, str) or not storage:
                raise JobNeedsReview('Completed job has no output path')
            mapped = RemoteMappings.remote_to_local(self.external_client.id, storage)
            root = Path(self.download_folder).resolve()
            path = Path(mapped)
            # Require an individual job folder under the configured download
            # root, including after symlink resolution. Never import the root.
            if not path.is_absolute() or path.is_symlink():
                raise JobNeedsReview('Completed path must be an absolute job folder')
            resolved = path.resolve()
            if root not in resolved.parents or not resolved.is_dir():
                raise JobNeedsReview(
                    'Completed folder is unavailable or outside the download folder. Check mounts and remote mappings.')
            self._files = [str(resolved)]
        self._state = info['state']

    def remove_from_client(self, delete_files):
        if self.external_id:
            self.external_client.delete_download(self.external_id, delete_files)

    def stop(self, state=DownloadState.CANCELED_STATE):
        self._state = state
        self._sleep_event.set()

    def as_dict(self):
        return {**super().as_dict(), 'client': self.external_client.id,
                'external_id': self.external_id, 'error': self.error}
