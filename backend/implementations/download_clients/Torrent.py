"""Managed torrents with persisted identity, ownership and client-reported paths."""

from pathlib import Path
from uuid import uuid4

from backend.base.definitions import (Constants, DownloadClientIdentifier,
                                      DownloadState as DS, DownloadType)
from backend.implementations.download_client_manager import DownloadClients
from backend.implementations.download_clients.Usenet import UsenetDownload
from backend.implementations.managed_job import (JobNeedsReview,
                                                 JobPathNeedsReview,
                                                 submit_once)
from backend.implementations.remote_mapping import RemoteMappings
from backend.implementations.torrent_support import resolve_torrent
from backend.internals.db import get_db


@DownloadClients.register_client(DownloadClientIdentifier.TORRENT)
class TorrentDownload(UsenetDownload):
    download_type = DownloadType.TORRENT
    token = ''

    def as_dict(self):
        result = super().as_dict()
        # Tracked torrents are excluded from category discovery, but their
        # completed payloads may still contain files needing Pack Inbox review.
        result.update(
            client_id=self.external_client.id,
            torrent_hash=self.external_id,
            can_review=bool(self.external_id) and self.uses_category_paths
            and self.state in (DS.SEEDING_STATE, DS.IMPORTING_STATE)
            and self.phase != 'importing'
        )
        return result

    @property
    def target_folder(self):
        return Path(self.download_folder) / ('kapowarr-' + self.token)

    def run(self):
        cursor = get_db()
        row = cursor.execute(
            'SELECT external_token FROM download_queue WHERE id = ?', (self.id,)).fetchone()
        if row is None:
            raise JobNeedsReview('Queue entry is missing')
        self.token = row[0]
        if not self.token:
            self.token = uuid4().hex
            cursor.execute(
                "UPDATE download_queue SET external_token = ? WHERE id = ? AND external_token = ''", (self.token, self.id))
            cursor.connection.commit()
            self.token = cursor.execute(
                'SELECT external_token FROM download_queue WHERE id = ?', (self.id,)).fetchone()[0]
        self._external_id, self.phase = submit_once(self)
        if self.phase == 'importing':
            raise JobNeedsReview(
                'Import was interrupted. Inspect the library copy; torrent data was retained.')

    def prepare_submission(self):
        if getattr(self, 'payload', None) is None:
            self.payload = resolve_torrent(self.download_link)
        if self.external_client.get_download(self.payload.info_hash) is not None:
            raise JobNeedsReview(
                'This torrent already exists in the client. It was not adopted or modified.')

    def submit_download(self):
        target = RemoteMappings.local_to_remote(
            self.external_client.id, str(self.target_folder))
        return self.external_client.add_torrent(self.payload, target, 'kapowarr-' + self.token)

    @property
    def uses_category_paths(self):
        return getattr(type(self.external_client), 'uses_category_paths', False)

    def content_root(self, info):
        """Return the local boundary for the client's completed content."""
        if self.uses_category_paths:
            return Path(RemoteMappings.remote_to_local(
                self.external_client.id, info['save_path'])).resolve()
        return self.target_folder.resolve()

    def owns(self, info):
        if self.uses_category_paths:
            path = info.get('save_path')
            if (not self.external_id or self.phase not in ('submitted', 'importing', 'imported')
                    or info.get('category') != Constants.EXTERNAL_DOWNLOAD_TAG
                    or not isinstance(path, str) or not path):
                return False
            local = Path(RemoteMappings.remote_to_local(self.external_client.id, path))
            root = Path(self.download_folder).resolve()
            return (local.is_absolute() and not local.is_symlink()
                    and (local.resolve() == root or root in local.resolve().parents))
        if (not self.token or self.target_folder.is_symlink()
                or Path(self.download_folder).resolve() not in self.target_folder.resolve().parents
                or 'kapowarr-' + self.token not in info.get('tags', [])):
            return False
        path = info.get('save_path')
        if not isinstance(path, str) or not path:
            return False
        local = RemoteMappings.remote_to_local(self.external_client.id, path)
        return Path(local).is_absolute() and Path(local).resolve() == self.target_folder.resolve()

    def update_status(self):
        info = self.external_client.get_download(self.external_id)
        if info is None:
            self._missing_polls = getattr(self, '_missing_polls', 0) + 1
            if self._missing_polls <= 3 and self.phase != 'imported':
                return  # Some clients expose a newly added magnet asynchronously.
            raise JobNeedsReview(
                'Tracked torrent is missing. It will not be resubmitted.')
        self._missing_polls = 0
        if not self.owns(info):
            raise JobNeedsReview(
                'Torrent ownership or save path changed. Inspect the client; it will not be modified automatically.')
        self._progress, self._speed, self._size = info['progress'], info['speed'], info['size']
        if self.state in (DS.CANCELED_STATE, DS.SHUTDOWN_STATE):
            return
        if info['state'] == DS.FAILED_STATE:
            raise JobNeedsReview(
                'Client reports a torrent error. Check the client; payload files were retained.')
        if info['state'] in (DS.IMPORTING_STATE, DS.SEEDING_STATE):
            storage = info.get('storage')
            if not isinstance(storage, str) or not storage:
                raise JobPathNeedsReview('Client did not report a completed content path')
            local = Path(RemoteMappings.remote_to_local(
                self.external_client.id, storage))
            if not local.is_absolute() or local.is_symlink() or not local.exists() or self.content_root(info) not in local.resolve().parents:
                raise JobPathNeedsReview(
                    f'Completed content is unavailable or outside its allowed download location. Client: {storage}; mapped: {local}; content root: {self.content_root(info)}. Check mounts and mappings.')
            self._files = [str(local.resolve())]
        self._state = info['state']

    def remove_from_client(self, delete_files):
        if self.external_id:
            info = self.external_client.get_download(self.external_id)
            if info is None:
                return
            if not self.owns(info):
                raise JobNeedsReview(
                    'Cleanup refused: torrent ownership or path changed')
            self.external_client.delete_download(self.external_id, delete_files)

    def cancel_remote(self):
        # A local queue entry can be removed without claiming an unrelated job.
        if self.external_id:
            info = self.external_client.get_download(self.external_id)
            if info is not None and self.owns(info):
                self.external_client.delete_download(
                    self.external_id, self.phase != 'imported')
