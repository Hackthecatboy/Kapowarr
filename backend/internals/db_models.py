# -*- coding: utf-8 -*-

"""
Interacting with the database
"""

from os import stat
from typing import Dict, Iterable, List, Union

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
    """Import journal queries used during managed-pack cleanup.

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
