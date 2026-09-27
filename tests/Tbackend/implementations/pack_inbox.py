"""Filesystem and database regression tests for the reviewed pack importer."""

import hashlib
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from flask import Flask

from backend.base.custom_exceptions import InvalidKeyValue
from backend.features import pack_inbox
from backend.internals.db import DB_SCHEMA, KapowarrCursor
from backend.internals.db_migration import DatabaseMigrationHandler
from frontend.api import api


class PackInbox(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.inbox = self.base / 'completed'
        self.library = self.base / 'library'
        self.inbox.mkdir(); self.library.mkdir()
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        self.start_patch('backend.features.pack_inbox.get_db', return_value=self.cursor)
        self.start_patch('backend.internals.db_models.get_db', return_value=self.cursor)
        self.settings = SimpleNamespace(sv=SimpleNamespace(pack_inbox_folder='', rename_downloaded_files=False))
        self.settings.clear_cache = Mock()
        self.settings.update = lambda values: setattr(self.settings.sv, 'pack_inbox_folder', values['pack_inbox_folder'])
        self.start_patch('backend.features.pack_inbox.Settings', return_value=self.settings)
        self.db.execute('INSERT INTO root_folders(id,folder) VALUES(1,?)', (str(self.library),))
        self.volume(1, 'Alpha Comics')
        self.volume(2, 'Beta Comics')
        self.db.commit()

    def start_patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def volume(self, identifier, title):
        folder = self.library / str(identifier)
        folder.mkdir()
        self.db.execute('INSERT INTO volumes(id,comicvine_id,title,year,root_folder,folder) VALUES(?,?,?,2026,1,?)', (identifier,identifier,title,str(folder)))
        self.db.execute("INSERT INTO issues(id,volume_id,comicvine_id,issue_number,calculated_issue_number,date) VALUES(?,?,?,'1',1,'2026-01-01')", (identifier,identifier,identifier))

    def comic(self, name):
        path = self.inbox / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(path, 'w') as archive:
            archive.writestr('page.jpg', b'fixture')
        os.utime(path, (1000000000,1000000000))
        return path

    def scan(self):
        return pack_inbox.scan(str(self.inbox))['items']

    def test_scan_and_picker_file_checks_allow_other_database_writers(self):
        for number in ('1', '01', '001'):
            self.comic(f'Other Title #{number}.cbz')
        self.db.commit()
        path = str(self.base / 'concurrency.db')
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        self.db.backup(connection)
        connection.row_factory = sqlite3.Row
        other = sqlite3.connect(path, timeout=0)
        self.addCleanup(other.close)
        cursor = connection.cursor(factory=KapowarrCursor)
        original = pack_inbox.safe_source
        checks = []
        def checked_source(*args):
            other.execute("INSERT OR REPLACE INTO config(key,value) VALUES('concurrency_probe',1)")
            other.commit()
            checks.append(args)
            return original(*args)
        with patch('backend.features.pack_inbox.get_db', return_value=cursor), \
                patch('backend.internals.db_models.get_db', return_value=cursor), \
                patch('backend.features.pack_inbox.safe_source', side_effect=checked_source):
            result = pack_inbox.scan(str(self.inbox))
            self.assertGreaterEqual(len(checks), 3)
            checks.clear()
            result = pack_inbox.set_match(result['items'][0]['token'], 1, [1])
            self.assertGreaterEqual(len(checks), 3)
            self.assertTrue(all(row['status'] == 'matched' for row in result['items']))

    def test_failed_scan_publication_rolls_back_previous_results(self):
        self.comic('Alpha Comics 001 (2026).cbz')
        before = self.scan()
        self.comic('Beta Comics 001 (2026).cbz')
        original = pack_inbox.PackInboxDB.save_scan
        writes = []
        def fail_second(*args):
            writes.append(args)
            if len(writes) == 2:
                raise RuntimeError('Simulated publication failure')
            return original(*args)
        with patch.object(pack_inbox.PackInboxDB, 'save_scan', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.scan()
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(pack_inbox.listing()['items'], before)

    def test_scan_invalidates_stale_folder_cache_after_commit(self):
        self.comic('Alpha Comics 001 (2026).cbz')
        self.settings.sv.pack_inbox_folder = '/previous-inbox'
        def update(values):
            # Model another request caching the old committed folder after
            # Settings.update invalidates its cache but before scan commits.
            self.db.execute("INSERT OR REPLACE INTO config(key,value) VALUES('pack_inbox_folder',?)",
                            (values['pack_inbox_folder'],))
            self.settings.sv.pack_inbox_folder = '/previous-inbox'
        def clear():
            self.assertFalse(self.db.in_transaction)
            self.settings.sv.pack_inbox_folder = self.db.execute(
                "SELECT value FROM config WHERE key='pack_inbox_folder'").fetchone()[0]
        self.settings.update = update
        self.settings.clear_cache.side_effect = clear
        result = pack_inbox.scan(str(self.inbox))
        self.assertEqual(result['folder'], str(self.inbox))
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(pack_inbox.listing()['folder'], str(self.inbox))
        self.settings.clear_cache.assert_called_once()

    def test_manual_series_link_survives_rescan_and_imports_selected_issue(self):
        source = self.comic('Different Name #001 (2016).cbz')
        row = self.scan()[0]
        options = pack_inbox.match_options(row['token'], 1)
        self.assertEqual(options['selected'], [1])
        linked = pack_inbox.set_match(row['token'], 1, [1])['items'][0]
        self.assertEqual(linked['status'], 'matched')
        rescanned = self.scan()[0]
        self.assertEqual(rescanned['status'], 'matched')
        result = pack_inbox.import_selected([rescanned['token']])['items'][0]
        self.assertEqual(result['status'], 'imported')
        self.assertTrue(source.exists())
        self.assertEqual(self.db.execute('SELECT issue_id FROM issues_files').fetchone()[0], 1)

    def test_owned_manual_match_is_saved_but_cannot_be_imported(self):
        source = self.comic('Different Name #001 (2015).cbz')
        row = self.scan()[0]
        self.db.execute("INSERT INTO files(id,filepath,size) VALUES(1,'existing.cbz',1)")
        self.db.execute('INSERT INTO issues_files(file_id,issue_id) VALUES(1,1)')
        linked = pack_inbox.set_match(row['token'], 1, [1])['items'][0]
        self.assertEqual(linked['status'], 'owned')
        self.assertIn('Already owned', linked['message'])
        rescanned = self.scan()[0]
        self.assertEqual(rescanned['status'], 'owned')
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.import_selected([rescanned['token']])
        self.assertTrue(source.exists())
        self.assertEqual(self.db.execute('SELECT count(*) FROM files').fetchone()[0], 1)

    def test_partially_owned_manual_match_stays_excluded_from_import(self):
        self.comic('Different Name #001-002.cbz')
        row = self.scan()[0]
        self.db.execute("INSERT INTO issues(id,volume_id,comicvine_id,issue_number,calculated_issue_number) VALUES(3,1,3,'2',2)")
        self.db.execute("INSERT INTO files(id,filepath,size) VALUES(1,'existing.cbz',1)")
        self.db.execute('INSERT INTO issues_files(file_id,issue_id) VALUES(1,1)')
        linked = pack_inbox.set_match(row['token'], 1, [1,3])['items'][0]
        self.assertEqual(linked['status'], 'owned')
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.import_selected([linked['token']])

    def test_manual_match_rejects_wrong_series_issue_and_changed_source(self):
        source = self.comic('Different Name #001 (2016).cbz')
        row = self.scan()[0]
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.set_match(row['token'], 1, [2])
        source.write_bytes(b'changed')
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.set_match(row['token'], 1, [1])

    def test_manual_match_does_not_reopen_imports_or_reuse_changed_file(self):
        source = self.comic('Different Name #001 (2016).cbz')
        row = self.scan()[0]
        pack_inbox.set_match(row['token'], 1, [1])
        source.write_bytes(b'changed')
        os.utime(source, (1000000000, 1000000000))
        self.assertEqual(self.scan()[0]['status'], 'review')
        current = self.scan()[0]
        pack_inbox.set_match(current['token'], 1, [1])
        pack_inbox.import_selected([current['token']])
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.set_match(current['token'], 2, [2])

    def test_collection_prefix_and_explicit_one_shot_issue(self):
        self.db.execute("UPDATE volumes SET title='Avengers Standoff: Welcome to Pleasant Hill', year=2016, special_version='one-shot' WHERE id=1")
        self.comic('001 - Avengers Standoff - Welcome to Pleasant Hill #001 (2016).cbr')
        row = self.scan()[0]
        self.assertEqual(row['status'], 'matched')

    def test_publication_prefix_excludes_future_series_and_uses_issue_year(self):
        for title, start, year, number in (
                ('Wolverine', 1982, 1982, 4),
                ('Wonder Woman', 1941, 1942, 1),
                ('Black Panther', 2005, 2006, 18)):
            with self.subTest(title=title):
                self.db.execute('UPDATE volumes SET title=?, year=2024 WHERE id=1', (title,))
                self.db.execute('UPDATE issues SET calculated_issue_number=?, date=? WHERE id=1',
                                (number, '2024-09-01'))
                name = f'{year}-09 - {title} #{number:03} (Publisher) [scanner].cbz'
                data = pack_inbox.filename_data(name)
                self.assertEqual(data['series'], title)
                self.assertEqual(data['year'], year)
                self.assertEqual(data['issue_number'], number)
                self.assertIsNone(pack_inbox.classify(name)[0])
                self.db.execute('UPDATE volumes SET year=? WHERE id=1', (start,))
                self.db.execute('UPDATE issues SET date=? WHERE id=1', (f'{year}-09-01',))
                self.assertEqual(pack_inbox.classify(name)[1], [1])
                # A series start year alone cannot establish an issue's date.
                self.db.execute('UPDATE issues SET date=NULL WHERE id=1')
                self.assertIsNone(pack_inbox.classify(name)[0])

    def test_publication_prefix_preserves_markers_for_explicit_review(self):
        for marker in ('reprint', 'partial', 'incomplete'):
            name = f'2026-01 - Alpha Comics #001-{marker} (Publisher).cbz'
            self.assertEqual(pack_inbox.filename_data(name)['issue_number'], 1)
            self.assertIn('explicitly', pack_inbox.classify(name)[2])
        self.assertEqual(pack_inbox.filename_data(
            '001 - Alpha Comics #004 (2026).cbz')['issue_number'], 4)

    def test_manual_match_migration_preserves_journal(self):
        self.db.execute('ALTER TABLE pack_inbox RENAME TO original_inbox')
        self.db.execute('CREATE TABLE pack_inbox(id INTEGER, token TEXT)')
        self.db.execute("INSERT INTO pack_inbox VALUES(1,'existing')")
        from backend.internals.db_migration import _migrate_pack_manual_matches
        with patch('backend.internals.db_migration.get_db', return_value=self.cursor):
            _migrate_pack_manual_matches()
            _migrate_pack_manual_matches()
        self.assertEqual(tuple(self.db.execute('SELECT token,manual_match FROM pack_inbox').fetchone()), ('existing', 0))

    def test_single_file_scan_excludes_neighbors_and_retains_original(self):
        source = self.comic('Alpha Comics 001 (2026).cbz')
        other = self.comic('Beta Comics 001 (2026).cbz')
        rows = pack_inbox.scan(str(self.inbox), filename=source.name)['items']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['relative_path'], source.name)
        result = pack_inbox.import_selected([rows[0]['token']])
        self.assertEqual(result['items'][0]['status'], 'imported')
        self.assertTrue(source.exists())
        self.assertTrue(other.exists())

    def test_rename_setting_updates_journal_but_preserves_source(self):
        source=self.comic('Alpha Comics 001 (2026).cbz')
        original=source.read_bytes()
        token=self.scan()[0]['token']
        self.settings.sv.rename_downloaded_files=True
        def rename(volume_id, filepath_filter, **kwargs):
            self.assertTrue(kwargs['keep_volume_folder'])
            old=Path(filepath_filter[0])
            target=old.parent / 'Normalized 001.cbz'
            old.rename(target)
            self.db.execute('UPDATE files SET filepath=? WHERE filepath=?',(str(target),str(old)))
            return [str(target)]
        with patch('backend.features.pack_inbox.mass_rename',side_effect=rename) as renamer:
            result=pack_inbox.import_selected([token])
        renamer.assert_called_once()
        row=result['items'][0]
        self.assertEqual(row['status'],'imported')
        self.assertEqual(Path(row['destination']).name,'Normalized 001.cbz')
        self.assertEqual(source.read_bytes(),original)
        self.assertEqual(Path(row['destination']).read_bytes(),original)

    def test_rename_failure_holds_verified_copy_without_replaying(self):
        source=self.comic('Alpha Comics 001 (2026).cbz')
        token=self.scan()[0]['token']
        self.settings.sv.rename_downloaded_files=True
        with patch('backend.features.pack_inbox.mass_rename',side_effect=OSError('rename unavailable')):
            result=pack_inbox.import_selected([token])
        row=result['items'][0]
        self.assertEqual(row['status'],'held')
        self.assertTrue(Path(row['destination']).exists())
        self.assertTrue(source.exists())
        self.assertEqual(self.db.execute('SELECT count(*) FROM issues_files').fetchone()[0],1)
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.import_selected([token])

    def test_mixed_pack_imports_to_two_series_and_preserves_sources(self):
        a = self.comic('Week 1/Alpha Comics 001 (2026).cbz')
        b = self.comic('Week 1/Beta Comics 001 (2026).cbz')
        unknown = self.comic('Week 1/Unknown Comic 001 (2026).cbz')
        original = {p: (p.read_bytes(),p.stat().st_mtime_ns) for p in (a,b,unknown)}
        rows = self.scan()
        self.assertEqual(sorted(r['status'] for r in rows), ['matched','matched','review'])
        matched = [r['token'] for r in rows if r['status']=='matched']
        result = pack_inbox.import_selected(matched)
        self.assertEqual(sum(r['status']=='imported' for r in result['items']),2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM issues_files WHERE forced=1').fetchone()[0],2)
        for identifier, path in self.db.execute('SELECT i.volume_id,f.filepath FROM files f JOIN issues_files x ON f.id=x.file_id JOIN issues i ON i.id=x.issue_id'):
            self.assertTrue(Path(path).is_relative_to(self.library / str(identifier)))
            source = a if identifier == 1 else b
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).digest(),hashlib.sha256(source.read_bytes()).digest())
        for path, (data,mtime) in original.items():
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(path.stat().st_mtime_ns,mtime)
        self.assertEqual(sum(r['status']=='imported' for r in self.scan()),2)
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.import_selected(matched)
        self.assertEqual(self.db.execute('SELECT count(*) FROM files').fetchone()[0],2)

    def test_ambiguous_owned_and_unsupported_are_not_selected(self):
        self.volume(3, 'Alpha Comics')
        self.comic('Alpha Comics 001 (2026).cbz')
        self.comic('Beta Comics 001 (2026).cbz')
        self.comic('weekly.zip')
        self.db.execute("INSERT INTO files(id,filepath,size) VALUES(1,'existing.cbz',1)")
        self.db.execute('INSERT INTO issues_files(file_id,issue_id) VALUES(1,2)')
        rows = self.scan()
        self.assertEqual([r['status'] for r in rows],['review','owned','review'])
        self.assertIn('Ambiguous',rows[0]['message'])
        self.assertIn('Outer archive',rows[2]['message'])
        for row in rows:
            with self.assertRaises(InvalidKeyValue):
                pack_inbox.import_selected([row['token']])

    def test_collection_prefix_keeps_ambiguous_file_selectable_in_picker(self):
        self.volume(3, 'Civil War II FCBD')
        self.volume(4, 'Civil War II FCBD')
        self.db.execute("UPDATE volumes SET special_version='one-shot' WHERE id IN (3,4)")
        self.comic('008 - Civil War II FCBD (2026).cbz')
        row = self.scan()[0]
        self.assertIn('Ambiguous', row['message'])
        self.assertEqual(row['series_query'], 'Civil War II FCBD')
        options = pack_inbox.match_options(row['token'], 3)
        self.assertEqual(options['selected'], [3])
        pack_inbox.set_match(row['token'], 3, [3])
        self.assertEqual(self.scan()[0]['status'], 'matched')

    def test_series_choice_matches_numbered_siblings_and_survives_rescan(self):
        self.db.execute("UPDATE volumes SET title='Different Library Title' WHERE id=1")
        for issue in range(2, 8):
            self.db.execute(
                "INSERT INTO issues(id,volume_id,comicvine_id,issue_number,calculated_issue_number) VALUES(?,1,?,?,?)",
                (100 + issue, 100 + issue, str(issue), issue))
        for issue in range(1, 9):
            self.comic(f'Collection/Alternate Name #{issue:03}.cbz')
        self.comic('Elsewhere/Alternate Name #003.cbz')
        self.comic('Collection/Alternate Name #003 (2026).cbz')
        self.comic('Collection/Another Name #003.cbz')
        self.comic('Collection/Alternate Name.cbz')
        rows = {r['relative_path']: r for r in self.scan()}
        # Preserve an explicit choice of a different series for issue 2.
        second = rows['Collection/Alternate Name #002.cbz']['token']
        self.db.execute("UPDATE pack_inbox SET manual_match=1,volume_id=2,issue_ids='[2]',status='matched' WHERE token=?", (second,))
        pack_inbox.set_match(rows['Collection/Alternate Name #001.cbz']['token'], 1, [1])
        updated = {r['relative_path']: r for r in self.scan()}
        for issue in range(3, 8):
            row = updated[f'Collection/Alternate Name #{issue:03}.cbz']
            self.assertEqual(row['status'], 'matched')
            journal = self.db.execute('SELECT * FROM pack_inbox WHERE token=?', (row['token'],)).fetchone()
            self.assertEqual(journal['issue_ids'], f'[{100 + issue}]')
            self.assertTrue(journal['manual_match'])
        self.assertEqual(updated['Collection/Alternate Name #008.cbz']['status'], 'review')
        for name in ['Elsewhere/Alternate Name #003.cbz', 'Collection/Alternate Name #003 (2026).cbz',
                     'Collection/Another Name #003.cbz', 'Collection/Alternate Name.cbz']:
            self.assertEqual(updated[name]['status'], 'review')
        journal = self.db.execute("SELECT volume_id FROM pack_inbox WHERE relative_path='Collection/Alternate Name #002.cbz'").fetchone()
        self.assertEqual(journal['volume_id'], 2)

    def test_custom_issue_number_choice_does_not_propagate(self):
        self.comic('Alternate #005.cbz')
        self.comic('Alternate #001.cbz')
        rows = {r['relative_path']: r for r in self.scan()}
        result = pack_inbox.set_match(rows['Alternate #005.cbz']['token'], 1, [1])
        updated = {r['relative_path']: r for r in result['items']}
        self.assertEqual(updated['Alternate #001.cbz']['status'], 'review')

    def test_selecting_series_refreshes_only_unchanged_saved_review_files(self):
        self.comic('New Series 001 (2026).cbz')
        changed = self.comic('New Series 1 (2026).cbz')
        self.comic('Alternate 001 (2026).cbz')
        rows = {r['relative_path']: r for r in self.scan()}
        self.volume(3, 'New Series')
        self.comic('New Series 01 (2026).cbz')  # Not part of this review.
        changed.write_bytes(b'changed')
        result = pack_inbox.set_match(rows['Alternate 001 (2026).cbz']['token'], 3, [3])
        updated = {r['relative_path']: r for r in result['items']}
        self.assertEqual(len(updated), 3)
        self.assertEqual(updated['New Series 001 (2026).cbz']['status'], 'matched')
        self.assertEqual(updated['New Series 1 (2026).cbz']['status'], 'review')
        self.assertEqual(updated['Alternate 001 (2026).cbz']['status'], 'matched')
        self.assertEqual(updated['New Series 001 (2026).cbz']['token'],
                         rows['New Series 001 (2026).cbz']['token'])

    def test_symlinks_and_overlapping_folders_are_rejected(self):
        self.comic('Alpha Comics 001 (2026).cbz')
        (self.inbox/'link.cbz').symlink_to(self.library/'outside.cbz')
        (self.inbox/'linked-directory').symlink_to(self.library,target_is_directory=True)
        rows=self.scan()
        self.assertEqual(sum('Symlink' in r['message'] for r in rows),2)
        for path in [self.library,self.base,self.library/'1']:
            with self.assertRaises(InvalidKeyValue):
                pack_inbox.scan(str(path))
        with self.assertRaises(ValueError):
            pack_inbox.safe_source(self.inbox,'../outside.cbz')

    def test_changed_source_or_newly_owned_issue_requires_review(self):
        path=self.comic('Alpha Comics 001 (2026).cbz')
        token=self.scan()[0]['token']
        path.write_bytes(b'changed')
        result=pack_inbox.import_selected([token])['items'][0]
        self.assertEqual(result['status'],'review')
        self.assertIn('changed',result['message'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM files').fetchone()[0],0)
        os.utime(path,(1000000000,1000000000))
        token=self.scan()[0]['token']
        self.db.execute("INSERT INTO files(id,filepath,size) VALUES(1,'already.cbz',1)")
        self.db.execute('INSERT INTO issues_files(file_id,issue_id) VALUES(1,1)')
        result=pack_inbox.import_selected([token])['items'][0]
        self.assertEqual(result['status'],'review')
        self.assertIn('Already owned',result['message'])

    def test_copy_failure_is_held_and_never_replayed(self):
        source=self.comic('Week 1/Alpha Comics 001 (2026).cbz')
        data=source.read_bytes()
        token=self.scan()[0]['token']
        with patch('backend.features.pack_inbox._digest', side_effect=OSError('fixture failure')):
            result=pack_inbox.import_selected([token])['items'][0]
        self.assertEqual(result['status'],'held')
        self.assertTrue(Path(result['destination']).is_file())
        self.assertEqual(source.read_bytes(),data)
        self.assertEqual(self.db.execute('SELECT count(*) FROM files').fetchone()[0],0)
        self.assertEqual(self.scan()[0]['status'],'held')
        self.assertEqual(pack_inbox.scan(str(source.parent))['items'][0]['status'],'held')
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.import_selected([token])
        self.db.execute("UPDATE pack_inbox SET status='importing'")
        self.db.commit()
        self.assertEqual(self.scan()[0]['status'],'importing')

    def interrupted_copy(self):
        source = self.comic('Alpha Comics 001 (2026).cbz')
        token = self.scan()[0]['token']
        with patch('backend.features.pack_inbox._digest', side_effect=OSError('interrupted')):
            item = pack_inbox.import_selected([token])['items'][0]
        return source, token, Path(item['destination'])

    def test_recover_removed_copy_and_import(self):
        source, token, destination = self.interrupted_copy()
        original = source.read_bytes()
        destination.unlink()
        self.db.execute("UPDATE pack_inbox SET status='importing' WHERE token=?", (token,))
        self.db.commit()
        result = pack_inbox.recover_interrupted([token])['items'][0]
        self.assertEqual(result['status'], 'matched')
        self.assertIsNone(result['destination'])
        self.assertFalse(destination.parent.exists())
        self.assertEqual(pack_inbox.import_selected([token])['items'][0]['status'], 'imported')
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.db.execute('SELECT count(*) FROM files').fetchone()[0], 1)

    def test_recovery_refuses_remaining_or_symlinked_copy(self):
        source, token, destination = self.interrupted_copy()
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.recover_interrupted([token])
        self.assertTrue(destination.exists())
        destination.unlink()
        destination.symlink_to(self.base / 'missing')
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.recover_interrupted([token])
        self.assertTrue(destination.is_symlink())
        self.assertEqual(self.scan()[0]['status'], 'held')

    def test_recovery_refuses_changed_source_or_library_binding(self):
        source, token, destination = self.interrupted_copy()
        destination.unlink()
        original = source.stat()
        os.utime(source, (1000000100, 1000000100))
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.recover_interrupted([token])
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.db.execute('INSERT INTO files(id,filepath,size) VALUES(1,?,1)', (str(destination),))
        self.db.execute('INSERT INTO issues_files(file_id,issue_id) VALUES(1,1)')
        self.db.commit()
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.recover_interrupted([token])
        self.assertEqual(self.scan()[0]['status'], 'held')

    def test_recovery_rejects_active_operation_and_wrong_folder(self):
        source, token, destination = self.interrupted_copy()
        destination.unlink()
        with pack_inbox.inbox_operation():
            with self.assertRaises(InvalidKeyValue):
                pack_inbox.recover_interrupted([token])
        other = self.inbox / 'other'
        other.mkdir()
        self.settings.sv.pack_inbox_folder = str(other)
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.recover_interrupted([token])
        self.assertTrue(destination.parent.exists())

    def test_recent_files_and_stale_preview_tokens(self):
        source=self.comic('Alpha Comics 001 (2026).cbz')
        old=self.scan()[0]['token']
        self.scan()
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.import_selected([old])
        os.utime(source,None)
        self.assertIn('recently',self.scan()[0]['message'])

    def test_settling_boundary_is_thirty_seconds(self):
        source = self.comic('Alpha Comics 001 (2026).cbz')
        os.utime(source, (1000, 1000))
        with patch.object(pack_inbox, 'time', return_value=1029):
            self.assertIn('recently', self.scan()[0]['message'])
        with patch.object(pack_inbox, 'time', return_value=1030):
            self.assertEqual(self.scan()[0]['status'], 'matched')

    def test_unmatched_file_offers_filename_series_search(self):
        self.comic('2026 Weekly Pack/New Series 001 (2026) (Digital).cbz')
        row = self.scan()[0]
        self.assertEqual(row['status'], 'review')
        self.assertEqual(row['series_query'], 'New Series')
        self.volume(3, 'New Series')
        self.db.commit()
        row = self.scan()[0]
        self.assertEqual(row['status'], 'matched')
        self.assertEqual(row['series_query'], 'New Series')

    def managed_pack(self):
        source = self.comic('Managed/ready/Alpha Comics 001 (2026).cbz')
        folder = self.inbox / 'Managed'
        archive = folder / 'payload.archive'
        archive.write_bytes(b'retained pack archive')
        self.db.execute("INSERT INTO pack_downloads VALUES('managed','https://getcomics.org/pack/','Pack',?,?, 'managed','ready','',0,0)",
                        (str(self.inbox), str(folder)))
        self.db.commit()
        return source, archive

    def test_managed_pack_import_deletes_only_verified_source(self):
        source, archive = self.managed_pack()
        unmatched = self.comic('Managed/ready/Unknown 001 (2026).cbz')
        token = next(row['token'] for row in self.scan() if row['status'] == 'matched')
        result = pack_inbox.import_selected([token])
        imported = next(row for row in result['items'] if row['status'] == 'imported')
        self.assertFalse(source.exists())
        self.assertTrue(archive.exists() and unmatched.exists())
        self.assertTrue(Path(imported['destination']).is_file())
        self.assertIn('source deleted', imported['message'])
        self.assertFalse(imported['can_cleanup'])
        self.assertEqual(next(row for row in self.scan() if row['token'] == token)['status'], 'imported')

    def test_existing_import_cleanup_refuses_changed_copy_then_removes_source(self):
        source, archive = self.managed_pack()
        original = source.read_bytes()
        token = self.scan()[0]['token']
        with patch.object(pack_inbox, '_cleanup_imported'):
            row = pack_inbox.import_selected([token])['items'][0]
        self.assertTrue(row['can_cleanup'])
        destination = Path(row['destination'])
        destination.write_bytes(b'changed')
        row = pack_inbox.cleanup_selected([token])['items'][0]
        self.assertTrue(source.exists())
        self.assertEqual(row['status'], 'imported')
        self.assertIn('differs', row['message'])
        destination.write_bytes(original)
        pack_inbox.cleanup_selected([token])
        self.assertFalse(source.exists())
        self.assertTrue(archive.exists())

    def test_external_inbox_import_is_never_cleaned(self):
        source = self.comic('Alpha Comics 001 (2026).cbz')
        token = self.scan()[0]['token']
        row = pack_inbox.import_selected([token])['items'][0]
        self.assertFalse(row['can_cleanup'])
        with self.assertRaises(InvalidKeyValue):
            pack_inbox.cleanup_selected([token])
        self.assertTrue(source.exists())

    def test_api_authentication(self):
        app=Flask(__name__); app.register_blueprint(api,url_prefix='/api')
        client=app.test_client()
        settings=self.start_patch('frontend.api.Settings')
        settings.return_value.sv.api_key='fixture-key'
        self.start_patch('frontend.api.StartTypeHandlers.diffuse_timer')
        for method,path in [('GET','/pack-inbox'),('POST','/pack-inbox/scan'),('POST','/pack-inbox/import'),('POST','/pack-inbox/recover')]:
            self.assertEqual(client.open('/api'+path,method=method,json={'folder':str(self.inbox),'items':[]}).status_code,401)
        self.assertEqual(client.post('/api/pack-inbox/scan?api_key=fixture-key',json={'folder':str(self.inbox)}).status_code,200)

    def test_volume_add_reports_database_contention(self):
        app = Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        settings = self.start_patch('frontend.api.Settings')
        settings.return_value.sv.api_key = 'fixture-key'
        self.start_patch('frontend.api.StartTypeHandlers.diffuse_timer')
        add = self.start_patch('frontend.api.Library.add',
                              side_effect=sqlite3.OperationalError('database is locked'))
        response = app.test_client().post('/api/volumes?api_key=fixture-key',
                                         json={'comicvine_id': 90125, 'root_folder_id': 1})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json['error'], 'DatabaseBusy')
        self.assertEqual(add.call_count, 1)

    def test_migration_is_repeatable(self):
        self.db.execute('DROP TABLE pack_inbox')
        with patch('backend.internals.db_migration.get_db',return_value=self.cursor):
            DatabaseMigrationHandler.handlers[55]()
            DatabaseMigrationHandler.handlers[55]()
        self.assertEqual(self.scan(),[])

    def test_unnumbered_graphic_novel_import(self):
        self.db.execute("UPDATE volumes SET title='Doctor Strange: Endless Nightmare', special_version='tpb' WHERE id=1")
        source = self.comic('Doctor Strange - Endless Nightmare (2026) (digital) (Marika-Empire).cbz')
        row = self.scan()[0]
        self.assertEqual(row['status'], 'matched')
        result = pack_inbox.import_selected([row['token']])['items'][0]
        self.assertEqual(result['status'], 'imported')
        self.assertEqual(Path(result['destination']).read_bytes(), source.read_bytes())
        self.assertIn('Already owned', pack_inbox.classify(source.name)[2])

    def test_unnumbered_book_requires_unique_standalone_edition(self):
        name = 'Alpha Comics (2026).cbz'
        self.assertIsNone(pack_inbox.classify(name)[0])  # An ongoing series with only one issue is not a standalone.
        self.db.execute("UPDATE volumes SET special_version='tpb' WHERE id=1")
        self.assertEqual(pack_inbox.classify(name)[1], [1])
        self.assertIsNone(pack_inbox.classify('Alpha Comics (2025).cbz')[0])
        self.assertIsNone(pack_inbox.classify('Alpha Comics.cbz')[0])
        self.db.execute("UPDATE volumes SET title='Alpha Comics',special_version='one-shot' WHERE id=2")
        self.assertIn('Ambiguous', pack_inbox.classify(name)[2])
        self.db.execute("UPDATE volumes SET title='Beta Comics' WHERE id=2")
        self.db.execute("INSERT INTO issues(id,volume_id,comicvine_id,issue_number,calculated_issue_number) VALUES(3,1,3,'2',2)")
        self.assertIsNone(pack_inbox.classify(name)[0])

    def test_journal_and_issue_bindings_follow_caller_rollback(self):
        from backend.internals.db_models import PackInboxDB
        self.comic('Alpha Comics 001 (2026).cbz')
        row = self.scan()[0]
        PackInboxDB.mark_importing('/library/pending.cbz', row['token'])
        self.db.rollback()
        self.assertEqual(PackInboxDB.journal_entry(row['token'])['status'], 'matched')
        self.db.execute('BEGIN IMMEDIATE')
        file_id = PackInboxDB.add_library_file('/library/verified.cbz', 100)
        PackInboxDB.bind_imported_issues([(file_id, 1)])
        self.assertTrue(PackInboxDB.existing_issue_file(1))
        self.assertEqual(self.db.execute(
            'SELECT forced FROM issues_files WHERE file_id=?', (file_id,)
        ).fetchone()[0], 1)
        self.db.rollback()
        self.assertIsNone(PackInboxDB.existing_issue_file(1))
        self.assertIsNone(self.db.execute(
            'SELECT id FROM files WHERE id=?', (file_id,)
        ).fetchone())
        PackInboxDB.set_state('held', 'Review copy', row['token'])
        self.db.commit()
        PackInboxDB.mark_sources_unseen(str(self.inbox))
        self.assertEqual(PackInboxDB.journal_entry(row['token'])['status'], 'held')
