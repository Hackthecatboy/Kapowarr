"""Review-first import of completed mixed-series folders, preserving sources."""

import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Row
from threading import Lock
from time import time
from typing import (Any, BinaryIO, Dict, Iterator, List,
                    Optional, Tuple, TypedDict, cast)
from uuid import uuid4

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import FilenameData, InboxJournalEntry
from backend.base.file_extraction import extract_filename_data
from backend.implementations.matching import match_title
from backend.implementations.naming import mass_rename
from backend.internals.db import KapowarrCursor, get_db
from backend.internals.db_models import PackInboxDB
from backend.internals.settings import Settings

_LOCK = Lock()
COMICS = {'.cbz', '.cbr', '.cb7', '.pdf'}
ARCHIVES = {'.zip', '.rar', '.7z'}
TERMINAL = {'imported', 'importing', 'held', 'discarded'}


class InboxItem(TypedDict, total=False):
    """A reviewed file returned to the Pack Inbox page.

    series_query and destination are present only when applicable.
    """

    token: str
    relative_path: str
    status: str
    message: str
    destination: Optional[str]
    can_cleanup: bool
    series_query: str


class InboxListing(TypedDict):
    """The configured inbox folder and its saved review results."""

    folder: str
    items: List[InboxItem]


@contextmanager
def inbox_operation() -> Iterator[None]:
    """Serialize imports, scans and managed-pack cleanup in this process.

    Acquire this context before a download-job lock when both are needed.
    It is not reentrant: callers must not nest inbox operations.
    """
    with _LOCK:
        yield


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _digest(handle: BinaryIO) -> str:
    """Hash the remaining bytes of an already-open binary stream."""
    result = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
        result.update(chunk)
    return result.hexdigest()


