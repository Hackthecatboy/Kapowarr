"""Duplicate review checks real contents without changing library data."""
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.features import duplicates
from backend.internals.db import DB_SCHEMA, KapowarrCursor


class DuplicateReview(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        self.db.execute('INSERT INTO root_folders(id,folder) VALUES(1,?)',(str(self.root),))
        self.db.execute("INSERT INTO volumes(id,comicvine_id,title,year,root_folder,folder) VALUES(1,1,'Example',2026,1,?)", (str(self.library),))
        self.db.execute("INSERT INTO issues(id,volume_id,comicvine_id,issue_number,calculated_issue_number) VALUES(1,1,1,'1',1)")
        p = patch('backend.features.duplicates.get_db', return_value=self.cursor)
        p.start()
        self.addCleanup(p.stop)

    def file(self, ident, name, data):
        path = self.library / name
        path.write_bytes(data)
        self.db.execute('INSERT INTO files VALUES(?,?,?)',(ident,str(path),len(data)))
        self.db.execute('INSERT INTO issues_files VALUES(?,1,0)',(ident,))
        return path

    def test_identical_suffix_copy_and_different_release_are_distinguished(self):
        a = self.file(1,'Example 001.cbz',b'abc')
        b = self.file(2,'Example 001 (1).cbz',b'abc')
        c = self.file(3,'Example 001 (2).cbz',b'xyz')
        before = [(p.read_bytes(),p.stat().st_mtime_ns) for p in (a,b,c)]
        result = duplicates.scan()
        self.assertEqual(len(result['exact']),1)
        self.assertEqual([f['id'] for f in result['exact'][0]['files']],[1,2])
        self.assertEqual(len(result['same_issue']),1)
        self.assertEqual(before,[(p.read_bytes(),p.stat().st_mtime_ns) for p in (a,b,c)])
        self.assertEqual(self.db.execute('SELECT count(*) FROM files').fetchone()[0],3)
        self.assertEqual(duplicates.scan(99)['scanned_files'],0)

    def test_symlinks_missing_files_and_scan_limits_are_reported(self):
        a = self.file(1,'a.cbz',b'abc')
        b = self.file(2,'b.cbz',b'abc')
        b.unlink()
        b.symlink_to(a)
        result = duplicates.scan(1)
        self.assertFalse(result['exact'])
        self.assertIn('Symlink',result['errors'][0]['reason'])
        b.unlink()
        b.write_bytes(b'abc')
        with patch.object(duplicates,'MAX_BYTES',1):
            result=duplicates.scan(1)
        self.assertTrue(result['limited'])
        self.assertFalse(result['exact'])
        self.assertEqual(len(result['same_issue']),1)

    def test_hard_links_are_labelled_and_never_removed(self):
        a=self.file(1,'a.cbz',b'abc')
        b=self.file(2,'b.cbz',b'abc')
        b.unlink()
        os.link(a,b)
        result=duplicates.scan()
        self.assertTrue(result['exact'][0]['same_physical_file'])
        self.assertTrue(a.exists() and b.exists())

    def test_scan_endpoint_requires_auth(self):
        from flask import Flask
        from frontend.api import api
        from types import SimpleNamespace
        app=Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        with patch('frontend.api.Settings',return_value=SimpleNamespace(sv=SimpleNamespace(api_key='test'))):
            client=app.test_client()
            self.assertEqual(client.post('/api/duplicates/scan',json={}).status_code,401)
            response=client.post('/api/duplicates/scan?api_key=test',json={'volume_id':1})
            self.assertEqual(response.status_code,200)
            self.assertEqual(client.post('/api/duplicates/scan?api_key=test',json={'volume_id':True}).status_code,400)

    def test_import_name_preview_keeps_existing_volume_folder_and_avoids_collision(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from backend.implementations.naming import preview_mass_rename
        source=self.file(1,'incoming.cbz',b'abc')
        existing=self.library / 'Normalized.cbz'
        existing.write_bytes(b'keep')
        volume=Mock()
        volume.get_data.return_value=SimpleNamespace(folder=str(self.library),custom_folder=False)
        volume.get_all_files.return_value=[dict(filepath=str(source))]
        with patch('backend.implementations.naming.Volume',return_value=volume), \
                patch('backend.implementations.naming.FilesDB.issues_covered',return_value=[1.0]), \
                patch('backend.implementations.naming.generate_issue_name',return_value='Normalized'), \
                patch('backend.implementations.naming.RootFolders') as roots:
            names, folder=preview_mass_rename(1,filepath_filter=[str(source)],keep_volume_folder=True)
        roots.assert_not_called()
        self.assertIsNone(folder)
        self.assertEqual(Path(names[str(source)]).parent,self.library)
        self.assertNotEqual(names[str(source)],str(existing))
        self.assertEqual(existing.read_bytes(),b'keep')
