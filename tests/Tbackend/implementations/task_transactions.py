"""Task download preparation must not hold SQLite writes during network work."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.base.custom_exceptions import EnqueuingDownloadFailure
from backend.base.definitions import EnqueuingDownloadFailureReason
from backend.features.download_queue import DownloadHandler
from backend.features.tasks import RssSync, TaskHandler


class TaskTransactions(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = str(Path(temp.name) / 'test.db')
        self.db = sqlite3.connect(path)
        self.other = sqlite3.connect(path, timeout=0)
        self.addCleanup(self.db.close)
        self.addCleanup(self.other.close)
        self.db.executescript('CREATE TABLE task_history(action, title, timestamp);'
                              'CREATE TABLE writes(value);')

    def write_elsewhere(self, *args):
        self.other.execute("INSERT INTO writes VALUES ('concurrent')")
        self.other.commit()

    def test_rss_history_is_committed_before_preparing_downloads(self):
        handler = object.__new__(TaskHandler)
        handler.queue = [object()]
        task = RssSync()
        def discover():
            self.db.execute("INSERT INTO writes VALUES ('rss setting')")
            return [('link', 1, 1, None)]
        with patch.object(task, 'run', side_effect=discover), \
                patch('backend.features.tasks.get_db', side_effect=self.db.cursor), \
                patch('backend.features.tasks.WebSocket'), \
                patch.object(handler, '_process_queue'), \
                patch('backend.features.tasks.DownloadHandler') as downloads, \
                patch('backend.features.tasks.LOGGER.exception') as errors:
            downloads.return_value.add_multiple.side_effect = self.write_elsewhere
            handler._TaskHandler__run_task(task)
            errors.assert_not_called()
            downloads.return_value.add_multiple.assert_called_once()
        self.assertEqual(self.other.execute('SELECT count(*) FROM task_history').fetchone()[0], 1)

    def test_rejected_download_commits_before_sleep_and_next_entry(self):
        handler = object.__new__(DownloadHandler)
        def reject(*args):
            self.write_elsewhere()
            self.db.execute("INSERT INTO writes VALUES ('blocklist')")
            raise EnqueuingDownloadFailure(EnqueuingDownloadFailureReason.WEBPAGE_BROKEN)
        with patch.object(handler, 'add', side_effect=reject), \
                patch('backend.features.download_queue.get_db', side_effect=self.db.cursor), \
                patch('backend.features.download_queue.sleep', side_effect=self.write_elsewhere):
            handler.add_multiple([('link', 1, 1, None, False)] * 2)
        self.assertEqual(self.other.execute("SELECT count(*) FROM writes WHERE value='blocklist'").fetchone()[0], 2)
