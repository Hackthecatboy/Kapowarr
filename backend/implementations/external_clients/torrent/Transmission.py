"""Transmission 4.1+ JSON-RPC adapter; session-ID retry is bounded."""

import base64
import posixpath
import re

from backend.base.custom_exceptions import ClientNotWorking
from backend.base.definitions import (BrokenClientReason, Constants,
                                      DownloadState as DS, DownloadType,
                                      ExternalClientField as ECF)
from backend.implementations.external_client_manager import (
    BaseExternalClient, ExternalClients)
from backend.implementations.managed_job import JobNeedsReview
from backend.implementations.torrent_support import (decode_json, http,
                                                     invalid, resolve_torrent,
                                                     session)
from backend.implementations.usenet_support import entries, number


@ExternalClients.register_client(DownloadType.TORRENT, 'Transmission',
                                 (ECF.TITLE, ECF.ENABLED, ECF.BASE_URL, ECF.USERNAME, ECF.PASSWORD))
class Transmission(BaseExternalClient):
    @staticmethod
    def _request(base_url, username, password, method, params):
        with session() as ssn:
            ssn.auth = (username or '', password or '')
            payload = dict(jsonrpc='2.0', method=method, params=params, id=1)
            for _ in range(2):
                response = http(ssn, 'POST', base_url +
                                '/transmission/rpc', json=payload)
                if response[0] == 409:
                    sid = response[1].get('X-Transmission-Session-Id')
                    if not sid:
                        raise invalid()
                    ssn.headers['X-Transmission-Session-Id'] = sid
                    continue
                result = decode_json(response)
                if not isinstance(result, dict) or result.get('error') or result.get('id') != 1 or not isinstance(result.get('result'), dict):
                    raise invalid()
                return result['result']
        raise invalid()

    def _call(self, method, params):
        return self._request(self.base_url, self.username, self.password, method, params)

    @classmethod
    def test(cls, base_url, username=None, password=None, api_token=None):
        result = cls._request(base_url, username, password, 'session_get', {
                              'fields': ['rpc_version_semver']})
        version = result.get('rpc_version_semver')
        if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise invalid()
        if tuple(int(v) for v in version.split('.')) < (6, 0, 0):
            raise ClientNotWorking(BrokenClientReason.VERSION_NOT_SUPPORTED)

    def add_torrent(self, payload, target_folder, tag):
        params = dict(download_dir=target_folder, paused=False, labels=[tag])
        if payload.metainfo:
            params['metainfo'] = base64.b64encode(payload.metainfo).decode('ascii')
        else:
            params['filename'] = payload.magnet
        result = self._call('torrent_add', params)
        if 'torrent_duplicate' in result:
            raise JobNeedsReview(
                'This torrent already exists in Transmission; it was not adopted')
        added = result.get('torrent_added')
        if not isinstance(added, dict) or str(added.get('hash_string', '')).lower() != payload.info_hash:
            raise invalid()
        return payload.info_hash

    def add_download(self, download_link, target_folder, download_name):
        return self.add_torrent(resolve_torrent(download_link), target_folder, Constants.EXTERNAL_DOWNLOAD_TAG)

    def get_download(self, download_id):
        result = self._call('torrent_get', {'ids': [download_id], 'fields': [
            'hash_string', 'total_size', 'percent_done', 'rate_download', 'status', 'error',
            'left_until_done', 'metadata_percent_complete', 'download_dir', 'name', 'labels']})
        jobs = entries(result.get('torrents'))
        job = next((j for j in jobs if str(j.get('hash_string', '')).lower()
                   == download_id.lower()), None)
        if job is None:
            return None
        phase = job.get('status')
        done = (job.get('left_until_done') == 0 and number(job.get('percent_done')) >= 1
                and number(job.get('metadata_percent_complete')) >= 1)
        state = DS.DOWNLOADING_STATE
        if job.get('error'):
            state = DS.FAILED_STATE
        elif phase == 0:
            state = DS.IMPORTING_STATE if done else DS.PAUSED_STATE
        elif phase == 3:
            state = DS.QUEUED_STATE
        elif done and phase in (5, 6):
            state = DS.SEEDING_STATE
        name, directory = job.get('name'), job.get('download_dir')
        storage = None
        if isinstance(name, str) and isinstance(directory, str) and name not in ('', '.', '..') and '/' not in name and '\\' not in name:
            storage = posixpath.join(directory, name)
        return dict(state=state, size=int(number(job.get('total_size'))),
                    progress=min(100, number(job.get('percent_done')) * 100),
                    speed=number(job.get('rate_download')), storage=storage,
                    save_path=directory, tags=job.get('labels', []))

    def delete_download(self, download_id, delete_files):
        self._call('torrent_remove', {
                   'ids': [download_id], 'delete_local_data': bool(delete_files)})

    def on_shutdown(self):
        pass
