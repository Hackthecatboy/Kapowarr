"""Explicit GetComics pack downloads, isolated from single-volume imports."""
import stat
from asyncio import run
from hashlib import sha256
from pathlib import Path, PurePosixPath
from shutil import copyfileobj, disk_usage
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from zipfile import ZipFile, is_zipfile

from bs4 import BeautifulSoup

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import DownloadClientIdentifier as ID
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.features.pack_inbox import valid_root
from backend.implementations.download_client_manager import DownloadClients
from backend.implementations.download_clients.base import BaseDirectDownload
from backend.implementations.download_preppers.ddl.GetComics import (
    _check_download_link, _get_title, _purify_link, _extract_button_links, _extract_list_links)
from backend.internals.db import get_db
from backend.internals.server import Server

_LOCK = Lock()
_PREVIEWS = {}
_ACTIVE = set()
MAX_BYTES = 50 * 1024 ** 3
MAX_MEMBERS = 2000
HTTP_CLIENTS = {ID.DDL, ID.MEDIAFIRE, ID.WETRANSFER, ID.PIXELDRAIN,
                ID.MEDIAFIRE_FOLDER, ID.PIXELDRAIN_FOLDER}


def has_active_download() -> bool:
    """Return whether a pack download or extraction is active in this process.

    This is a scheduling hint; start() still enforces the single-download
    limit while holding the same lock.
    """
    with _LOCK:
        return bool(_ACTIVE)


def article_url(value):
    if not isinstance(value, str):
        raise InvalidKeyValue('url', 'Enter a GetComics article URL')
    try:
        url = urlsplit(value.strip())
        port = url.port
    except ValueError:
        raise InvalidKeyValue('url', 'Enter a valid GetComics article URL')
    if (url.scheme != 'https' or url.hostname not in ('getcomics.org', 'www.getcomics.org')
            or url.username or url.password or port not in (None, 443)
            or not url.path.strip('/')):
        raise InvalidKeyValue('url', 'Use an HTTPS getcomics.org article URL')
    return urlunsplit(('https', 'getcomics.org', url.path.rstrip('/') + '/', '', ''))


def preview(value):
    article = article_url(value)
    with Session() as session:
        response = session.get(article)
        response.raise_for_status()
        article_url(response.url)
        soup = BeautifulSoup(response.text, 'html.parser')
    body = soup.find('section', class_='post-contents')
    if body is None:
        raise InvalidKeyValue('url', 'No GetComics article download section found')
    title = _get_title(soup) or 'GetComics pack'
    choices, seen = [], set()
    groups = _extract_button_links(body, False) + _extract_list_links(body, False)
    labels = {link: group['web_sub_title'] for group in groups
              for links in group['links'].values() for link in links}
    # No series/issue filter: the user explicitly picks one pack link. Preserve
    # multipart labels and mirrors instead of assuming every article link is a pack.
    with _LOCK:
        for token, item in list(_PREVIEWS.items()):
            if monotonic() - item['created'] > 900:
                del _PREVIEWS[token]
        for anchor in body.find_all('a', href=True):
            link = anchor['href']
            label = anchor.get_text(' ', strip=True)
            service = _check_download_link(label.lower(), link, False)
            if service is None or link in seen:
                continue
            seen.add(link)
            token = uuid4().hex
            supported = service.value not in ('Mega',)
            _PREVIEWS[token] = dict(created=monotonic(), article=article,
                                    title=title, link=link, service=service)
            choices.append(dict(token=token, label=(labels.get(link) or label) + " — " + label, service=service.value,
                                supported=supported))
            if len(choices) >= 100:
                break
    return dict(title=title, choices=choices)


def listing():
    with _LOCK:
        cursor = get_db()
        for row in cursor.execute("SELECT id FROM pack_downloads WHERE status IN ('downloading','extracting')").fetchall():
            if row['id'] not in _ACTIVE:
                cursor.execute("UPDATE pack_downloads SET status='held', message='Interrupted download or extraction; retained files require review. No automatic retry.' WHERE id=?", (row['id'],))
        cursor.connection.commit()
        return cursor.execute('SELECT * FROM pack_downloads ORDER BY rowid DESC LIMIT 100').fetchalldict()


def download_root(folder):
    root = valid_root(folder)
    for row in get_db().execute('SELECT folder FROM pack_downloads').fetchall():
        managed = Path(row[0])
        if root == managed or managed in root.parents:
            raise InvalidKeyValue('folder', 'Choose the inbox root, not a previous pack job or its ready folder')
    return root


