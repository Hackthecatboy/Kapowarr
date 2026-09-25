"""Review-first import of completed mixed-series folders, preserving sources."""

import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from time import time
from uuid import uuid4

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.file_extraction import extract_filename_data
from backend.implementations.matching import match_title
from backend.internals.db import get_db
from backend.internals.settings import Settings

_LOCK = Lock()
COMICS = {'.cbz', '.cbr', '.cb7', '.pdf'}
ARCHIVES = {'.zip', '.rar', '.7z'}
TERMINAL = {'imported', 'importing', 'held'}


def _within(path, root):
    return path == root or root in path.parents


def _digest(handle):
    result = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        result.update(chunk)
    return result.hexdigest()


def valid_root(value):
    if not isinstance(value, str) or not value.strip():
        raise InvalidKeyValue('folder', 'Choose a completed-pack folder visible inside Kapowarr')
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise InvalidKeyValue('folder', 'Use an existing absolute folder, not a symlink')
    root = path.resolve()
    libraries = get_db().execute('SELECT folder FROM root_folders UNION SELECT folder FROM volumes WHERE folder IS NOT NULL').fetchall()
    for row in libraries:
        library = Path(row[0]).resolve()
        if root == library or root in library.parents or library in root.parents:
            raise InvalidKeyValue('folder', 'Inbox and library folders must not overlap')
    return root


def safe_source(root, relative):
    path = root / relative
    if not _within(path, root) or '..' in Path(relative).parts:
        raise ValueError('Source is outside the inbox')
    if any(part.is_symlink() for part in [path, *path.parents] if part != root and _within(part, root)):
        raise ValueError('Symlinked content requires review')
    if not path.is_file() or not _within(path.resolve(), root):
        raise ValueError('Source is missing or outside the inbox')
    return path


def classify(name):
    data = extract_filename_data(name, assume_volume_number=False, fix_year=True)
    number = data['issue_number']
    if number is None or data['special_version']:
        return None, [], 'No explicit ordinary issue number; review required'
    bounds = number if isinstance(number, tuple) else (number, number)
    candidates = []
    volumes = get_db().execute('SELECT id,title,alt_title,year,volume_number,special_version,folder FROM volumes').fetchall()
    for volume in volumes:
        if not (match_title(data['series'], volume['title']) or match_title(data['series'], volume['alt_title'] or '')):
            continue
        if data['annual'] != ('annual' in volume['title'].lower()):
            continue
        if volume['special_version'] not in (None, 'normal'):
            continue
        if data['volume_number'] is not None and data['volume_number'] != volume['volume_number']:
            continue
        issues = get_db().execute('SELECT id,calculated_issue_number,date FROM issues WHERE volume_id=? AND calculated_issue_number BETWEEN ? AND ? ORDER BY calculated_issue_number', (volume['id'], *bounds)).fetchall()
        if not issues or issues[0]['calculated_issue_number'] != bounds[0] or issues[-1]['calculated_issue_number'] != bounds[1]:
            continue
        years = {volume['year']} | {int(i['date'][:4]) for i in issues if i['date'] and i['date'][:4].isdigit()}
        if data['year'] is not None and data['year'] not in years:
            continue
        candidates.append((volume, [i['id'] for i in issues]))
    if len(candidates) != 1:
        return None, [], 'No library match' if not candidates else 'Ambiguous: multiple library series match'
    volume, ids = candidates[0]
    owned = any(get_db().execute('SELECT 1 FROM issues_files WHERE issue_id=? LIMIT 1', (i,)).fetchone() for i in ids)
    return volume, ids, 'Already owned (all or part of this file)' if owned else ''


def listing():
    folder = Settings().sv.pack_inbox_folder
    rows = get_db().execute('SELECT token,relative_path,status,message,destination FROM pack_inbox WHERE root=? ORDER BY relative_path', (folder,)).fetchalldict()
    return dict(folder=folder, items=rows)


