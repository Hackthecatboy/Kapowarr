# -*- coding: utf-8 -*-

"""
Interacting with the database
"""

from os import stat
from sqlite3 import Row
from typing import Any, Dict, Iterable, List, Tuple, Union

from backend.base.custom_exceptions import FileNotFound
from backend.base.definitions import (FileData, GeneralFileData, PackJob,
                                      PackRelease, PackSubscription)
from backend.base.helpers import first_of_subarrays
from backend.base.logging import LOGGER
from backend.internals.db import get_db


class FilesDB:
    @staticmethod
    def fetch(
        *,
        volume_id: Union[int, None] = None,
        issue_id: Union[int, None] = None,
        file_id: Union[int, None] = None,
        filepath: Union[str, None] = None
    ) -> List[FileData]:

        cursor = get_db()
        if volume_id:
            cursor.execute("""
                SELECT DISTINCT f.id, filepath, size
                FROM files f
                INNER JOIN issues_files if
                INNER JOIN issues i
                ON
                    f.id = if.file_id
                    AND if.issue_id = i.id
                WHERE volume_id = ?
                ORDER BY filepath;
                """,
                (volume_id,)
            )

        elif issue_id:
            cursor.execute("""
                SELECT DISTINCT f.id, filepath, size
                FROM files f
                INNER JOIN issues_files if
                ON f.id = if.file_id
                WHERE if.issue_id = ?
                ORDER BY filepath;
                """,
                (issue_id,)
            )

        elif file_id:
            cursor.execute("""
                SELECT id, filepath, size
                FROM files f
                WHERE f.id = ?
                LIMIT 1;
                """,
                (file_id,)
            )

        elif filepath:
            cursor.execute("""
                SELECT id, filepath, size
                FROM files f
                WHERE f.filepath = ?
                LIMIT 1;
                """,
                (filepath,)
            )

        else:
            cursor.execute("""
                SELECT id, filepath, size
                FROM files
                ORDER BY filepath;
                """
            )

        result: List[FileData] = cursor.fetchalldict() # type: ignore

        if (file_id or filepath) and not result:
            raise FileNotFound(file_id or filepath or '')

        return result

    @staticmethod
    def volume_of_file(filepath: str) -> Union[int, None]:
        volume_id = get_db().execute("""
            SELECT i.volume_id
            FROM
                files f
                INNER JOIN issues_files if
                INNER JOIN issues i
            ON
                f.id = if.file_id
                AND if.issue_id = i.id
            WHERE f.filepath = ?
            LIMIT 1;
            """,
            (filepath,)
        ).fetchone()

        if not volume_id:
            volume_id = get_db().execute("""
                SELECT vf.volume_id
                FROM
                    files f
                    INNER JOIN volume_files vf
                ON
                    f.id = vf.file_id
                WHERE f.filepath = ?
                LIMIT 1;
                """,
                (filepath,)
            ).fetchone()

        if not volume_id:
            return None
        return volume_id[0]

    @staticmethod
    def issues_covered(filepath: str) -> List[float]:
        return first_of_subarrays(get_db().execute("""
            SELECT DISTINCT
                i.calculated_issue_number
            FROM issues i
            INNER JOIN issues_files if
            INNER JOIN files f
            ON
                i.id = if.issue_id
                AND if.file_id = f.id
            WHERE f.filepath = ?
            ORDER BY calculated_issue_number;
            """,
            (filepath,)
        ))

    @staticmethod
    def add_file(
        filepath: str
    ) -> int:
        cursor = get_db()
        cursor.execute(
            "INSERT OR IGNORE INTO files(filepath, size) VALUES (?,?)",
            (filepath, stat(filepath).st_size)
        )

        if cursor.rowcount:
            LOGGER.debug(f'Added file to the database: {filepath}')
            return cursor.lastrowid

        return FilesDB.fetch(filepath=filepath)[0]["id"]

    @staticmethod
    def update_filepaths(old_to_new_mapping: Dict[str, str]) -> None:
        get_db().executemany(
            "UPDATE files SET filepath = ? WHERE filepath = ?;",
            ((new, old) for old, new in old_to_new_mapping.items())
        )
        return

    @staticmethod
    def delete_file(
        file_id: int
    ) -> None:
        get_db().execute(
            "DELETE FROM files WHERE id = ?;",
            (file_id,)
        )
        return

    @staticmethod
    def delete_filepath(
        filepath: str
    ) -> None:
        get_db().execute(
            "DELETE FROM files WHERE filepath = ?;",
            (filepath,)
        )
        return

    @staticmethod
    def delete_filepaths(
        filepaths: Iterable[str]
    ) -> None:
        get_db().executemany(
            "DELETE FROM files WHERE filepath = ?;",
            ((filepath,) for filepath in filepaths)
        )
        return

    @staticmethod
    def delete_linked_files(volume_id: int) -> None:
        get_db().execute(
            """
            DELETE FROM files
            WHERE id IN (
                SELECT DISTINCT file_id
                FROM issues_files
                INNER JOIN issues
                ON issues_files.issue_id = issues.id
                WHERE volume_id = ?
            ) OR id IN (
                SELECT DISTINCT file_id
                FROM volume_files
                WHERE volume_id = ?
            );
            """,
            (volume_id, volume_id)
        )
        return

    @staticmethod
    def delete_issue_linked_files(issue_id: int) -> None:
        get_db().execute(
            """
            DELETE FROM files
            WHERE id in (
                SELECT DISTINCT file_id
                FROM issues_files
                WHERE issue_id = ?
            );
            """,
            (issue_id,)
        )

    @staticmethod
    def delete_unmatched_files() -> None:
        get_db().execute("""
            WITH ids AS (
                SELECT file_id
                FROM issues_files
                UNION
                SELECT file_id
                FROM volume_files
            )
            DELETE FROM files
            WHERE id NOT IN ids;
            """
        )
        return


