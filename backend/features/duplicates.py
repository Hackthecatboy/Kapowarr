"""Read-only review of indexed library duplicates; never delete or rename."""

import hashlib
import os
from collections import defaultdict
from pathlib import Path
from threading import Lock
from time import monotonic

from backend.base.custom_exceptions import InvalidKeyValue
from backend.internals.db import get_db

_LOCK = Lock()
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
            exact_groups.append(dict(files=group, same_physical_file=
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
        issue_groups.append(dict(volume_id=row['volume_id'], title=row['title'], issue_number=row['issue_number'],
                                 files=[item for _, item in entries]))
    for item in files.values():
        del item['signature']
    return dict(exact=exact_groups, same_issue=issue_groups, errors=errors,
                scanned_files=len(files), hashed_bytes=hashed_bytes, limited=limited)
