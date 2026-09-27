"""Read-only qBittorrent category discovery and explicit completed-file review."""

import re
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, Dict, List, Tuple

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import Constants, DownloadState as DS
from backend.base.logging import LOGGER
from backend.features import pack_inbox
from backend.implementations.download_clients.Torrent import TorrentDownload
from backend.implementations.external_client_manager import ExternalClients
from backend.implementations.remote_mapping import RemoteMappings
from backend.internals.db_models import PackDownloadsDB
from backend.internals.settings import Settings

_CACHE: Dict[int, Tuple[float, List[Dict[str, Any]], str]] = {}
_LOCK = RLock()
POLL_SECONDS = 15


def listing(handler) -> Dict[str, Any]:
    """List untracked category jobs; never submit, import or alter client data.

    Cache client reads briefly because Activity refreshes every five seconds.
    Failed clients retain their last snapshot with review disabled and an error.
    """
    result, errors = [], []
    tracked = {(job.external_client.id, handler.torrent_identity(job))
               for job in handler.queue if isinstance(job, TorrentDownload)}
    for data in ExternalClients.get_clients():
        if not data['enabled'] or data['client_type'] != 'qBittorrent':
            continue
        ident = data['id']
        with _LOCK:
            stamp, jobs, error = _CACHE.get(ident, (float('-inf'), [], ''))
            if monotonic() - stamp >= POLL_SECONDS:
                try:
                    client = ExternalClients.get_client(ident)
                    jobs = client.get_category_downloads()
                    error = ''
                except Exception:
                    LOGGER.exception('Could not discover category torrents for client %s', ident)
                    error = 'Could not refresh qBittorrent category downloads; check the client connection.'
                _CACHE[ident] = (monotonic(), jobs, error)
        if error:
            errors.append(f"{data['title']}: {error}")
        for job in jobs:
            if (ident, job['id']) in tracked:
                continue
            completed = job['state'] in (DS.SEEDING_STATE, DS.IMPORTING_STATE)
            result.append(dict(
                id=f"discovered-{ident}-{job['id']}", discovered=True,
                client_id=ident, torrent_hash=job['id'], volume_id=None,
                title=job['title'], source_name=data['title'], web_link='',
                web_title='', web_sub_title=None, status=job['state'].value,
                status_detail='Client unavailable; last known status' if error else (
                    'Ready for review; not automatically imported' if completed
                    else 'Discovered in kapowarr category'),
                size=job['size'], speed=job['speed'], progress=job['progress'],
                can_review=completed and not error, can_retry=False, can_forget=False
            ))
    return dict(downloads=result, errors=errors)


def review(client_id: object, info_hash: object):
    """Recheck a completed category torrent and scan only its payload for review.

    No ownership is adopted. Pack Inbox copies selected files and retains these
    external originals so seeding can continue.
    """
    if type(client_id) is not int or not isinstance(info_hash, str) or not re.fullmatch(r'[a-fA-F0-9]{40}', info_hash):
        raise InvalidKeyValue('torrent', 'Choose a discovered torrent')
    data = next((row for row in ExternalClients.get_clients()
                 if row['id'] == client_id and row['enabled']
                 and row['client_type'] == 'qBittorrent'), None)
    if data is None:
        raise InvalidKeyValue('torrent', 'The qBittorrent client is unavailable or disabled')
    client = ExternalClients.get_client(client_id)
    info = client.get_download(info_hash.lower())
    if (not info or info.get('category') != Constants.EXTERNAL_DOWNLOAD_TAG
            or info['state'] not in (DS.SEEDING_STATE, DS.IMPORTING_STATE)):
        raise InvalidKeyValue('torrent', 'Torrent must still be completed and in the kapowarr category')
    storage = info.get('storage')
    if not isinstance(storage, str) or not storage:
        raise InvalidKeyValue('torrent', 'Client did not report a completed content path')
    path = Path(RemoteMappings.remote_to_local(client_id, storage))
    root = Path(Settings().sv.download_folder).resolve()
    if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
            or not path.exists() or root not in path.resolve().parents):
        raise InvalidKeyValue('torrent', 'Completed content is unavailable or outside the download folder; check remote mappings')
    path = path.resolve()
    for folder in PackDownloadsDB.folders():
        managed = Path(folder).resolve()
        if path == managed or managed in path.parents or path in managed.parents:
            raise InvalidKeyValue('torrent', 'Torrent content overlaps a managed pack folder; use a separate external inbox')
    return pack_inbox.scan(str(path if path.is_dir() else path.parent),
                           filename=None if path.is_dir() else path.name)