def start(token, folder):
    root = download_root(folder)
    with _LOCK:
        item = _PREVIEWS.get(token) if isinstance(token, str) else None
        if item is None or monotonic() - item['created'] > 900:
            raise InvalidKeyValue('token', 'Preview expired; preview the article again')
        if item['service'].value == 'Mega':
            raise InvalidKeyValue('download', 'Choose an HTTP mirror; Mega packs are not supported yet')
        if _ACTIVE:
            raise InvalidKeyValue('download', 'Wait for the current pack download to finish')
        identity = sha256((item['article'] + '\n' + item['link']).encode()).hexdigest()
        cursor = get_db()
        if cursor.execute('SELECT 1 FROM pack_downloads WHERE identity=?', (identity,)).fetchone():
            raise InvalidKeyValue('download', 'This link was already submitted; inspect its saved job')
        ident = uuid4().hex
        destination = root / ('Pack-' + ident)
        try:
            destination.mkdir()  # Requires a writable inbox; never reuse a folder.
        except OSError:
            raise InvalidKeyValue('folder', 'Inbox must be writable to download packs')
        cursor.execute("INSERT INTO pack_downloads(id,article,title,root,folder,identity,status) VALUES(?,?,?,?,?,?,'downloading')",
                       (ident, item['article'], item['title'], str(root), str(destination), identity))
        cursor.connection.commit()
        _ACTIVE.add(ident)
        try:
            Server().get_db_thread(target=_worker, args=(ident, dict(item), destination),
                                   name='PackDownload-' + ident).start()
        except Exception:
            _ACTIVE.discard(ident)
            cursor.execute("UPDATE pack_downloads SET status='held',message='Could not start worker; no automatic retry' WHERE id=?", (ident,))
            cursor.connection.commit()
            raise
    return dict(id=ident)


def _update(ident, **values):
    cursor = get_db()
    cursor.execute('UPDATE pack_downloads SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                   (*values.values(), ident))
    cursor.connection.commit()


def extract_zip(archive, destination):
    """Extract one outer ZIP, keeping comic archives intact and rejecting unsafe paths."""
    with ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_BYTES:
            raise ValueError('ZIP exceeds the 2,000-entry or 50 GiB extraction limit')
        if sum(m.file_size for m in members) > disk_usage(destination.parent).free:
            raise ValueError('Insufficient free space for extracted pack')
        names = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                    or ':' in member.filename or stat.S_ISLNK(member.external_attr >> 16)
                    or member.flag_bits & 1 or member.filename in names):
                raise ValueError('ZIP has unsafe, duplicate or encrypted members; retained for review')
            names.add(member.filename)
        destination.mkdir()
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.filename).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as incoming, target.open('xb') as outgoing:
                    copyfileobj(incoming, outgoing)


def _worker(ident, item, destination):
    try:
        link, identifier = run(_purify_link(item['service'], item['link']))
        if identifier not in HTTP_CLIENTS:
            raise ValueError('This pack link needs an unsupported client; use an HTTP mirror or download externally')
        cls = DownloadClients.get_client(identifier)
        if not issubclass(cls, BaseDirectDownload):
            raise ValueError('Unsupported pack download provider')
        client = cls.pack_client(link)
        received = 0
        last_update = monotonic()
        partial = destination / 'payload.partial'
        try:
            with client._fetch_pure_link() as response, partial.open('xb') as output:
                response.raise_for_status()
                if 'text/html' in response.headers.get('Content-Type', '').lower():
                    raise ValueError('Provider returned a web page instead of a download')
                total = int(response.headers.get('Content-Length', '0'))
                if total > MAX_BYTES or total > disk_usage(destination).free:
                    raise ValueError('Pack exceeds available space or the 50 GiB download limit')
                _update(ident, total=total)
                for chunk in response.iter_content(1024 * 1024):
                    received += len(chunk)
                    if received > MAX_BYTES:
                        raise ValueError('Pack exceeds the 50 GiB download limit')
                    output.write(chunk)
                    if monotonic() - last_update > 2:
                        _update(ident, received=received)
                        last_update = monotonic()
                if not received or (total and received != total):
                    raise ValueError('Incomplete download; partial file retained')
        finally:
            client._ssn.close()
        archive = destination / 'payload.archive'
        partial.rename(archive)
        _update(ident, received=received, status='extracting')
        if not is_zipfile(archive):
            _update(ident, status='held', message='Downloaded archive retained as payload.archive. Extract RAR/7z or other formats manually into a completed folder, then scan it.')
            return
        ready = destination / 'ready'
        extract_zip(archive, ready)
        _update(ident, status='ready', message='ZIP extracted. Review in Pack Inbox; recently written files need 30 seconds before scanning. Original archive retained.')
    except Exception:
        LOGGER.exception('Pack download %s needs review', ident)
        _update(ident, status='held', message='Download or extraction failed. Inspect System Logs and the retained folder; no automatic retry.')
    finally:
        with _LOCK:
            _ACTIVE.discard(ident)


