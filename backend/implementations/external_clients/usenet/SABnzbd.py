# SPDX-License-Identifier: GPL-3.0
# Queue/history interpretation adapted from silasfelinus/Kapowarr at 2a283b1d.
# Reworked for v1.3.2 registration, bounded transport and strict validation 2026-09-20.

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (BrokenClientReason, DownloadState as DS,
                                      DownloadType, ExternalClientField as ECF)
from backend.implementations.external_client_manager import (
    BaseExternalClient, ExternalClients)
from backend.implementations.usenet_support import (CATEGORY, entries,
                                                    invalid_response, number,
                                                    request_json, status)


@ExternalClients.register_client(
    DownloadType.USENET, 'SABnzbd',
    (ECF.TITLE, ECF.ENABLED, ECF.BASE_URL, ECF.API_TOKEN)
)
class SABnzbd(BaseExternalClient):
    @staticmethod
    def _request(base_url, api_token, mode, **params):
        args = dict(mode=mode, apikey=api_token or '', output='json', **params)
        result = request_json(base_url, '/api', data=args)
        if not isinstance(result, dict):
            raise invalid_response()
        if result.get('error') or result.get('status') is False:
            error = str(result.get('error', '')).lower()
            if 'api key' in error or 'apikey' in error:
                raise CredentialInvalid
            raise invalid_response()
        return result

    def _call(self, mode, **params):
        return self._request(self.base_url, self.api_token, mode, **params)

    @staticmethod
    def _slots(result, mode):
        section = result.get(mode)
        if not isinstance(section, dict):
            raise invalid_response()
        return entries(section.get('slots'))

    @classmethod
    def test(cls, base_url, username=None, password=None, api_token=None):
        # version is unauthenticated; queue proves the full API key works.
        result = cls._request(base_url, api_token, 'queue', limit=1)
        cls._slots(result, 'queue')
        if not isinstance(result['queue'].get('version'), str) or not result['queue']['version']:
            raise invalid_response()
        categories = cls._request(base_url, api_token, 'get_cats').get('categories')
        if not isinstance(categories, list):
            raise invalid_response()
        if CATEGORY not in categories:
            raise ClientNotWorking(BrokenClientReason.CATEGORY_NOT_FOUND)

    def add_download(self, download_link, target_folder, download_name):
        result = self._call('addurl', name=download_link, cat=CATEGORY,
                            nzbname=download_name or '', pp=3, priority=0)
        ids = result.get('nzo_ids')
        if result.get('status') is not True or not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            raise invalid_response()
        return ids[0]

    def get_download(self, download_id):
        queue = self._slots(self._call('queue', nzo_ids=download_id), 'queue')
        job = next((j for j in queue if j.get('nzo_id') == download_id), None)
        if job is not None:
            state = {'Paused': DS.PAUSED_STATE, 'Queued': DS.QUEUED_STATE}.get(
                job.get('status'), DS.DOWNLOADING_STATE)
            return status(state, number(job.get('mb')) * 1024**2, job.get('percentage'))
        history = self._slots(self._call(
            'history', nzo_ids=download_id, failed_only=0), 'history')
        job = next((j for j in history if j.get('nzo_id') == download_id), None)
        if job is None:
            return None
        phase = job.get('status')
        if phase == 'Completed':
            return status(DS.IMPORTING_STATE, job.get('bytes'), 100, job.get('storage'))
        if phase == 'Failed':
            return status(DS.FAILED_STATE, job.get('bytes'))
        # Repair, verification, extraction, moves and scripts are not completion.
        return status(DS.DOWNLOADING_STATE, job.get('bytes'), 100)

    def delete_download(self, download_id, delete_files):
        # Only address the tracked ID and the section where it currently exists.
        for mode in ('queue', 'history'):
            jobs = self._slots(self._call(mode, nzo_ids=download_id), mode)
            if any(j.get('nzo_id') == download_id for j in jobs):
                self._call(mode, name='delete', value=download_id,
                           del_files=int(delete_files))
                return

    def on_shutdown(self):
        pass
