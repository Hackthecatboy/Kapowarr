"""Settings cache isolation across committed and uncommitted SQLite writes."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from backend.internals.settings import Settings


class SettingsCache(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = str(Path(directory.name) / 'settings.db')
        self.writer = sqlite3.connect(path)
        self.reader = sqlite3.connect(path)
        self.addCleanup(self.writer.close)
        self.addCleanup(self.reader.close)
        self.writer.execute('CREATE TABLE config(key PRIMARY KEY, value)')
        self.writer.execute("INSERT INTO config VALUES('log_level',20)")
        self.writer.commit()
        self.settings = object.__new__(Settings)
        self.settings.clear_cache()
        self.addCleanup(self.settings.clear_cache)

    def read(self, connection, public=False):
        with patch('backend.internals.settings.get_db', return_value=connection.cursor()):
            if public:
                return self.settings.get_public_settings().log_level
            return self.settings.sv.log_level

    def update(self):
        with patch('backend.internals.settings.get_db', return_value=self.writer.cursor()), \
                patch('backend.internals.settings.set_log_level'):
            self.settings.update({'log_level': 10})

    def test_concurrent_old_read_does_not_survive_commit(self):
        self.assertEqual(self.read(self.reader), 20)
        self.update()
        self.assertTrue(self.writer.in_transaction)
        self.assertEqual(self.read(self.reader), 20)
        self.assertEqual(self.read(self.reader, public=True), 20)
        self.writer.commit()
        self.assertEqual(self.read(self.reader), 10)
        self.assertEqual(self.read(self.reader, public=True), 10)

    def test_uncommitted_writer_values_are_private_and_rollback_is_visible(self):
        self.update()
        self.assertEqual(self.read(self.writer), 10)
        self.assertEqual(self.read(self.writer, public=True), 10)
        self.assertEqual(self.read(self.reader), 20)
        self.writer.rollback()
        self.assertEqual(self.read(self.writer), 20)
        self.assertEqual(self.read(self.writer, public=True), 20)
        self.assertEqual(self.read(self.reader), 20)

    def test_own_commits_and_direct_database_updates_invalidate_cache(self):
        self.assertEqual(self.read(self.writer), 20)
        self.writer.execute("UPDATE config SET value=30 WHERE key='log_level'")
        self.writer.commit()
        self.assertEqual(self.read(self.writer), 30)
        self.assertEqual(self.read(self.reader), 30)

    def test_unchanged_reads_reuse_decoded_settings_without_consuming_shared_cursor(self):
        cursor = self.reader.cursor()
        cursor.execute('SELECT 1 UNION ALL SELECT 2')
        self.assertEqual(cursor.fetchone()[0], 1)
        with patch('backend.internals.settings.get_db', return_value=cursor), \
                patch.object(self.settings, '_read_settings', wraps=self.settings._read_settings) as read:
            self.settings.get_settings()
            self.settings.get_settings()
            self.settings.get_public_settings()
            self.assertEqual(read.call_count, 1)
        self.assertEqual(cursor.fetchone()[0], 2)

    def test_inflight_old_read_cannot_repopulate_a_current_cache_entry(self):
        started, resume = Event(), Event()
        path = self.writer.execute('PRAGMA database_list').fetchone()[2]
        observed, failures = [], []
        original = self.settings._read_settings
        def delayed_read(connection):
            result = original(connection)
            started.set()
            if not resume.wait(5):
                raise AssertionError('Reader was not released')
            return result
        def worker():
            connection = sqlite3.connect(path)
            try:
                observed.append(self.read(connection))
                observed.append(self.read(connection))
            except Exception as error:
                failures.append(error)
            finally:
                connection.close()
        with patch.object(self.settings, '_read_settings', side_effect=delayed_read):
            thread = Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                self.writer.execute("UPDATE config SET value=30 WHERE key='log_level'")
                self.settings.clear_cache()
                self.writer.commit()
            finally:
                resume.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(observed, [20, 30])

    def test_read_transaction_snapshot_is_not_cached_after_transaction_ends(self):
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.reader.execute('BEGIN')
        self.assertEqual(self.read(self.reader), 20)
        self.writer.execute("UPDATE config SET value=30 WHERE key='log_level'")
        self.writer.commit()
        self.assertEqual(self.read(self.reader), 20)
        self.reader.commit()
        self.assertEqual(self.read(self.reader), 30)
