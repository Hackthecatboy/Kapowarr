"""qBittorrent Web API adapter with local torrent identity and explicit states."""

import re
from contextlib import contextmanager

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, Constants,
                                      DownloadState as DS, DownloadType,
                                      ExternalClientField as ECF)
from backend.implementations.external_client_manager import (
    BaseExternalClient, ExternalClients)
from backend.implementations.torrent_support import (decode_json, http,
                                                     invalid, resolve_torrent,
                                                     session)
from backend.implementations.usenet_support import entries, number


@ExternalClients.register_client(DownloadType.TORRENT, 'qBittorrent',
                                 (ECF.TITLE, ECF.ENABLED, ECF.BASE_URL, ECF.USERNAME, ECF.PASSWORD))
class qBittorrent(BaseExternalClient):
    @staticmethod
    @contextmanager
    def _login(base_url, username, password):
        with session() as ssn:
            ssn.headers['Referer'] = base_url.rstrip('/') + '/'
            if username or password:
                reply = http(ssn, 'POST', base_url + '/api/v2/auth/login',
                             data=dict(username=username or '', password=password or ''))
                if reply[0] != 200 or reply[2].strip() != b'Ok.':
                    raise CredentialInvalid
            yield ssn

    @classmethod
    def test(cls, base_url, username=None, password=None, api_token=None):
        with cls._login(base_url, username, password) as ssn:
            version = http(ssn, 'GET', base_url + '/api/v2/app/version')
            match = re.match(rb'v(\d+)\.(\d+)\.(\d+)', version[2].strip())
            if version[0] != 200 or not match:
                raise invalid()
            if tuple(int(v) for v in match.groups()) < (4, 3, 9):
                raise ClientNotWorking(BrokenClientReason.VERSION_NOT_SUPPORTED)
            entries(decode_json(http(ssn, 'GET', base_url +
                    '/api/v2/torrents/info', params={'limit': 1})))
            categories = decode_json(
                http(ssn, 'GET', base_url + '/api/v2/torrents/categories'))
            if not isinstance(categories, dict):
                raise invalid()
            if Constants.EXTERNAL_DOWNLOAD_TAG not in categories:
                raise ClientNotWorking(BrokenClientReason.CATEGORY_NOT_FOUND)

    def _request(self, method, path, **kwargs):
        with self._login(self.base_url, self.username, self.password) as ssn:
            reply = http(ssn, method, self.base_url + '/api/v2/' + path, **kwargs)
            if reply[0] != 200:
                raise invalid()
            return reply

    def add_torrent(self, payload, target_folder, tag):
        data = dict(savepath=target_folder, category=Constants.EXTERNAL_DOWNLOAD_TAG,
                    tags=tag, autoTMM='false')
        kwargs = {'data': data}
        if payload.metainfo:
            kwargs['files'] = {'torrents': (
                'release.torrent', payload.metainfo, 'application/x-bittorrent')}
        else:
            data['urls'] = payload.magnet
        reply = self._request('POST', 'torrents/add', **kwargs)
        if reply[2].strip() != b'Ok.':
            raise invalid()
        return payload.info_hash

    def add_download(self, download_link, target_folder, download_name):
        return self.add_torrent(resolve_torrent(download_link), target_folder, Constants.EXTERNAL_DOWNLOAD_TAG)

    def get_download(self, download_id):
        jobs = entries(decode_json(self._request(
            'GET', 'torrents/info', params={'hashes': download_id})))
        job = next((j for j in jobs if str(j.get('hash', '')).lower()
                   == download_id.lower()), None)
        if job is None:
            return None
        phase = job.get('state')
        done = number(job.get('progress')) >= 1 and job.get('amount_left') == 0
        state = DS.DOWNLOADING_STATE
        if phase in ('error', 'missingFiles'):
            state = DS.FAILED_STATE
        elif phase in ('pausedDL', 'stoppedDL'):
            state = DS.PAUSED_STATE
        elif phase == 'queuedDL':
            state = DS.QUEUED_STATE
        elif done and phase in ('pausedUP', 'stoppedUP'):
            state = DS.IMPORTING_STATE
        elif done and phase in ('uploading', 'forcedUP', 'stalledUP', 'queuedUP'):
            state = DS.SEEDING_STATE
        # Metadata, allocating, checking and unknown states are never importable.
        return dict(state=state, size=int(number(job.get('total_size'))),
                    progress=min(100, number(job.get('progress')) * 100),
                    speed=number(job.get('dlspeed')), storage=job.get('content_path'),
                    save_path=job.get('save_path'), tags=[t.strip() for t in str(job.get('tags', '')).split(',')])

    def delete_download(self, download_id, delete_files):
        self._request('POST', 'torrents/delete',
                      data={'hashes': download_id, 'deleteFiles': str(bool(delete_files)).lower()})

    def on_shutdown(self):
        pass
