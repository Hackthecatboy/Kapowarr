"""Pack finish previews, historical discovery and bounded subscriptions."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features import pack_downloads as downloads, pack_subscriptions as subscriptions
from backend.internals.db import DB_SCHEMA, KapowarrCursor


class PackSubscriptions(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = sqlite3.connect(':memory:'); self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close); self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        for module in ('pack_downloads', 'pack_subscriptions', 'pack_inbox'):
            patcher = patch('backend.features.' + module + '.get_db', return_value=self.cursor)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch('backend.internals.db_models.get_db', return_value=self.cursor)
        patcher.start(); self.addCleanup(patcher.stop)
        downloads._ACTIVE.clear(); downloads._CLEANUPS.clear()

    def pack(self):
        folder = self.root / 'Pack-test'; (folder / 'ready').mkdir(parents=True)
        (folder / 'payload.archive').write_bytes(b'archive')
        (folder / 'ready/unwanted.cbz').write_bytes(b'unwanted')
        self.cursor.execute("INSERT INTO pack_downloads VALUES('test','https://getcomics.org/pack/','Pack',?,?, 'test','ready','',0,0)", (str(self.root), str(folder)))
        self.db.commit()
        return folder

    def test_finish_requires_confirmation_and_preserves_library(self):
        folder = self.pack(); library = self.root / 'library.cbz'; library.write_bytes(b'keep')
        preview = downloads.cleanup_preview('test')
        self.assertEqual(len(preview['files']), 2)
        with self.assertRaises(InvalidKeyValue): downloads.cleanup_confirm(preview['token'], False)
        self.assertTrue(folder.exists())
        preview = downloads.cleanup_preview('test')
        downloads.cleanup_confirm(preview['token'], True)
        self.assertFalse(folder.exists()); self.assertEqual(library.read_bytes(), b'keep')
        self.assertEqual(self.cursor.execute("SELECT status FROM pack_downloads").fetchone()[0], 'finished')
        with self.assertRaises(InvalidKeyValue): downloads.cleanup_confirm(preview['token'], True)

    def test_changed_files_and_active_jobs_block_cleanup(self):
        folder = self.pack(); preview = downloads.cleanup_preview('test')
        (folder / 'ready/new.cbz').write_bytes(b'new')
        with self.assertRaises(InvalidKeyValue): downloads.cleanup_confirm(preview['token'], True)
        self.assertTrue((folder / 'payload.archive').exists())
        downloads._ACTIVE.add('test')
        with self.assertRaises(InvalidKeyValue): downloads.cleanup_preview('test')
        downloads._ACTIVE.clear()
        with self.assertRaises(InvalidKeyValue): downloads.download_root(str(folder / 'ready'))
        (folder / 'ready/symlink').symlink_to(self.root)
        with self.assertRaises(InvalidKeyValue): downloads.cleanup_preview('test')

    def test_past_search_returns_articles_and_pagination(self):
        response = Mock(text='<article class="post"><h1 class="post-title"><a href="https://getcomics.org/other/2026-weekly/">2026.09.16 Weekly Pack</a></h1></article><a class="next page-numbers">Next</a>')
        with patch.object(subscriptions, 'Session') as session:
            session.return_value.__enter__.return_value.get.return_value = response
            result = subscriptions.search('weekly pack', 2)
        self.assertEqual(len(result['articles']), 1); self.assertTrue(result['has_more'])
        self.assertEqual(result['page'], 2)

    def subscribe(self, automatic=True):
        subscriptions.create(dict(query='weekly pack', link_filter='Marvel', service='GetComics', folder=str(self.root), automatic=automatic))
        self.cursor.execute("UPDATE pack_subscriptions SET created='2020-01-01'")
        self.db.commit()

    def test_auto_download_is_unique_and_ambiguous_or_old_releases_need_review(self):
        self.subscribe()
        articles = [dict(url='https://getcomics.org/new/', title='2026.09.16 Weekly Pack'),
                    dict(url='https://getcomics.org/old/', title='2019.01.01 Weekly Pack')]
        with patch.object(subscriptions, 'search', return_value=dict(articles=articles, has_more=False)), \
                patch.object(downloads, 'preview', return_value=dict(choices=[dict(token='one', label='Marvel Main Server', service='GetComics', supported=True)])), \
                patch.object(downloads, 'start', return_value=dict(id='job')) as start:
            subscriptions.check(force=True); subscriptions.check(force=True)
        start.assert_called_once_with('one', str(self.root))
        states = {row['article']: row['status'] for row in subscriptions.listing()['releases']}
        self.assertEqual(states['https://getcomics.org/old/'], 'review')
        self.assertEqual(states['https://getcomics.org/new/'], 'tracked')
        articles = [dict(url='https://getcomics.org/ambiguous/', title='2026.09.17 Weekly Pack')]
        choice = dict(token='one', label='Marvel', service='GetComics', supported=True)
        with patch.object(subscriptions, 'search', return_value=dict(articles=articles, has_more=False)), \
                patch.object(downloads, 'preview', return_value=dict(choices=[choice, choice])), \
                patch.object(downloads, 'start') as start:
            subscriptions.check(force=True)
        start.assert_not_called()

    def test_paused_and_review_only_subscriptions_do_not_download(self):
        self.subscribe(False)
        with patch.object(subscriptions, 'search', return_value=dict(articles=[dict(url='https://getcomics.org/new/', title='2026.09.16 Weekly Pack')], has_more=False)), \
                patch.object(downloads, 'start') as start:
            subscriptions.check(force=True)
        start.assert_not_called()
        subscriptions.toggle(1, False)
        with patch.object(subscriptions, 'search') as search:
            subscriptions.check(force=True)
        search.assert_not_called()

    def test_weekly_schedule_persists_and_manual_check_bypasses_day(self):
        from datetime import datetime
        self.subscribe(False)
        today = datetime.now().weekday()
        subscriptions.schedule(1, (today + 1) % 7)
        with patch.object(subscriptions, 'search', return_value=dict(articles=[], has_more=False)) as search:
            subscriptions.check()
            search.assert_not_called()
            subscriptions.check(force=True)
            self.assertEqual(search.call_count, 1)
            subscriptions.schedule(1, today)
            subscriptions.check()
            subscriptions.check()
            self.assertEqual(search.call_count, 2)
            subscriptions.check(force=True)
            self.assertEqual(search.call_count, 3)
            from datetime import timedelta
            with patch.object(subscriptions, 'datetime', wraps=datetime) as clock:
                clock.now.return_value = datetime.now() + timedelta(days=7)
                subscriptions.check()
            self.assertEqual(search.call_count, 4)
        self.assertEqual(subscriptions.listing()['subscriptions'][0]['weekday'], today)

    def test_invalid_weekdays_and_failure_does_not_retry_hourly(self):
        from datetime import datetime
        self.subscribe(False)
        for value in (-1, 7, True, 'Monday', None):
            with self.assertRaises(InvalidKeyValue): subscriptions.schedule(1, value)
        subscriptions.schedule(1, datetime.now().weekday())
        with patch.object(subscriptions, 'search', side_effect=RuntimeError('offline')) as search:
            subscriptions.check()
            subscriptions.check()
            search.assert_called_once()

    def test_weekday_migration_preserves_existing_subscriptions(self):
        from backend.internals.db_migration import _migrate_pack_weekday
        self.subscribe(False)
        self.cursor.execute('ALTER TABLE pack_subscriptions DROP COLUMN weekday')
        self.cursor.execute('ALTER TABLE pack_subscriptions DROP COLUMN last_scheduled')
        with patch('backend.internals.db_migration.get_db', return_value=self.cursor):
            _migrate_pack_weekday()
            _migrate_pack_weekday()  # Earlier migrations may already use current schema.
        row = subscriptions.listing()['subscriptions'][0]
        self.assertEqual(row['query'], 'weekly pack')
        self.assertEqual(row['weekday'], 6)
        self.assertIsNone(row['last_scheduled'])

    def test_database_model_keeps_transaction_ownership_with_workflow(self):
        from backend.internals.db_models import PackSubscriptionsDB
        self.subscribe(False)
        PackSubscriptionsDB.set_weekday(1, 2)
        PackSubscriptionsDB.record_release(
            1, 'https://getcomics.org/example/', 'Example', 'pending', ''
        )
        self.db.rollback()
        self.assertEqual(PackSubscriptionsDB.releases(), [])
        PackSubscriptionsDB.record_release(
            1, 'https://getcomics.org/example/', 'Example', 'tracked', 'Saved'
        )
        self.db.commit()
        PackSubscriptionsDB.record_release(
            1, 'https://getcomics.org/example/', 'Example', 'pending', ''
        )
        self.assertEqual(PackSubscriptionsDB.releases()[0]['status'], 'tracked')
        self.assertEqual(PackSubscriptionsDB.pending(1), [])
        self.db.rollback()