class GeneralFilesDB:
    @staticmethod
    def fetch(volume_id: int) -> List[GeneralFileData]:
        result: List[GeneralFileData] = get_db().execute("""
            SELECT f.id, filepath, size, file_type
            FROM files f
            INNER JOIN volume_files vf
            ON f.id = vf.file_id
            WHERE volume_id = ?;
            """,
            (volume_id,)
        ).fetchalldict() # type: ignore

        return result

    @staticmethod
    def delete_linked_files(volume_id: int) -> None:
        get_db().execute(
            """
            DELETE FROM files
            WHERE id IN (
                SELECT DISTINCT file_id
                FROM volume_files
                WHERE volume_id = ?
            );
            """,
            (volume_id,)
        )
        return


class PackSubscriptionsDB:
    """Persist pack subscriptions and discovered releases.

    Methods never commit. The feature owns transactions so weekly attempts,
    search pages and download decisions retain their recovery boundaries.
    """

    @staticmethod
    def fetch(enabled_only: bool = False) -> List[PackSubscription]:
        """Return subscriptions, optionally restricted to enabled entries."""
        if enabled_only:
            return get_db().execute(
                'SELECT * FROM pack_subscriptions WHERE enabled=1'
            ).fetchalldict()
        return get_db().execute(
            'SELECT * FROM pack_subscriptions ORDER BY id'
        ).fetchalldict()

    @staticmethod
    def releases() -> List[PackRelease]:
        """Return the most recently recorded 100 releases."""
        return get_db().execute(
            'SELECT * FROM pack_subscription_releases '
            'ORDER BY rowid DESC LIMIT 100'
        ).fetchalldict()

    @staticmethod
    def count() -> int:
        """Count saved subscriptions, including paused entries."""
        return get_db().execute(
            'SELECT count(*) FROM pack_subscriptions'
        ).fetchone()[0]

    @staticmethod
    def add(query: str, link_filter: str, service: str, folder: str,
            automatic: bool, created: str, weekday: int) -> None:
        """Insert validated subscription configuration."""
        get_db().execute(
            'INSERT INTO pack_subscriptions('
            'query,link_filter,service,folder,automatic,created,weekday'
            ') VALUES(?,?,?,?,?,?,?)',
            (query, link_filter, service, folder, automatic, created, weekday)
        )

    @staticmethod
    def set_enabled(ident: int, enabled: bool) -> None:
        """Set whether the subscription participates in future checks."""
        get_db().execute(
            'UPDATE pack_subscriptions SET enabled=? WHERE id=?',
            (enabled, ident)
        )

    @staticmethod
    def set_weekday(ident: int, weekday: int) -> None:
        """Save a validated weekday without clearing the last attempt."""
        get_db().execute(
            'UPDATE pack_subscriptions SET weekday=? WHERE id=?',
            (weekday, ident)
        )

    @staticmethod
    def mark_scheduled(ident: int, day: str) -> None:
        """Record a scheduled attempt before network access."""
        get_db().execute(
            'UPDATE pack_subscriptions SET last_scheduled=? WHERE id=?',
            (day, ident)
        )

    @staticmethod
    def mark_checked(ident: int, checked: str, message: str) -> None:
        """Record completion of a check."""
        get_db().execute(
            'UPDATE pack_subscriptions SET last_checked=?,message=? WHERE id=?',
            (checked, message, ident)
        )

    @staticmethod
    def set_message(ident: int, message: str) -> None:
        """Record a failure without replacing the previous check timestamp."""
        get_db().execute(
            'UPDATE pack_subscriptions SET message=? WHERE id=?',
            (message, ident)
        )

    @staticmethod
    def is_enabled(ident: int) -> bool:
        """Read the current enabled flag for an existing subscription."""
        return bool(get_db().execute(
            'SELECT enabled FROM pack_subscriptions WHERE id=?', (ident,)
        ).fetchone()[0])

    @staticmethod
    def record_release(ident: int, article: str, title: str,
                       status: str, message: str) -> None:
        """Insert a discovered article without resetting existing history."""
        get_db().execute(
            'INSERT OR IGNORE INTO pack_subscription_releases '
            '(subscription_id,article,title,status,message) VALUES(?,?,?,?,?)',
            (ident, article, title, status, message)
        )

    @staticmethod
    def pending(ident: int) -> List[PackRelease]:
        """Fetch pending releases in the existing article-URL order."""
        return get_db().execute(
            "SELECT * FROM pack_subscription_releases "
            "WHERE subscription_id=? AND status='pending' ORDER BY article",
            (ident,)
        ).fetchalldict()

    @staticmethod
    def set_release_status(ident: int, article: str,
                           status: str, message: str) -> None:
        """Update the processing state of one subscription/article pair."""
        get_db().execute(
            'UPDATE pack_subscription_releases SET status=?,message=? '
            'WHERE subscription_id=? AND article=?',
            (status, message, ident, article)
        )



