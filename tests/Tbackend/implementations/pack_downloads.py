"""Pack downloads use isolated folders, durable jobs and safe ZIP extraction."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile, ZipInfo

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import GCDownloadService
from backend.features import pack_downloads as packs
from backend.internals.db import DB_SCHEMA, KapowarrCursor


class PackDownloads(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = sqlite3.connect(':memory:'); self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close); self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        for module in ('pack_downloads', 'pack_inbox'):
            mock = patch('backend.features.' + module + '.get_db', return_value=self.cursor)
            mock.start(); self.addCleanup(mock.stop)
        blocklist = patch('backend.implementations.blocklist.get_db', return_value=self.cursor)
        blocklist.start(); self.addCleanup(blocklist.stop)
        packs._ACTIVE.clear(); packs._PREVIEWS.clear()
        self.addCleanup(packs._ACTIVE.clear); self.addCleanup(packs._PREVIEWS.clear)

    def test_article_validation(self):
        self.assertEqual(packs.article_url('https://www.getcomics.org/other/weekly/?utm=a'),
                         'https://getcomics.org/other/weekly/')
        for url in ('http://getcomics.org/pack', 'https://example.org/pack',
                    'https://getcomics.org.evil.test/pack', 'https://user@getcomics.org/pack'):
            with self.assertRaises(InvalidKeyValue): packs.article_url(url)

    def test_preview_labels_without_series_and_submission_is_persisted(self):
        response = Mock(url='https://getcomics.org/other/weekly/', text='<h1>Weekly Pack</h1><section class="post-contents"><a href="https://host.test/pack">Main Server</a></section>')
        session = Mock(); session.get.return_value = response
        with patch.object(packs, 'Session') as factory:
            factory.return_value.__enter__.return_value = session
            data = packs.preview(response.url)
        self.assertEqual(data['title'], 'Weekly Pack')
        self.assertEqual(len(data['choices']), 1)
        token = data['choices'][0]['token']
        with patch.object(packs, 'Server') as server:
            result = packs.start(token, str(self.root))
            server.return_value.get_db_thread.return_value.start.assert_called_once()
        row = self.cursor.execute('SELECT * FROM pack_downloads').fetchone()
        self.assertEqual(row['id'], result['id']); self.assertEqual(row['status'], 'downloading')
        self.assertTrue(Path(row['folder']).is_dir())
        with self.assertRaises(InvalidKeyValue): packs.start(token, str(self.root))
        packs._ACTIVE.clear()
        self.assertEqual(packs.listing()[0]['status'], 'held')
        with self.assertRaises(InvalidKeyValue): packs.start(token, str(self.root))

    def test_zip_keeps_original_and_nested_comics(self):
        archive = self.root / 'pack.zip'
        with ZipFile(archive, 'w') as output:
            output.writestr('Marvel/Comic 001.cbz', b'comic archive bytes')
            output.writestr('DC/Other 002.cbr', b'another comic')
        ready = self.root / 'ready'; packs.extract_zip(archive, ready)
        self.assertTrue(archive.exists())
        self.assertEqual((ready / 'Marvel/Comic 001.cbz').read_bytes(), b'comic archive bytes')
        with self.assertRaises(FileExistsError): packs.extract_zip(archive, ready)

    def test_zip_traversal_symlink_and_limits_are_rejected_before_extracting(self):
        for name in ('../escape.cbz', '/escape.cbz', 'a\\escape.cbz'):
            archive = self.root / 'bad.zip'
            with ZipFile(archive, 'w') as output: output.writestr(name, b'bad')
            with self.assertRaises(ValueError): packs.extract_zip(archive, self.root / 'ready')
            self.assertFalse((self.root / 'ready').exists())
        with ZipFile(archive, 'w') as output:
            entry = ZipInfo('link.cbz'); entry.external_attr = 0o120777 << 16
            output.writestr(entry, b'/etc/passwd')
        with self.assertRaises(ValueError): packs.extract_zip(archive, self.root / 'ready')
        with ZipFile(archive, 'w') as output: output.writestr('comic.cbz', b'1234')
        with patch.object(packs, 'MAX_BYTES', 2):
            with self.assertRaises(ValueError): packs.extract_zip(archive, self.root / 'ready')

    def test_endpoints_require_auth_and_valid_body(self):
        from flask import Flask
        from frontend.api import api
        app = Flask(__name__); app.register_blueprint(api, url_prefix='/api')
        with patch('frontend.api.Settings', return_value=SimpleNamespace(sv=SimpleNamespace(api_key='test'))):
            client = app.test_client()
            self.assertEqual(client.get('/api/pack-downloads').status_code, 401)
            self.assertEqual(client.post('/api/pack-downloads/download', json={}).status_code, 401)
            self.assertEqual(client.post('/api/pack-downloads/preview?api_key=test', json=[]).status_code, 400)
            self.assertEqual(client.get('/api/pack-downloads?api_key=test').status_code, 200)

    def test_worker_downloads_zip_and_retains_archive_and_partial_failures(self):
        from io import BytesIO
        from unittest.mock import AsyncMock, MagicMock
        from backend.base.definitions import DownloadClientIdentifier
        from backend.implementations.download_clients.DDL import DDLDownload
        content = BytesIO()
        with ZipFile(content, 'w') as zipfile:
            zipfile.writestr('Example 001.cbz', b'comic bytes')
        body = content.getvalue()
        for ident, truncated in [('good', False), ('partial', True)]:
            destination = self.root / ident; destination.mkdir()
            self.cursor.execute("INSERT INTO pack_downloads VALUES(?,?,?,?,?,?,?, '',0,0)",
                                (ident, 'https://getcomics.org/weekly/', 'Pack', str(self.root), str(destination), ident, 'downloading'))
            self.db.commit()
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = {'Content-Length': str(len(body) + int(truncated))}
            response.iter_content.return_value = [body]
            client = Mock(); client._fetch_pure_link.return_value = response
            with patch.object(packs, '_purify_link', new=AsyncMock(return_value=('https://host.test/pack', DownloadClientIdentifier.DDL))), \
                    patch.object(packs.DownloadClients, 'get_client', return_value=DDLDownload), \
                    patch.object(DDLDownload, 'pack_client', return_value=client):
                packs._worker(ident, dict(service=GCDownloadService.GETCOMICS, link='https://host.test/pack'), destination)
            status = self.cursor.execute('SELECT status FROM pack_downloads WHERE id=?', (ident,)).fetchone()[0]
            self.assertEqual(status, 'held' if truncated else 'ready')
            if truncated:
                self.assertTrue((destination / 'payload.partial').exists())
                self.assertFalse((destination / 'ready').exists())
            else:
                self.assertEqual((destination / 'ready/Example 001.cbz').read_bytes(), b'comic bytes')
                self.assertEqual((destination / 'payload.archive').read_bytes(), body)

    def test_unfinished_pack_cannot_be_scanned(self):
        from backend.features import pack_inbox
        self.cursor.execute("INSERT INTO pack_downloads VALUES('held','https://getcomics.org/weekly/','Pack',?,?, 'held','held','',0,0)", (str(self.root), str(self.root)))
        self.db.commit()
        with patch.object(pack_inbox, 'Settings'):
            with self.assertRaises(InvalidKeyValue): pack_inbox.scan(str(self.root))