def valid_root(value: object) -> Path:
    """Validate an inbox folder and return its resolved absolute path.

    Raises:
        InvalidKeyValue: The folder is missing, symlinked or overlaps a library.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidKeyValue(
            'folder', 'Choose a completed-pack folder visible inside Kapowarr')
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise InvalidKeyValue(
            'folder', 'Use an existing absolute folder, not a symlink')
    root = path.resolve()
    libraries = PackInboxDB.library_folders()
    for row in libraries:
        library = Path(row[0]).resolve()
        if root == library or root in library.parents or library in root.parents:
            raise InvalidKeyValue(
                'folder', 'Inbox and library folders must not overlap')
    return root


def safe_source(root: Path, relative: str) -> Path:
    """Resolve an existing file beneath a previously validated inbox root.

    Raises:
        ValueError: The file is missing, escapes the root or uses symlinks.
    """
    path = root / relative
    if not _within(path, root) or '..' in Path(relative).parts:
        raise ValueError('Source is outside the inbox')
    if any(part.is_symlink() for part in [path, *path.parents] if part != root and _within(part, root)):
        raise ValueError('Symlinked content requires review')
    if not path.is_file() or not _within(path.resolve(), root):
        raise ValueError('Source is missing or outside the inbox')
    return path


def filename_data(name: str) -> FilenameData:
    """Ignore collection prefixes before explicit issues or empty parsed titles."""
    if re.search(r'#\d', name):
        name = re.sub(r'^\d+\s*-\s*', '', name)
    data = extract_filename_data(name, assume_volume_number=False, fix_year=True)
    if not data['series']:
        # A leading collection position can be mistaken for the issue number.
        title = re.sub(r'^\d+\s*-\s*', '', name)
        if title != name:
            data = extract_filename_data(
                title, assume_volume_number=False, fix_year=True)
    return data


def manual_identity(volume_id: int, ids: List[int]) -> Tuple[Optional[Row], List[int], str]:
    """Validate explicit issue membership and current ownership before copying."""
    volume = next((v for v in PackInboxDB.matching_volumes() if v['id'] == volume_id), None)
    available = {i['id'] for i in PackInboxDB.volume_issues(volume_id)} if volume else set()
    if not volume or not ids or not set(ids) <= available:
        return None, [], 'Selected series or issues no longer exist; choose again'
    reason = 'Already owned (all or part of this file)' if any(PackInboxDB.existing_issue_file(i) for i in ids) else ''
    return volume, ids, reason


def match_options(token: object, volume_id: object) -> Dict[str, Any]:
    """Return issue choices for the library series explicitly picked by a user."""
    root = valid_root(Settings().sv.pack_inbox_folder)
    row = PackInboxDB.review_selection(token, str(root)) if isinstance(token, str) else None
    if row is None or type(volume_id) is not int:
        raise InvalidKeyValue('match', 'Preview expired; refresh the inbox and choose again')
    try:
        source = safe_source(root, row['relative_path'])
        stat = source.stat()
    except (OSError, ValueError) as error:
        raise InvalidKeyValue('match', 'Source unavailable; scan again before choosing a series') from error
    if (stat.st_size != row['size'] or str(stat.st_mtime_ns) != row['mtime']
            or source.suffix.lower() not in COMICS or stat.st_size == 0 or time() - stat.st_mtime < 30):
        raise InvalidKeyValue('match', 'File changed or is still settling; scan again before selecting a series')
    volume = next((v for v in PackInboxDB.matching_volumes() if v['id'] == volume_id), None)
    if volume is None:
        raise InvalidKeyValue('match', 'Add the selected series to the library first')
    issues = PackInboxDB.volume_issues(volume_id)
    number = filename_data(source.name)['issue_number']
    bounds = number if isinstance(number, tuple) else (number, number)
    selected = [i['id'] for i in issues if number is not None and bounds[0] <= i['calculated_issue_number'] <= bounds[1]]
    if number is None and len(issues) == 1:
        selected = [issues[0]['id']]
    return dict(title=volume['title'], volume_id=volume_id, selected=selected,
                issues=[dict(id=i['id'], number=i['calculated_issue_number'], owned=bool(PackInboxDB.existing_issue_file(i['id']))) for i in issues])


def set_match(token: object, volume_id: object, issue_ids: object) -> InboxListing:
    """Save a choice and refresh related files without importing or adding aliases."""
    with inbox_operation():
        match_options(token, volume_id)
        token, volume_id = cast(str, token), cast(int, volume_id)
        if (not isinstance(issue_ids, list) or not 1 <= len(issue_ids) <= 100
                or any(type(i) is not int for i in issue_ids)):
            raise InvalidKeyValue('match', 'Choose the issues contained in this file')
        ids = sorted(set(issue_ids))
        volume, ids, reason = manual_identity(volume_id, ids)
        if reason or volume is None:
            raise InvalidKeyValue('match', reason)
        PackInboxDB.set_manual_match(token, volume_id, json.dumps(ids), f"Selected: {volume['title']} — {len(ids)} issue(s)")
        get_db().connection.commit()
        _refresh_review_matches(token, volume_id, ids)
        return listing()


def classify(name: str) -> Tuple[Optional[Row], List[int], str]:
    """Match a filename to one library edition and its issue IDs.

    Returns:
        The volume row, issue IDs and a review reason. An empty reason denotes
        a unique missing-issue match. A matched but owned edition retains its
        volume and issue IDs alongside an ownership reason.
    """
    data = filename_data(name)
    number = data['issue_number']
    standalone = number is None and data['special_version'] in (
        None, 'tpb', 'one-shot', 'hard-cover', 'omnibus')
    if (number is None and not standalone) or (number is not None and data['special_version']):
        return None, [], 'No explicit ordinary issue number; review required'
    if standalone and data['year'] is None:
        return None, [], 'Unnumbered book needs a year and a unique single-issue library edition'
    bounds = number if isinstance(number, tuple) else (number, number)
    candidates = []
    volumes = PackInboxDB.matching_volumes()
    for volume in volumes:
        if not (match_title(data['series'], volume['title']) or match_title(data['series'], volume['alt_title'] or '')):
            continue
        if data['annual'] != ('annual' in volume['title'].lower()):
            continue
        if standalone:
            if volume['special_version'] not in ('tpb', 'one-shot', 'hard-cover', 'omnibus'):
                continue
        elif volume['special_version'] not in (None, 'normal'):
            if (volume['special_version'] not in ('tpb', 'one-shot', 'hard-cover', 'omnibus')
                    or len(PackInboxDB.volume_issues(volume['id'])) != 1):
                continue
        if data['volume_number'] is not None and data['volume_number'] != volume['volume_number']:
            continue
        if standalone:
            issues = PackInboxDB.volume_issues(volume['id'])
            if len(issues) != 1:
                continue
        else:
            issues = PackInboxDB.range_issues(volume['id'], *bounds)
            if not issues or issues[0]['calculated_issue_number'] != bounds[0] or issues[-1]['calculated_issue_number'] != bounds[1]:
                continue
        years = {volume['year']} | {
            int(i['date'][:4]) for i in issues if i['date'] and i['date'][:4].isdigit()}
        if data['year'] is not None and data['year'] not in years:
            continue
        candidates.append((volume, [i['id'] for i in issues]))
    if len(candidates) != 1:
        return None, [], 'No library match' if not candidates else 'Ambiguous: multiple library series match'
    volume, ids = candidates[0]
    owned = any(PackInboxDB.existing_issue_file(i) for i in ids)
    return volume, ids, 'Already owned (all or part of this file)' if owned else ''


def _managed_source(source: Path) -> Optional[Path]:
    """Only extracted files owned by completed Kapowarr pack jobs are disposable."""
    for row in PackInboxDB.ready_pack_folders():
        ready = Path(row[0]) / 'ready'
        if (ready.is_absolute() and ready in source.parents
                and not any(p.is_symlink() for p in (ready, *ready.parents))):
            return ready
    return None


def _cleanup_imported(row: InboxJournalEntry) -> None:
    """Delete a managed extracted source only after verifying its library copy.

    The row is the committed import journal record. External sources remain
    untouched. Verification failures retain the source and update the journal
    message; they do not undo a successful import.
    """
    cursor = get_db()
    source = Path(row['root']) / row['relative_path']
    ready = _managed_source(source)
    if ready is None:
        return  # External inboxes may be torrent-managed or read-only.
    try:
        source = safe_source(valid_root(str(ready)),
                             str(source.relative_to(ready)))
        before = source.stat()
        if before.st_size != row['size'] or str(before.st_mtime_ns) != row['mtime']:
            raise ValueError('Source changed since import; retained for review')
        destination = Path(row['destination'])
        record = PackInboxDB.imported_file_location(
            row['volume_id'], str(destination))
        if record is None or any(p.is_symlink() for p in (destination, *destination.parents)):
            raise ValueError(
                'Library copy missing or symlinked; source retained')
        if (Path(record['folder']).resolve() not in destination.resolve().parents
                or Path(record['root']).resolve() not in destination.resolve().parents):
            raise ValueError(
                'Library copy moved outside its library; source retained')
        bound = {r[0] for r in PackInboxDB.file_issue_bindings(record['id'])}
        if not set(json.loads(row['issue_ids'])).issubset(bound):
            raise ValueError('Library issue bindings changed; source retained')
        copied_before = destination.stat()
        with source.open('rb') as original, destination.open('rb') as copied:
            if _digest(original) != _digest(copied):
                raise ValueError(
                    'Library copy differs from source; source retained')

        def identity(value: os.stat_result) -> Tuple[int, int, int, int, int]:
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
        if identity(source.stat()) != identity(before) or identity(destination.stat()) != identity(copied_before):
            raise ValueError(
                'Files changed during cleanup verification; source retained')
        source.unlink()
        message = 'Copied and verified; extracted source deleted'
    except (OSError, ValueError) as error:
        message = 'Copied and verified; cleanup needs review: ' + str(error)
    PackInboxDB.set_message(message, row['token'])
    cursor.connection.commit()


def cleanup_selected(tokens: object) -> InboxListing:
    """Retry source cleanup for selected imported files in the saved inbox.

    Raises:
        InvalidKeyValue: Tokens are invalid or refer to unmanaged imports.

    Returns:
        Updated review results, including any retained-source explanations.
    """
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= 100 or any(not isinstance(t, str) for t in tokens):
        raise InvalidKeyValue(
            'items', 'Select between 1 and 100 imported files')
    with inbox_operation():
        root = valid_root(Settings().sv.pack_inbox_folder)
        cursor = get_db()
        rows = []
        for token in dict.fromkeys(tokens):
            row = PackInboxDB.imported_selection(token, str(root))
            if row is None or _managed_source(Path(row['root']) / row['relative_path']) is None:
                raise InvalidKeyValue(
                    'items', 'Only imported files from completed Kapowarr pack downloads can be cleaned')
            rows.append(cast(InboxJournalEntry, dict(row)))
        for row in rows:
            _cleanup_imported(row)
        return listing()


def _refresh_review_matches(token: str, volume_id: int, selected_ids: List[int]) -> None:
    """Recheck saved unresolved files after a selection, within inbox_operation.

    Do not expand a single-file torrent review to neighboring downloads, or
    replace explicit choices and import/hold states. Imports still revalidate.
    """
    root = valid_root(Settings().sv.pack_inbox_folder)
    selected = PackInboxDB.review_selection(token, str(root))
    selected_path = Path(selected['relative_path']) if selected else Path()
    selected_data = filename_data(selected_path.name)
    number = selected_data['issue_number']
    # Only carry a series choice across explicitly numbered ordinary issues.
    # A custom issue choice must not establish a numbering rule for siblings.
    propagate = False
    if selected_data['series'] and isinstance(number, (int, float)) and not selected_data['special_version']:
        numbered = PackInboxDB.range_issues(volume_id, number, number)
        propagate = len(numbered) == 1 and [numbered[0]['id']] == selected_ids
    for item in PackInboxDB.review_rows(str(root)):
        if item['status'] != 'review':
            continue
        row = PackInboxDB.review_selection(item['token'], str(root))
        if row is None or row['manual_match']:
            continue
        try:
            source = safe_source(root, row['relative_path'])
            stat = source.stat()
            if (source.suffix.lower() not in COMICS or not stat.st_size
                    or time() - stat.st_mtime < 30
                    or stat.st_size != row['size']
                    or str(stat.st_mtime_ns) != row['mtime']):
                continue
            data = filename_data(source.name)
            related = (propagate
                       and Path(row['relative_path']).parent == selected_path.parent
                       and all(data[key] == selected_data[key] for key in (
                           'series', 'year', 'volume_number', 'annual', 'special_version')))
            if related:
                issue_number = data['issue_number']
                if not isinstance(issue_number, (int, float)):
                    continue
                issues = PackInboxDB.range_issues(volume_id, issue_number, issue_number)
                if len(issues) != 1:
                    continue
                volume, ids, reason = manual_identity(volume_id, [issues[0]['id']])
            else:
                volume, ids, reason = classify(source.name)
            if volume is None:
                continue
            message = reason or f"{volume['title']} ({volume['year']}) — {len(ids)} issue(s)"
            PackInboxDB.save_scan(
                str(root), row['relative_path'], row['token'],
                'owned' if reason else 'matched', message, row['size'],
                row['mtime'], volume['id'], json.dumps(ids), related)
        except (ValueError, OSError):
            continue
    get_db().connection.commit()


def listing() -> InboxListing:
    """Return saved review rows with available cleanup and series actions."""
    folder = Settings().sv.pack_inbox_folder
    rows = PackInboxDB.review_rows(folder)
    for row in rows:
        source = Path(folder) / row['relative_path']
        row['can_cleanup'] = row['status'] == 'imported' and _managed_source(
            source) is not None and source.is_file()
        if row['status'] in ('review', 'matched', 'owned'):
            data = filename_data(Path(row['relative_path']).name)
            row['series_query'] = data['series'] or source.stem
    return dict(folder=folder, items=rows)


def scan(folder: object, *, filename: Optional[str] = None) -> InboxListing:
    """Save and scan a completed folder without importing its files.

    Raises:
        InvalidKeyValue: The folder is unsafe, unreadable, contains too many
            entries or belongs to an unfinished managed pack.

    Returns:
        Saved review results. Imported and held journal entries are preserved.
    """
    root = valid_root(folder)
    with inbox_operation():
        Settings().update({'pack_inbox_folder': str(root)})
        cursor = get_db()
        paths = []
        unfinished = [Path(row[0])
                      for row in PackInboxDB.unfinished_pack_folders()]
        if any(root == path or path in root.parents for path in unfinished):
            raise InvalidKeyValue(
                'folder', 'This pack download is unfinished or held; review it before scanning')

        def failed(error: OSError) -> None:
            raise InvalidKeyValue(
                'folder', 'Cannot read part of the inbox; check permissions')
        # Client discovery can target a single-file torrent without scanning
        # unrelated downloads that share its category folder.
        if filename is not None:
            paths.append(safe_source(root, filename))
        for directory, dirs, files in ([] if filename is not None else os.walk(
                root, followlinks=False, onerror=failed)):
            for name in list(dirs):
                if Path(directory) / name in unfinished:
                    dirs.remove(name)
                    continue
                if (Path(directory) / name).is_symlink():
                    paths.append(Path(directory) / name)
                    dirs.remove(name)
            paths.extend(Path(
                directory) / name for name in files if Path(name).suffix.lower() in COMICS | ARCHIVES)
            if len(paths) > 2000:
                raise InvalidKeyValue(
                    'folder', 'Choose a smaller completed folder (maximum 2,000 files per scan)')
        PackInboxDB.mark_sources_unseen(str(root))
        for path in sorted(paths):
            relative = str(path.relative_to(root))
            old = PackInboxDB.find_source(os.sep, str(path))
            if old:
                # Narrowing the inbox to a weekly subfolder must not bypass a hold.
                PackInboxDB.rebase_source(str(root), relative, old['id'])
                if old['status'] in TERMINAL:
                    continue
            size, mtime, volume_id, ids = 0, '', None, []
            status, message = 'review', ''
            manual = False
            try:
                source = safe_source(root, relative)
                stat = source.stat()
                size, mtime = stat.st_size, str(stat.st_mtime_ns)
                if source.suffix.lower() not in COMICS:
                    message = 'Outer archive: extract to a completed folder before scanning'
                elif stat.st_size == 0 or time() - stat.st_mtime < 30:
                    message = 'Empty or recently modified file; wait until completed, then scan again'
                else:
                    manual = bool(old and old['manual_match'] and old['size'] == size and old['mtime'] == mtime)
                    volume, ids, message = (manual_identity(old['volume_id'], json.loads(old['issue_ids']))
                                            if manual else classify(source.name))
                    if volume is not None:
                        volume_id = volume['id']
                        if not message:
                            status = 'matched'
                            message = f"{volume['title']} ({volume['year']}) — {len(ids)} issue(s)"
                        else:
                            status = 'owned'
            except (ValueError, OSError) as error:
                message = str(error)
            PackInboxDB.save_scan(
                str(root), relative, uuid4().hex, status, message,
                size, mtime, volume_id, json.dumps(ids), manual
            )
        cursor.connection.commit()
        return listing()


def _prepare_import(
    root: Path, row: InboxJournalEntry
) -> Tuple[Path, os.stat_result, Row, List[int], Path]:
    """Revalidate the source, match and destination before journaling a copy."""
    source = safe_source(root, row['relative_path'])
    before = source.stat()
    if before.st_size != row['size'] or str(before.st_mtime_ns) != row['mtime']:
        raise ValueError('File changed since preview; scan again')
    volume, ids, reason = (manual_identity(row['volume_id'], json.loads(row['issue_ids']))
                           if row['manual_match'] else classify(source.name))
    if reason or volume is None or volume['id'] != row['volume_id'] or ids != json.loads(row['issue_ids']):
        raise ValueError(reason or 'Library match changed; scan again')
    library = Path(volume['folder'])
    if not library.is_absolute() or library.is_symlink():
        raise ValueError('Library destination requires review')
    destination = library.resolve() / ('Pack-Inbox-' +
                                       row['token']) / source.name
    return source, before, volume, ids, destination


def _copy_verified(
    source: Path, destination: Path, before: os.stat_result
) -> None:
    """Copy to an exclusive file, fsync and verify identity and checksum.

    The caller must journal the import first. Failed output is retained for
    review and exceptions propagate to the per-file hold handler.
    """
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
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(
            'Source changed during copy; inspect the retained library copy')
    with destination.open('rb') as copied:
        if _digest(copied) != digest.hexdigest():
            raise ValueError('Copy checksum mismatch; review required')


def _bind_verified(
    destination: Path, size: int, ids: List[int], cursor: KapowarrCursor
) -> None:
    """Recheck ownership under a write transaction, then commit issue bindings.

    On failure the caller rolls back; the verified copy remains for review.
    """
    cursor.execute('BEGIN IMMEDIATE')
    if any(PackInboxDB.existing_issue_file(i) for i in ids):
        raise ValueError(
            'An issue was imported elsewhere during copying; review the retained copy')
    # Explicit verified issue bindings preserve this match during rescans.
    file_id = PackInboxDB.add_library_file(str(destination), size)
    PackInboxDB.bind_imported_issues([(file_id, i) for i in ids])
    cursor.connection.commit()


def _finish_import(
    row: InboxJournalEntry, volume: Row, destination: Path, cursor: KapowarrCursor
) -> None:
    """Rename if configured, commit completion, then verify source cleanup."""
    if Settings().sv.rename_downloaded_files:
        renamed = mass_rename(volume['id'], filepath_filter=[str(destination)],
                              process_individual_files=False, keep_volume_folder=True)
        if len(renamed) != 1:
            raise ValueError(
                'Renaming did not return the imported file; inspect library')
        destination = Path(renamed[0])
        PackInboxDB.set_destination(str(destination), row['token'])
    PackInboxDB.mark_imported(row['token'])
    cursor.connection.commit()
    imported = PackInboxDB.journal_entry(row['token'])
    _cleanup_imported(cast(InboxJournalEntry, dict(imported)))


def _import_one(
    root: Path, row: InboxJournalEntry, cursor: KapowarrCursor
) -> None:
    """Run one import and preserve review versus held failure semantics.

    The caller holds inbox_operation for the whole selected batch. The copy
    flag changes only after the importing journal entry has been committed.
    """
    destination = None
    copying = False
    try:
        source, before, volume, ids, destination = _prepare_import(root, row)
        PackInboxDB.mark_importing(str(destination), row['token'])
        cursor.connection.commit()
        copying = True
        _copy_verified(source, destination, before)
        _bind_verified(destination, before.st_size, ids, cursor)
        _finish_import(row, volume, destination, cursor)
    except Exception as error:
        cursor.connection.rollback()
        PackInboxDB.set_state(
            'held' if copying else 'review', str(error), row['token'])
        cursor.connection.commit()


def import_selected(tokens: object) -> InboxListing:
    """Revalidate, copy and bind up to 100 selected missing-issue matches.

    Each copy is journaled before writing, checksum-verified, then bound to
    its issues. Failed copies are held for review. Verified managed sources
    may be removed only after committing their library import.

    Raises:
        InvalidKeyValue: Selection tokens or the configured inbox are invalid.

    Returns:
        Updated review results, including per-file import or hold states.
    """
    if not isinstance(tokens, list) or not tokens or len(tokens) > 100 or any(not isinstance(t, str) for t in tokens):
        raise InvalidKeyValue('items', 'Select between 1 and 100 matched files')
    with inbox_operation():
        root = valid_root(Settings().sv.pack_inbox_folder)
        cursor = get_db()
        rows = []
        for token in dict.fromkeys(tokens):
            row = PackInboxDB.matched_selection(token, str(root))
            if row is None:
                raise InvalidKeyValue(
                    'items', 'Preview changed or item cannot be imported; scan again')
            rows.append(cast(InboxJournalEntry, dict(row)))
        for row in rows:
            _import_one(root, row, cursor)
        return listing()