class PackDownloadsDB:
    """Download-job persistence; callers retain commit and lock ownership."""

    @staticmethod
    def fetch() -> List[PackJob]:
        """Return the most recent 100 jobs, including finished history."""
        return get_db().execute(
            'SELECT * FROM pack_downloads ORDER BY rowid DESC LIMIT 100'
        ).fetchalldict()

    @staticmethod
    def get(ident: str) -> Union[PackJob, None]:
        """Return one job, or None when its identity is unknown."""
        row = get_db().execute(
            'SELECT * FROM pack_downloads WHERE id=?', (ident,)
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def active_ids() -> List[str]:
        """Return persisted downloading/extracting IDs for restart recovery."""
        return [row[0] for row in get_db().execute(
            "SELECT id FROM pack_downloads "
            "WHERE status IN ('downloading','extracting')"
        ).fetchall()]

    @staticmethod
    def folders(exclude_id: Union[str, None] = None) -> List[str]:
        """Return all managed folders, optionally excluding one job."""
        if exclude_id is None:
            rows = get_db().execute('SELECT folder FROM pack_downloads').fetchall()
        else:
            rows = get_db().execute(
                'SELECT folder FROM pack_downloads WHERE id != ?', (exclude_id,)
            ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def has_identity(identity: str) -> bool:
        """Check whether a particular article/mirror has already been submitted."""
        return get_db().execute(
            'SELECT 1 FROM pack_downloads WHERE identity=?', (identity,)
        ).fetchone() is not None

    @staticmethod
    def article_has_download(article: str) -> bool:
        """Check for any saved job, including held and finished jobs."""
        return get_db().execute(
            'SELECT 1 FROM pack_downloads WHERE article=?', (article,)
        ).fetchone() is not None

    @staticmethod
    def add(ident: str, article: str, title: str, root: str,
            folder: str, identity: str) -> None:
        """Insert a downloading job before its worker is started."""
        get_db().execute(
            'INSERT INTO pack_downloads(id,article,title,root,folder,identity,'
            "status) VALUES(?,?,?,?,?,?,'downloading')",
            (ident, article, title, root, folder, identity)
        )

    @staticmethod
    def update(ident: str, *, status: Union[str, None] = None,
               message: Union[str, None] = None,
               received: Union[int, None] = None,
               total: Union[int, None] = None) -> None:
        """Update supplied progress/status fields without changing job ownership."""
        values = {
            key: value for key, value in (
                ('status', status), ('message', message),
                ('received', received), ('total', total)
            ) if value is not None
        }
        if values:
            get_db().execute(
                'UPDATE pack_downloads SET '
                + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                (*values.values(), ident)
            )


class PackInboxDB:
    """Pack import journal, matching and cleanup queries.

    Callers own commits and perform filesystem validation before mutations.
    """

    @staticmethod
    def importing_paths() -> List[Dict[str, str]]:
        """Return paths whose import was started but not finalized."""
        return get_db().execute(
            "SELECT root,relative_path FROM pack_inbox WHERE status='importing'"
        ).fetchalldict()

    @staticmethod
    def cleanup_records() -> List[Dict[str, str]]:
        """Return path/status records for determining which pack rows to discard."""
        return get_db().execute(
            'SELECT token,root,relative_path,status FROM pack_inbox'
        ).fetchalldict()

    @staticmethod
    def discard(token: str) -> None:
        """Record removal of an unimported source after confirmed pack cleanup."""
        get_db().execute(
            "UPDATE pack_inbox SET status='discarded',"
            "message='Discarded when finishing pack' WHERE token=?", (token,)
        )

    @staticmethod
    def library_folders() -> List[Row]:
        """Return root and volume folders for overlap validation."""
        return get_db().execute(
            'SELECT folder FROM root_folders UNION SELECT folder FROM volumes '
            'WHERE folder IS NOT NULL'
        ).fetchall()

    @staticmethod
    def matching_volumes() -> List[Row]:
        """Return library identities used by filename matching."""
        return get_db().execute(
            'SELECT '
            'id,title,alt_title,year,volume_number,special_version,folder FROM '
            'volumes'
        ).fetchall()

    @staticmethod
    def volume_issues(volume_id: int) -> List[Row]:
        """Return every issue for a possible standalone edition."""
        return get_db().execute(
            'SELECT id,calculated_issue_number,date FROM issues WHERE '
            'volume_id=?',
            (volume_id,)
        ).fetchall()

    @staticmethod
    def range_issues(volume_id: int, first: float, last: float) -> List[Row]:
        """Return an inclusive issue range ordered by issue number."""
        return get_db().execute(
            'SELECT id,calculated_issue_number,date FROM issues WHERE '
            'volume_id=? AND calculated_issue_number BETWEEN ? AND ? ORDER BY '
            'calculated_issue_number',
            (volume_id, first, last)
        ).fetchall()

    @staticmethod
    def existing_issue_file(issue_id: int) -> Union[Row, None]:
        """Return a row when an issue already has a file binding."""
        return get_db().execute(
            'SELECT 1 FROM issues_files WHERE issue_id=? LIMIT 1',
            (issue_id,)
        ).fetchone()

    @staticmethod
    def ready_pack_folders() -> List[Row]:
        """Return managed folders eligible for source cleanup."""
        return get_db().execute(
            "SELECT folder FROM pack_downloads WHERE status='ready'"
        ).fetchall()

    @staticmethod
    def imported_file_location(volume_id: int, filepath: str) -> Union[Row, None]:
        """Locate a library file and the expected volume/root folders."""
        return get_db().execute(
            'SELECT f.id,v.folder,r.folder AS root FROM files f JOIN volumes v '
            'ON v.id=? JOIN root_folders r ON r.id=v.root_folder WHERE '
            'f.filepath=?',
            (volume_id, filepath)
        ).fetchone()

    @staticmethod
    def file_issue_bindings(file_id: int) -> List[Row]:
        """Return all issue IDs bound to a library file."""
        return get_db().execute(
            'SELECT issue_id FROM issues_files WHERE file_id=?',
            (file_id,)
        ).fetchall()

    @staticmethod
    def set_message(message: str, token: str) -> None:
        """Update a journal explanation without changing its status."""
        get_db().execute(
            'UPDATE pack_inbox SET message=? WHERE token=?',
            (message, token)
        )

    @staticmethod
    def imported_selection(token: str, root: str) -> Union[Row, None]:
        """Resolve an imported selection only within the saved inbox."""
        return get_db().execute(
            'SELECT * FROM pack_inbox WHERE token=? AND root=? AND '
            "status='imported'",
            (token, root)
        ).fetchone()

    @staticmethod
    def review_rows(root: str) -> List[Dict[str, Any]]:
        """Return saved review rows in relative-path order."""
        return get_db().execute(
            'SELECT token,relative_path,status,message,destination FROM '
            'pack_inbox WHERE root=? ORDER BY relative_path',
            (root,)
        ).fetchalldict()

    @staticmethod
    def unfinished_pack_folders() -> List[Row]:
        """Return managed folders excluded from scanning."""
        return get_db().execute(
            "SELECT folder FROM pack_downloads WHERE status != 'ready'"
        ).fetchall()

    @staticmethod
    def mark_sources_unseen(root: str) -> None:
        """Reset reviewable rows before a scan; preserve import and hold states."""
        get_db().execute(
            "UPDATE pack_inbox SET status='review', message='Source no longer "
            "present; scan again after restoring it' WHERE root=? AND status "
            "NOT IN ('imported','importing','held')",
            (root,)
        )

    @staticmethod
    def find_source(separator: str, filepath: str) -> Union[Row, None]:
        """Find a journal entry even when the scan root has changed."""
        return get_db().execute(
            'SELECT id,status FROM pack_inbox WHERE root || ? || '
            'relative_path=?',
            (separator, filepath)
        ).fetchone()

    @staticmethod
    def rebase_source(root: str, relative_path: str, ident: int) -> None:
        """Rebase a journal path without changing its recovery state."""
        get_db().execute(
            'UPDATE pack_inbox SET root=?,relative_path=? WHERE id=?',
            (root, relative_path, ident)
        )

    @staticmethod
    def save_scan(
        root: str,
        relative_path: str,
        token: str,
        status: str,
        message: str,
        size: int,
        mtime: str,
        volume_id: Union[int, None],
        issue_ids: str
    ) -> None:
        """Insert or refresh a scanned file without replacing its destination."""
        get_db().execute(
            'INSERT INTO pack_inbox('
            'root,relative_path,token,status,message,size,mtime,volume_id,issue_ids'
            ') VALUES(?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(root,relative_path) DO UPDATE SET '
            'token=excluded.token,status=excluded.status,'
            'message=excluded.message,size=excluded.size,mtime=excluded.mtime,'
            'volume_id=excluded.volume_id,issue_ids=excluded.issue_ids',
            (root, relative_path, token, status, message,
             size, mtime, volume_id, issue_ids)
        )

    @staticmethod
    def matched_selection(token: str, root: str) -> Union[Row, None]:
        """Resolve a matched selection only within the saved inbox."""
        return get_db().execute(
            'SELECT * FROM pack_inbox WHERE token=? AND root=? AND '
            "status='matched'",
            (token, root)
        ).fetchone()

    @staticmethod
    def mark_importing(destination: str, token: str) -> None:
        """Journal the intended destination before filesystem writes."""
        get_db().execute(
            "UPDATE pack_inbox SET status='importing',message='Copy started; "
            "interrupted imports require review',destination=? WHERE token=?",
            (destination, token)
        )

    @staticmethod
    def add_library_file(filepath: str, size: int) -> Union[int, None]:
        """Insert the verified library file within the caller transaction."""
        return get_db().execute(
            'INSERT INTO files(filepath,size) VALUES(?,?)',
            (filepath, size)
        ).lastrowid

    @staticmethod
    def bind_imported_issues(bindings: List[Tuple[int, int]]) -> None:
        """Bind verified issues with forced matching; leave commit to the caller."""
        get_db().executemany(
            'INSERT INTO issues_files(file_id,issue_id,forced) VALUES(?,?,1)',
            bindings
        )

    @staticmethod
    def set_destination(destination: str, token: str) -> None:
        """Save the final path returned by renaming."""
        get_db().execute(
            'UPDATE pack_inbox SET destination=? WHERE token=?',
            (destination, token)
        )

    @staticmethod
    def mark_imported(token: str) -> None:
        """Record completion before any managed-source cleanup."""
        get_db().execute(
            "UPDATE pack_inbox SET status='imported',message='Copied and "
            "verified; original retained' WHERE token=?",
            (token,)
        )

    @staticmethod
    def journal_entry(token: str) -> Union[Row, None]:
        """Return the complete journal record for one token."""
        return get_db().execute(
            'SELECT * FROM pack_inbox WHERE token=?',
            (token,)
        ).fetchone()

    @staticmethod
    def set_state(status: str, message: str, token: str) -> None:
        """Record a review or hold after the caller rolls back failed work."""
        get_db().execute(
            'UPDATE pack_inbox SET status=?,message=? WHERE token=?',
            (status, message, token)
        )
