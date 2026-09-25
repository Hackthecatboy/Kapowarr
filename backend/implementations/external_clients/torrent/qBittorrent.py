"""qBittorrent Web API adapter with local torrent identity and explicit states."""

import re
from contextlib import contextmanager

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.logging import LOGGER
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
                try:
                    reply = http(ssn, 'POST', base_url + '/api/v2/auth/login',
                                 data=dict(username=username or '', password=password or ''))
                except CredentialInvalid:
                    LOGGER.warning('qBittorrent login endpoint denied access (HTTP 401/403)')
                    raise ClientNotWorking(BrokenClientReason.LOGIN_ACCESS_DENIED) from None
                if reply[0] == 200 and reply[2].strip() == b'Fails.':
                    raise CredentialInvalid
                # 5.2 returns HTTP 204 without a body; older versions return "Ok.".
                # Subsequent API requests must still pass session authentication.
                accepted = ((reply[0] == 200 and reply[2].strip() in (b'Ok.', b''))
                            or (reply[0] == 204 and not reply[2]))
                if not accepted:
                    LOGGER.warning(
                        'Unexpected qBittorrent login response (HTTP %s); credentials and response body omitted',
                        reply[0])
                    raise invalid()
            try:
                yield ssn
            except CredentialInvalid:
                LOGGER.warning(
                    'qBittorrent API session rejected after login or authentication bypass (HTTP 401/403)')
                raise ClientNotWorking(BrokenClientReason.SESSION_REJECTED) from None

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

    def _request(self, method, path, accepted_statuses=(200,), **kwargs):
        with self._login(self.base_url, self.username, self.password) as ssn:
            reply = http(ssn, method, self.base_url + '/api/v2/' + path, **kwargs)
            if reply[0] not in accepted_statuses:
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
        reply = self._request('POST', 'torrents/add', accepted_statuses=(200, 202), **kwargs)
        if reply[2].strip() != b'Ok.':
            # 5.2 returns a structured submission result instead of "Ok.".
            result = decode_json((200, reply[1], reply[2]))
            if not isinstance(result, dict) or result.get('failure_count') != 0:
                raise invalid()
            added = result.get('added_torrent_ids')
            complete = (reply[0] == 200 and result.get('success_count') == 1
                        and result.get('pending_count') == 0
                        and isinstance(added, list) and len(added) == 1
                        and isinstance(added[0], str)
                        and added[0].lower() == payload.info_hash.lower())
            pending = (reply[0] == 202 and result.get('success_count') == 0
                       and result.get('pending_count') == 1 and added == [])
            if not (complete or pending):
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
                      accepted_statuses=(200, 204),
                      data={'hashes': download_id, 'deleteFiles': str(bool(delete_files)).lower()})

    def on_shutdown(self):
        pass
