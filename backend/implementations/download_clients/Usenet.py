"""Managed Usenet jobs: no network access during queue reconstruction."""

from pathlib import Path
from threading import Event
from time import monotonic
from typing import Optional, Tuple

from backend.base.custom_exceptions import IssueNotFound
from backend.base.definitions import (DownloadClientIdentifier, DownloadState,
                                      DownloadType, ExternalDownload, FileConstants)
from backend.implementations.download_client_manager import DownloadClients
from backend.implementations.download_clients.base import BaseDirectDownload
from backend.implementations.external_client_manager import ExternalClients
from backend.implementations.managed_job import JobNeedsReview, JobPathNeedsReview, submit_once
from backend.implementations.remote_mapping import RemoteMappings
from backend.implementations.volumes import Volume
from backend.internals.settings import Settings


@DownloadClients.register_client(DownloadClientIdentifier.USENET)
class UsenetDownload(ExternalDownload, BaseDirectDownload):
    download_type = DownloadType.USENET

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
        self.path_review = False
        self.processing_review = False
        self.completed_since: Optional[float] = None
        self.completion_wait_remaining = 0
        self._progress_sample: Optional[Tuple[float, float]] = None
        self.retry_requested = Event()
        self.forget_requested = Event()
        self._state = DownloadState.QUEUED_STATE
        self._progress = self._speed = 0.0
        self._size = -1
        self._download_folder = Settings().sv.download_folder
        self._files = ['']
        # Human-readable only; never used to construct a filesystem path.
        self._title = self._filename_body = web_title or (
            'Torrent release' if self.download_type == DownloadType.TORRENT else 'Usenet release'
        )
        self._sleep_event = Event()
        self._external_client = external_client or ExternalClients.get_least_used_client(
            self.download_type)
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
        # Usenet APIs do not consistently expose per-job speed. Estimate it
        # from this job's downloaded bytes rather than showing client-wide speed.
        sampled_at = monotonic()
        downloaded = max(0, self._size) * self._progress / 100
        previous = self._progress_sample
        if info['state'] == DownloadState.DOWNLOADING_STATE:
            if previous is not None and sampled_at > previous[0] and not self._speed:
                self._speed = max(0, (downloaded - previous[1]) / (sampled_at - previous[0]))
            self._progress_sample = (sampled_at, downloaded)
        else:
            self._progress_sample = None
            self._speed = 0
        if self.state in (DownloadState.CANCELED_STATE, DownloadState.SHUTDOWN_STATE):
            return
        if info['state'] == DownloadState.FAILED_STATE:
            raise JobNeedsReview(
                'Client reports a failed or unsupported terminal state. Inspect its history; files were retained.')
        if info['state'] != DownloadState.IMPORTING_STATE:
            self.completed_since = None
            self.completion_wait_remaining = 0
        if info['state'] == DownloadState.IMPORTING_STATE:
            now = monotonic()
            if self.completed_since is None:
                self.completed_since = now
            delay = Settings().sv.usenet_completion_delay
            self.completion_wait_remaining = max(0, int(delay - (now - self.completed_since) + 0.999))
            if self.completion_wait_remaining:
                self._state = DownloadState.QUEUED_STATE
                return
            storage = info.get('storage')
            if not isinstance(storage, str) or not storage:
                raise JobPathNeedsReview('Completed job has no output path')
            mapped = RemoteMappings.remote_to_local(self.external_client.id, storage)
            root = Path(self.download_folder).resolve()
            path = Path(mapped)
            # Require a job folder or supported comic file under the download
            # root, including after symlink resolution. Never import the root.
            if not path.is_absolute() or path.is_symlink():
                raise JobPathNeedsReview(f'Completed path must be an absolute, non-symlink job folder or comic file. Client: {storage}; mapped: {mapped}; download folder: {root}')
            resolved = path.resolve()
            supported_output = resolved.is_dir() or (
                resolved.is_file()
                and resolved.suffix.lower() in FileConstants.SCANNABLE_EXTENSIONS
            )
            if root not in resolved.parents or not supported_output:
                raise JobPathNeedsReview(
                    f'Completed path is unavailable, unsupported, or outside the download folder. Client: {storage}; mapped: {resolved}; download folder: {root}. Check mounts and remote mappings.')
            self._files = [str(resolved)]
        self._state = info['state']

    def remove_from_client(self, delete_files):
        if self.external_id:
            self.external_client.delete_download(self.external_id, delete_files)

    def stop(self, state=DownloadState.CANCELED_STATE):
        self._state = state
        self._sleep_event.set()

    @property
    def can_retry(self):
        return (self.state == DownloadState.PAUSED_STATE and (self.path_review or self.processing_review)
                and bool(self.external_id) and self.phase == 'submitted'
                and not self.retry_requested.is_set()
                and not self.forget_requested.is_set())

    @property
    def can_forget(self):
        return (self.state == DownloadState.PAUSED_STATE and bool(self.error)
                and not self.retry_requested.is_set()
                and not self.forget_requested.is_set())

    @property
    def status_detail(self):
        return (f'Waiting {self.completion_wait_remaining}s before importing completed files'
                if self.completion_wait_remaining else None)

    def as_dict(self):
        return {**super().as_dict(), 'client': self.external_client.id,
                'external_id': self.external_id, 'error': self.error,
                'can_retry': self.can_retry, 'can_forget': self.can_forget,
                'status_detail': self.status_detail}
