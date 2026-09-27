"""Duplicate review with explicit, revalidated deletion of redundant library copies."""

import hashlib
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from backend.base.custom_exceptions import InvalidKeyValue
from backend.internals.db import get_db

_LOCK = Lock()
_PREVIEWS = {}
PREVIEW_SECONDS = 900
MAX_FILES = 20000
MAX_BYTES = 2 * 1024 ** 3
MAX_SECONDS = 20
COMICS = {'.cbz', '.cbr', '.cb7', '.pdf', '.zip', '.rar', '.7z'}


def signature(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def scan(volume_id=None):
    if volume_id is not None and (type(volume_id) is not int or volume_id < 1):
        raise InvalidKeyValue('volume_id', 'Use a positive volume ID or leave empty')
    if not _LOCK.acquire(blocking=False):
        raise InvalidKeyValue('scan', 'A duplicate scan is already running')
    try:
        return _scan(volume_id)
    finally:
        _LOCK.release()


def _scan(volume_id):
    started = monotonic()
    for token, (created, _) in list(_PREVIEWS.items()):
        if started - created > PREVIEW_SECONDS:
            del _PREVIEWS[token]
    if len(_PREVIEWS) > 10000:
        _PREVIEWS.clear()
    cursor = get_db()
    # Include registered issue files and volume-level archives, but no client
    # payloads or unrelated filesystem trees. Hash only candidates with equal sizes.
    rows = cursor.execute('''SELECT DISTINCT f.id, f.filepath, v.id AS volume_id,
        v.title, v.folder, r.folder AS root
        FROM files f JOIN (
          SELECT b.file_id, i.volume_id FROM issues_files b JOIN issues i ON i.id=b.issue_id
          UNION SELECT file_id, volume_id FROM volume_files
        ) linked ON linked.file_id=f.id JOIN volumes v ON v.id=linked.volume_id
        JOIN root_folders r ON r.id=v.root_folder
        WHERE (? IS NULL OR v.id=?) LIMIT ?''', (volume_id, volume_id, MAX_FILES + 1)).fetchall()
    limited = len(rows) > MAX_FILES
    files = {}
    errors = []
    sizes = defaultdict(list)
    for row in rows[:MAX_FILES]:
        row = dict(row)
        if row['id'] in files:
            continue
        path = Path(row['filepath'])
        if path.suffix.lower() not in COMICS:
            continue
        try:
            folder, root = Path(row['folder']), Path(row['root'])
            if not all(p.is_absolute() for p in (path, folder, root)):
                raise ValueError('Non-absolute library path')
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('Symlink path skipped')
            resolved = path.resolve()
            if folder.resolve() not in resolved.parents or root.resolve() not in resolved.parents:
                raise ValueError('File is outside its library folder')
            if not path.is_file():
                raise ValueError('File unavailable; rescan the volume')
            stat = path.stat()
            item = dict(id=row['id'], filepath=str(path), size=stat.st_size,
                        volume_id=row['volume_id'], title=row['title'], sha256=None,
                        signature=signature(stat))
            files[row['id']] = item
            if stat.st_size:
                sizes[stat.st_size].append(item)
        except (OSError, ValueError) as error:
            errors.append(dict(filepath=str(path), reason=str(error)))
    hashed_bytes = 0
    for size, candidates in sizes.items():
        if len(candidates) < 2:
            continue
        for item in candidates:
            if hashed_bytes + size > MAX_BYTES or monotonic() - started > MAX_SECONDS:
                limited = True
                continue
            try:
                descriptor = os.open(item['filepath'], os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(descriptor, 'rb') as handle:
                    if signature(os.fstat(handle.fileno())) != item['signature']:
                        raise ValueError('File changed before hashing; scan again')
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        hashed_bytes += len(chunk)
                        if hashed_bytes > MAX_BYTES or monotonic() - started > MAX_SECONDS:
                            limited = True
                            raise ValueError('Scan limit reached; narrow the scan to one volume')
                        digest.update(chunk)
                    if signature(os.fstat(handle.fileno())) != item['signature']:
                        raise ValueError('File changed during hashing; scan again')
                item['sha256'] = digest.hexdigest()
            except (OSError, ValueError) as error:
                errors.append(dict(filepath=item['filepath'], reason=str(error)))
    exact = defaultdict(list)
    for item in files.values():
        try:
            if signature(Path(item['filepath']).stat()) != item['signature']:
                raise ValueError('File changed during scan; scan again')
        except (OSError, ValueError) as error:
            item['sha256'] = None
            errors.append(dict(filepath=item['filepath'], reason=str(error)))
        if item['sha256']:
            exact[(item['size'], item['sha256'])].append(item)
    exact_groups = []
    for group in exact.values():
        if len(group) > 1:
            token = uuid4().hex
            _PREVIEWS[token] = (monotonic(), {i['id']: dict(i) for i in group})
            exact_groups.append(dict(token=token, files=group, same_physical_file=
                                     len({tuple(i['signature'][:2]) for i in group}) == 1))
    issue_groups = []
    bindings = cursor.execute('''SELECT i.id,i.volume_id,i.issue_number,v.title,b.file_id
        FROM issues i JOIN issues_files b ON b.issue_id=i.id JOIN volumes v ON v.id=i.volume_id
        WHERE (? IS NULL OR v.id=?) ORDER BY i.id''', (volume_id, volume_id)).fetchall()
    by_issue = defaultdict(list)
    for row in bindings:
        if row['file_id'] in files:
            by_issue[row['id']].append((row, files[row['file_id']]))
    for entries in by_issue.values():
        if len(entries) < 2:
            continue
        hashes = {item['sha256'] for _, item in entries}
        if len(hashes) == 1 and None not in hashes:
            continue  # Already covered by exact duplicates.
        row = entries[0][0]
        token = uuid4().hex
        _PREVIEWS[token] = (monotonic(), {item['id']: dict(item, review_issue_id=row['id'])
                                        for _, item in entries})
        issue_groups.append(dict(token=token, volume_id=row['volume_id'], title=row['title'], issue_number=row['issue_number'],
                                 files=[item for _, item in entries]))
    for item in files.values():
        del item['signature']
    return dict(exact=exact_groups, same_issue=issue_groups, errors=errors,
                scanned_files=len(files), hashed_bytes=hashed_bytes, limited=limited)


def delete_selected(token, keep_id, delete_ids, confirmed=False, reviewed_different=False):
    """Keep a verified copy or manually chosen edition; protect current issue coverage."""
    if (confirmed is not True or type(keep_id) is not int or not isinstance(token, str)
            or not isinstance(delete_ids, list) or not 1 <= len(delete_ids) <= 100
            or any(type(i) is not int for i in delete_ids) or keep_id in delete_ids):
        raise InvalidKeyValue('selection', 'Confirm selected duplicates and choose a separate file to keep')
    with _LOCK:
        preview = _PREVIEWS.get(token)
        if preview is None or monotonic() - preview[0] > PREVIEW_SECONDS:
            raise InvalidKeyValue('selection', 'Preview expired; scan again')
        files = preview[1]
        ids = list(dict.fromkeys(delete_ids))
        if keep_id not in files or any(i not in files for i in ids):
            raise InvalidKeyValue('selection', 'Files are not part of this review group; scan again')
        selected = [files[keep_id]] + [files[i] for i in ids]
        manual_review = 'review_issue_id' in files[keep_id]
        if manual_review and reviewed_different is not True:
            raise InvalidKeyValue('selection', 'Confirm review of different or unverified editions')
        if len({i['volume_id'] for i in selected}) != 1:
            raise InvalidKeyValue('selection', 'Keep a copy in each volume; cross-volume deletion is not supported')
        if sum(i['size'] for i in selected) > MAX_BYTES:
            raise InvalidKeyValue('selection', 'Select fewer files; verification exceeds the scan limit')
        cursor = get_db()
        started = monotonic()
        try:
            for item in selected:
                _validate_delete_file(cursor, item)
                if manual_review and not item['sha256']:
                    # Manual edition choice: unchanged filesystem identity is checked,
                    # but no claim is made that archive contents are identical.
                    continue
                descriptor = os.open(item['filepath'], os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(descriptor, 'rb') as handle:
                    if signature(os.fstat(handle.fileno())) != item['signature']:
                        raise ValueError('File changed; scan again')
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        if monotonic() - started > MAX_SECONDS:
                            raise ValueError('Verification timed out; select fewer files')
                        digest.update(chunk)
                    if (digest.hexdigest() != item['sha256']
                            or signature(os.fstat(handle.fileno())) != item['signature']):
                        raise ValueError('File contents changed; scan again')
            for item in selected[1:]:
                _validate_coverage(cursor, keep_id, item['id'])
        except (ValueError, OSError) as error:
            raise InvalidKeyValue('selection', str(error))
        removed = []
        errors = []
        for item in selected[1:]:
            try:
                cursor.execute('BEGIN IMMEDIATE')
                _validate_delete_file(cursor, files[keep_id])
                _validate_delete_file(cursor, item)
                _validate_coverage(cursor, keep_id, item['id'])
                Path(item['filepath']).unlink()
                removed.append(item['filepath'])
                cursor.execute('DELETE FROM issues_files WHERE file_id=?', (item['id'],))
                cursor.execute('DELETE FROM volume_files WHERE file_id=?', (item['id'],))
                cursor.execute('DELETE FROM files WHERE id=?', (item['id'],))
                cursor.connection.commit()
                # Unlinking a hard link changes the keeper's ctime/link count.
                current = signature(Path(files[keep_id]['filepath']).stat())
                if current[:4] != files[keep_id]['signature'][:4]:
                    raise ValueError('Retained copy changed; stop and rescan')
                files[keep_id]['signature'] = current
            except (ValueError, OSError, sqlite3.Error) as error:
                cursor.connection.rollback()
                errors.append(dict(filepath=item['filepath'], reason=str(error)))
                break
        del _PREVIEWS[token]
        return dict(deleted=removed, errors=errors, kept=files[keep_id]['filepath'])


def _validate_delete_file(cursor, item):
    row = cursor.execute('''SELECT f.filepath,v.folder,r.folder AS root FROM files f
        JOIN volumes v ON v.id=? JOIN root_folders r ON r.id=v.root_folder WHERE f.id=?''',
        (item['volume_id'], item['id'])).fetchone()
    if row is None or row['filepath'] != item['filepath']:
        raise ValueError('Library record changed; scan again')
    if 'review_issue_id' in item and not cursor.execute(
            'SELECT 1 FROM issues_files WHERE file_id=? AND issue_id=?',
            (item['id'], item['review_issue_id'])).fetchone():
        raise ValueError('Issue binding changed; scan again')
    path = Path(row['filepath'])
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink paths cannot be deleted')
    if (not path.is_absolute() or Path(row['folder']).resolve() not in path.resolve().parents
            or Path(row['root']).resolve() not in path.resolve().parents):
        raise ValueError('File is outside its library folder')
    if signature(path.stat()) != item['signature'] or not path.is_file():
        raise ValueError('File changed or disappeared; scan again')
    if cursor.execute('SELECT 1 FROM download_queue WHERE volume_id=? LIMIT 1', (item['volume_id'],)).fetchone():
        raise ValueError('Volume has queued downloads; finish or resolve them before deleting duplicates')
    if cursor.execute("SELECT 1 FROM pack_inbox WHERE volume_id=? AND status='importing' LIMIT 1", (item['volume_id'],)).fetchone():
        raise ValueError('Pack import is in progress; finish it before deleting duplicates')


def _validate_coverage(cursor, keep_id, delete_id):
    for query in ('SELECT issue_id FROM issues_files WHERE file_id=?',
                  'SELECT volume_id,file_type FROM volume_files WHERE file_id=?'):
        kept = {tuple(row) for row in cursor.execute(query, (keep_id,)).fetchall()}
        removed = {tuple(row) for row in cursor.execute(query, (delete_id,)).fetchall()}
        if not removed.issubset(kept):
            raise ValueError('The selected copy covers additional issues or metadata; keep it for review')
