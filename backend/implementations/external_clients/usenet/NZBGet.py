# SPDX-License-Identifier: GPL-3.0
# Queue/history interpretation and split sizes adapted from silasfelinus/Kapowarr
# at 2a283b1d; reworked for v1.3.2 and conservative completion on 2026-09-20.

from backend.base.custom_exceptions import ClientNotWorking
from backend.base.definitions import (BrokenClientReason, DownloadState as DS,
                                      DownloadType, ExternalClientField as ECF)
from backend.implementations.external_client_manager import (
    BaseExternalClient, ExternalClients)
from backend.implementations.usenet_support import (CATEGORY, entries,
                                                    invalid_response, number,
                                                    request_json, status)


@ExternalClients.register_client(
    DownloadType.USENET, 'NZBGet',
    (ECF.TITLE, ECF.ENABLED, ECF.BASE_URL, ECF.USERNAME, ECF.PASSWORD)
)
class NZBGet(BaseExternalClient):
    @staticmethod
    def _request(base_url, username, password, method, params=None):
        result = request_json(base_url, '/jsonrpc', auth=(username or '', password or ''),
                              payload=dict(method=method, params=params or [], id=1))
        if not isinstance(result, dict) or result.get('error') or 'result' not in result or result.get('id') != 1:
            raise invalid_response()
        return result['result']

    def _call(self, method, params=None):
        return self._request(self.base_url, self.username, self.password, method, params)

    @classmethod
    def test(cls, base_url, username=None, password=None, api_token=None):
        version = cls._request(base_url, username, password, 'version')
        if not isinstance(version, str) or not version.strip():
            raise invalid_response()
        entries(cls._request(base_url, username, password, 'listgroups', [0]))
        config = entries(cls._request(base_url, username, password, 'config'))
        if not any(str(p.get('Name', '')).startswith('Category') and str(p.get('Name', '')).endswith('.Name') and p.get('Value') == CATEGORY for p in config):
            raise ClientNotWorking(BrokenClientReason.CATEGORY_NOT_FOUND)

    def add_download(self, download_link, target_folder, download_name):
        result = self._call('append', [(download_name or 'Kapowarr') + '.nzb', download_link, CATEGORY,
                                       0, False, False, '', 0, 'score'])
        if type(result) is not int or result <= 0:
            raise invalid_response()
        return str(result)

    @staticmethod
    def _size(job, prefix):
        if prefix + 'Lo' in job:
            return int(number(job.get(prefix + 'Hi'))) * 2**32 + int(number(job[prefix + 'Lo']))
        return int(number(job.get(prefix + 'MB')) * 1024**2)

    def get_download(self, download_id):
        groups = entries(self._call('listgroups', [0]))
        job = next((j for j in groups if str(j.get('NZBID')) == download_id), None)
        if job is not None:
            size = self._size(job, 'FileSize')
            left = self._size(job, 'RemainingSize')
            phase = job.get('Status')
            state = {'PAUSED': DS.PAUSED_STATE, 'QUEUED': DS.QUEUED_STATE}.get(
                phase, DS.DOWNLOADING_STATE)
            return status(state, size, 100 * max(0, size - left) / size if size else 0)
        history = entries(self._call('history', [False]))
        job = next((j for j in history if str(j.get('NZBID')) == download_id), None)
        if job is None:
            return None
        phase = str(job.get('Status', ''))
        if phase.startswith('SUCCESS/'):
            return status(DS.IMPORTING_STATE, self._size(job, 'FileSize'), 100,
                          job.get('FinalDir') or job.get('DestDir'))
        # Warnings, deleted and unknown terminal states require review.
        return status(DS.FAILED_STATE, self._size(job, 'FileSize'))

    def delete_download(self, download_id, delete_files):
        if not str(download_id).isdigit() or int(download_id) <= 0:
            raise invalid_response()
        groups = entries(self._call('listgroups', [0]))
        if any(str(j.get('NZBID')) == download_id for j in groups):
            if not delete_files:
                raise invalid_response()
            result = self._call(
                'editqueue', ['GroupFinalDelete', 0, '', [int(download_id)]])
        else:
            jobs = entries(self._call('history', [False]))
            job = next((j for j in jobs if str(j.get('NZBID')) == download_id), None)
            if job is None:
                return
            # NZBGet's history delete can remove failed data: never use it for
            # preserve-files cleanup unless completion was explicitly successful.
            if not delete_files and not str(job.get('Status', '')).startswith('SUCCESS/'):
                raise invalid_response()
            result = self._call(
                'editqueue', ['HistoryFinalDelete', 0, '', [int(download_id)]])
        if result is not True:
            raise invalid_response()

    def on_shutdown(self):
        pass
