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
        self.settings = SimpleNamespace(sv=SimpleNamespace(pack_inbox_folder=''))
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