_CLEANUPS = {}


def _pack_for_cleanup(ident):
    if not isinstance(ident, str):
        raise InvalidKeyValue('pack', 'Choose a pack job')
    row = get_db().execute('SELECT * FROM pack_downloads WHERE id=?', (ident,)).fetchone()
    if row is None or ident in _ACTIVE or row['status'] not in ('ready', 'held'):
        raise InvalidKeyValue('pack', 'Only completed or held packs can be finished')
    root = valid_root(row['root'])
    folder = Path(row['folder'])
    if folder != root / ('Pack-' + ident) or any(p.is_symlink() for p in (folder, *folder.parents)):
        raise InvalidKeyValue('pack', 'Pack folder changed; review required')
    for other in get_db().execute('SELECT folder FROM pack_downloads WHERE id != ?', (ident,)).fetchall():
        if folder in Path(other[0]).parents:
            raise InvalidKeyValue('pack', 'Another pack job is nested inside this folder; review manually')
    for item in get_db().execute("SELECT root,relative_path FROM pack_inbox WHERE status='importing'").fetchall():
        source = Path(item['root']) / item['relative_path']
        if folder in source.parents:
            raise InvalidKeyValue('pack', 'An interrupted import needs review before finishing this pack')
    return folder


def _inventory(folder):
    import os
    result = []
    def unreadable(error):
        raise InvalidKeyValue('pack', 'Cannot read the complete pack folder; review permissions')
    for directory, dirs, files in os.walk(folder, followlinks=False, onerror=unreadable):
        for path in [Path(directory)] + [Path(directory) / name for name in files]:
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise InvalidKeyValue('pack', 'Special files or symlinks require manual review')
            result.append((str(path), (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns), path.is_dir()))
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise InvalidKeyValue('pack', 'Symlinked folders require manual review')
        if len(result) > 5000:
            raise InvalidKeyValue('pack', 'Too many files for one cleanup; review manually')
    if not result:
        raise InvalidKeyValue('pack', 'Pack folder is unavailable')
    return sorted(result)


def cleanup_preview(ident):
    from backend.features.pack_inbox import _LOCK as inbox_lock
    with inbox_lock, _LOCK:
        folder = _pack_for_cleanup(ident)
        inventory = _inventory(folder)
        token = uuid4().hex
        for old, entry in list(_CLEANUPS.items()):
            if monotonic() - entry[0] > 900:
                del _CLEANUPS[old]
        _CLEANUPS[token] = (monotonic(), ident, inventory)
        files = [dict(path=str(Path(path).relative_to(folder)), size=info[2])
                 for path, info, directory in inventory if not directory]
        return dict(token=token, files=files, bytes=sum(f['size'] for f in files))


def cleanup_confirm(token, confirmed):
    from backend.features.pack_inbox import _LOCK as inbox_lock
    with inbox_lock, _LOCK:
        preview = _CLEANUPS.pop(token, None) if isinstance(token, str) else None
        if confirmed is not True or preview is None or monotonic() - preview[0] > 900:
            raise InvalidKeyValue('cleanup', 'Confirm a current cleanup preview')
        _, ident, inventory = preview
        folder = _pack_for_cleanup(ident)
        if _inventory(folder) != inventory:
            raise InvalidKeyValue('cleanup', 'Pack files changed; preview cleanup again')
        try:
            for path, before, directory in inventory:
                if directory:
                    continue
                item = Path(path)
                info = item.lstat()
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != before or item.is_symlink():
                    raise ValueError('Pack changed during cleanup; remaining files retained')
                item.unlink()
            for path, _, directory in sorted(inventory, key=lambda item: len(Path(item[0]).parts), reverse=True):
                if directory:
                    Path(path).rmdir()
        except (OSError, ValueError):
            LOGGER.exception('Pack %s cleanup incomplete', ident)
            _update(ident, status='held', message='Cleanup incomplete; preview remaining files again. Library imports are untouched.')
            raise InvalidKeyValue('cleanup', 'Cleanup incomplete; inspect remaining files and preview again')
        cursor = get_db()
        for row in cursor.execute('SELECT token,root,relative_path,status FROM pack_inbox').fetchall():
            if folder in (Path(row['root']) / row['relative_path']).parents and row['status'] != 'imported':
                cursor.execute("UPDATE pack_inbox SET status='discarded',message='Discarded when finishing pack' WHERE token=?", (row['token'],))
        cursor.connection.commit()
        _update(ident, status='finished', message='Archive and remaining extracted files deleted by request. Library imports preserved.')
        return dict(id=ident)
