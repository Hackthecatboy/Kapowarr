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
        self.settings = SimpleNamespace(sv=SimpleNamespace(pack_inbox_folder='', rename_downloaded_files=False))
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
        self.assertNotIn('series_query', row)

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
        for method,path in [('GET','/pack-inbox'),('POST','/pack-inbox/scan'),('POST','/pack-inbox/import')]:
            self.assertEqual(client.open('/api'+path,method=method,json={'folder':str(self.inbox),'items':[]}).status_code,401)
        self.assertEqual(client.post('/api/pack-inbox/scan?api_key=fixture-key',json={'folder':str(self.inbox)}).status_code,200)

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