def scan(folder):
    root = valid_root(folder)
    with _LOCK:
        Settings().update({'pack_inbox_folder': str(root)})
        cursor = get_db()
        paths = []
        def failed(error):
            raise InvalidKeyValue('folder', 'Cannot read part of the inbox; check permissions')
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
            for name in list(dirs):
                if (Path(directory) / name).is_symlink():
                    paths.append(Path(directory) / name)
                    dirs.remove(name)
            paths.extend(Path(directory) / name for name in files if Path(name).suffix.lower() in COMICS | ARCHIVES)
            if len(paths) > 2000:
                raise InvalidKeyValue('folder', 'Choose a smaller completed folder (maximum 2,000 files per scan)')
        cursor.execute("UPDATE pack_inbox SET status='review', message='Source no longer present; scan again after restoring it' WHERE root=? AND status NOT IN ('imported','importing','held')", (str(root),))
        for path in sorted(paths):
            relative = str(path.relative_to(root))
            old = cursor.execute('SELECT id,status FROM pack_inbox WHERE root || ? || relative_path=?', (os.sep, str(path))).fetchone()
            if old:
                # Narrowing the inbox to a weekly subfolder must not bypass a hold.
                cursor.execute('UPDATE pack_inbox SET root=?,relative_path=? WHERE id=?', (str(root), relative, old['id']))
                if old['status'] in TERMINAL:
                    continue
            size, mtime, volume_id, ids = 0, '', None, []
            status, message = 'review', ''
            try:
                source = safe_source(root, relative)
                stat = source.stat()
                size, mtime = stat.st_size, str(stat.st_mtime_ns)
                if source.suffix.lower() not in COMICS:
                    message = 'Outer archive: extract to a completed folder before scanning'
                elif stat.st_size == 0 or time() - stat.st_mtime < 60:
                    message = 'Empty or recently modified file; wait until completed, then scan again'
                else:
                    volume, ids, message = classify(source.name)
                    if volume is not None:
                        volume_id = volume['id']
                        if not message:
                            status = 'matched'
                            message = f"{volume['title']} ({volume['year']}) — {len(ids)} issue(s)"
                        else:
                            status = 'owned'
            except (ValueError, OSError) as error:
                message = str(error)
            cursor.execute('''INSERT INTO pack_inbox(root,relative_path,token,status,message,size,mtime,volume_id,issue_ids)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(root,relative_path) DO UPDATE SET
                token=excluded.token,status=excluded.status,message=excluded.message,size=excluded.size,
                mtime=excluded.mtime,volume_id=excluded.volume_id,issue_ids=excluded.issue_ids''',
                (str(root), relative, uuid4().hex, status, message, size, mtime, volume_id, json.dumps(ids)))
        cursor.connection.commit()
        return listing()


def import_selected(tokens):
    if not isinstance(tokens, list) or not tokens or len(tokens) > 100 or any(not isinstance(t, str) for t in tokens):
        raise InvalidKeyValue('items', 'Select between 1 and 100 matched files')
    with _LOCK:
        root = valid_root(Settings().sv.pack_inbox_folder)
        cursor = get_db()
        rows = []
        for token in dict.fromkeys(tokens):
            row = cursor.execute("SELECT * FROM pack_inbox WHERE token=? AND root=? AND status='matched'", (token, str(root))).fetchone()
            if row is None:
                raise InvalidKeyValue('items', 'Preview changed or item cannot be imported; scan again')
            rows.append(dict(row))
        for row in rows:
            destination = None
            copying = False
            try:
                source = safe_source(root, row['relative_path'])
                before = source.stat()
                if before.st_size != row['size'] or str(before.st_mtime_ns) != row['mtime']:
                    raise ValueError('File changed since preview; scan again')
                volume, ids, reason = classify(source.name)
                if reason or volume is None or volume['id'] != row['volume_id'] or ids != json.loads(row['issue_ids']):
                    raise ValueError(reason or 'Library match changed; scan again')
                library = Path(volume['folder'])
                if not library.is_absolute() or library.is_symlink():
                    raise ValueError('Library destination requires review')
                destination = library.resolve() / ('Pack-Inbox-' + row['token']) / source.name
                cursor.execute("UPDATE pack_inbox SET status='importing',message='Copy started; interrupted imports require review',destination=? WHERE token=?", (str(destination), row['token']))
                cursor.connection.commit()
                copying = True
                destination.parent.mkdir(parents=True, exist_ok=False)
                digest = hashlib.sha256()
                descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(descriptor, 'rb') as incoming, destination.open('xb') as outgoing:
                    opened = os.fstat(incoming.fileno())
                    if (opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
                        raise ValueError('Source changed before copying; review required')
                    for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                        digest.update(chunk)
                        outgoing.write(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                after = source.stat()
                if (before.st_ino,before.st_size,before.st_mtime_ns) != (after.st_ino,after.st_size,after.st_mtime_ns):
                    raise ValueError('Source changed during copy; inspect the retained library copy')
                with destination.open('rb') as copied:
                    if _digest(copied) != digest.hexdigest():
                        raise ValueError('Copy checksum mismatch; review required')
                cursor.execute('BEGIN IMMEDIATE')
                if any(cursor.execute('SELECT 1 FROM issues_files WHERE issue_id=? LIMIT 1', (i,)).fetchone() for i in ids):
                    raise ValueError('An issue was imported elsewhere during copying; review the retained copy')
                # Explicit verified issue bindings preserve this match during rescans.
                file_id = cursor.execute('INSERT INTO files(filepath,size) VALUES(?,?)', (str(destination), before.st_size)).lastrowid
                cursor.executemany('INSERT INTO issues_files(file_id,issue_id,forced) VALUES(?,?,1)', [(file_id,i) for i in ids])
                cursor.execute("UPDATE pack_inbox SET status='imported',message='Copied and verified; original retained' WHERE token=?", (row['token'],))
                cursor.connection.commit()
            except Exception as error:
                cursor.connection.rollback()
                cursor.execute('UPDATE pack_inbox SET status=?,message=? WHERE token=?', ('held' if copying else 'review', str(error), row['token']))
                cursor.connection.commit()
        return listing()
