"""Prowlarr import ownership, failure boundaries and authenticated API tests."""

import json
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask
from requests import ConnectionError

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid, InvalidKeyValue
from backend.implementations import prowlarr
from backend.internals.db import DB_SCHEMA, KapowarrCursor
from backend.internals.db_migration import DatabaseMigrationHandler
from frontend.api import api


class ProwlarrImport(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        self.addCleanup(self.db.close)
        self.start_patch('backend.implementations.prowlarr.get_db', return_value=self.cursor)
        self.config = dict(url='http://prowlarr:9696/prefix/', api_token='fixture-secret', categories=[7030])
        self.entries = [dict(id=1, name='Comics NZB', enable=True, protocol='usenet'),
                        dict(id=2, name='Comics Torrent', enable=True, protocol='torrent')]
        self.session = self.start_patch('backend.implementations.prowlarr.Session')
        self.response = MagicMock(status_code=200)
        self.session.return_value.__enter__.return_value.get.return_value.__enter__.return_value = self.response
        self.response.iter_content.side_effect = lambda size: [json.dumps(self.entries).encode()]

    def start_patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def sync(self, ids=(1, 2), **changes):
        return prowlarr.synchronize({**self.config, 'ids': list(ids), **changes})

    def rows(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM indexer_clients ORDER BY id')]

    def test_preview_is_read_only_and_uses_authenticated_base_path(self):
        entries = prowlarr.preview(self.config)
        self.assertEqual(len(entries), 2)
        self.assertFalse(entries[0]['managed'])
        self.assertEqual(self.rows(), [])
        self.assertIsNone(prowlarr.configuration())
        self.session.return_value.__enter__.return_value.get.assert_called_once_with(
            'http://prowlarr:9696/prefix/api/v1/indexer',
            headers={'X-Api-Key': 'fixture-secret'}, timeout=(10, 30), allow_redirects=False, stream=True)

    def test_refresh_preserves_ids_and_updates_owned_fields(self):
        self.assertEqual(self.sync()['created'], 2)
        before = self.rows()
        self.entries[0]['name'] = 'Renamed'
        result = self.sync(api_token='rotated-fixture', categories=[7030, 100001])
        after = self.rows()
        self.assertEqual(result['updated'], 2)
        self.assertEqual([r['id'] for r in before], [r['id'] for r in after])
        self.assertEqual(after[0]['title'], 'Prowlarr: Renamed')
        self.assertEqual(after[0]['api_token'], 'rotated-fixture')
        self.assertEqual(json.loads(after[0]['categories']), [7030, 100001])
        self.assertEqual(after[0]['url'], 'http://prowlarr:9696/prefix/1/api')
        self.assertEqual([r['client_type'] for r in after], ['Newznab', 'Torznab'])
        self.assertNotIn('rotated-fixture', json.dumps(prowlarr.public_configuration()))
        self.assertTrue(prowlarr.public_configuration()['has_api_key'])
        self.assertEqual(self.sync(api_token='')['updated'], 2)

    def test_manual_duplicate_is_never_adopted_or_changed(self):
        self.db.execute("INSERT INTO indexer_clients(title,download_type,client_type,url,api_token,categories) VALUES('Manual',3,'Newznab','http://PROWLARR:9696/prefix/1/api/','manual-secret','[]')")
        before = self.rows()[0]
        self.assertTrue(prowlarr.preview(self.config)[0]['duplicate'])
        self.assertEqual(self.sync()['skipped'], 1)
        self.assertEqual(self.rows()[0], before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM prowlarr_indexers').fetchone()[0], 1)
        self.entries = []
        self.sync(ids=[])
        self.assertEqual(self.rows()[0], before)
        self.assertFalse(self.rows()[1]['enabled'])

    def test_missing_and_disabled_entries_retained_and_reenabled(self):
        self.sync()
        ids = [r['id'] for r in self.rows()]
        self.entries = [self.entries[0]]
        self.entries[0]['enable'] = False
        self.assertEqual(self.sync(ids=[])['disabled'], 2)
        self.assertEqual([r['id'] for r in self.rows()], ids)
        self.entries[0]['enable'] = True
        self.sync(ids=[1])
        self.assertTrue(self.rows()[0]['enabled'])
        self.assertFalse(self.rows()[1]['enabled'])

    def test_unselected_managed_settings_are_preserved(self):
        self.sync()
        self.db.execute("UPDATE indexer_clients SET categories='[]', title='Local override' WHERE id=2")
        before = self.rows()[1]
        self.sync(ids=[1])
        self.assertEqual(self.rows()[1], before)

    def test_failed_fetch_or_malformed_snapshot_never_disables_saved_indexers(self):
        self.sync()
        before = self.rows()
        for content in [b'not json', b'{}', b'[{}]', json.dumps([self.entries[0], self.entries[0]]).encode(), b'x' * (4 * 1024 * 1024 + 1)]:
            self.response.iter_content.side_effect = lambda size, content=content: [content]
            with self.assertRaises(ClientNotWorking):
                self.sync()
            self.assertEqual(self.rows(), before)
        for code, exception in [(401, CredentialInvalid), (403, CredentialInvalid), (302, ClientNotWorking), (500, ClientNotWorking)]:
            self.response.status_code = code
            with self.assertRaises(exception):
                self.sync()
            self.assertEqual(self.rows(), before)
        self.session.return_value.__enter__.return_value.get.side_effect = ConnectionError
        with self.assertRaises(ClientNotWorking):
            self.sync()

    def test_validation_and_connection_identity(self):
        for changes in [dict(url='http://user:secret@host'), dict(url='http://host?apikey=secret'),
                        dict(url='file:///tmp/file'), dict(categories=[True]), dict(api_token='a\nb')]:
            with self.subTest(changes=changes), self.assertRaises(InvalidKeyValue):
                self.sync(**changes)
        self.sync()
        with self.assertRaises(InvalidKeyValue):
            self.sync(url='http://another-prowlarr')
        with self.assertRaises(InvalidKeyValue):
            self.sync(ids=[999])
        self.assertEqual(len(self.rows()), 2)

    def test_atomic_rollback_and_deletion_cascade(self):
        self.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON indexer_clients WHEN NEW.client_type='Torznab' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.sync()
        self.assertEqual(self.rows(), [])
        self.assertIsNone(prowlarr.configuration())
        self.assertEqual(self.db.execute('SELECT count(*) FROM prowlarr_indexers').fetchone()[0], 0)
        self.db.execute('DROP TRIGGER fail_second')
        self.sync()
        self.db.execute('DELETE FROM indexer_clients WHERE id=1')
        self.assertEqual(self.db.execute('SELECT count(*) FROM prowlarr_indexers').fetchone()[0], 1)
        self.assertEqual(self.sync()['created'], 1)

    def test_migration_preserves_existing_indexers(self):
        self.db.execute('DROP TABLE prowlarr_indexers')
        self.db.execute('DROP TABLE prowlarr_connection')
        self.db.execute("INSERT INTO indexer_clients(title,download_type,client_type,url) VALUES('Manual',3,'Newznab','http://host/api')")
        before = self.rows()
        with patch('backend.internals.db_migration.get_db', return_value=self.cursor):
            DatabaseMigrationHandler.handlers[54]()
            DatabaseMigrationHandler.handlers[54]()
        self.assertEqual(self.rows(), before)
        self.assertIsNone(prowlarr.configuration())

    def test_api_authentication_and_secret_free_preview(self):
        app = Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        client = app.test_client()
        settings = self.start_patch('frontend.api.Settings')
        settings.return_value.sv.api_key = 'test-api-key'
        self.start_patch('frontend.api.StartTypeHandlers.diffuse_timer')
        for method, path in [('GET','/prowlarr'), ('POST','/prowlarr/preview'), ('POST','/prowlarr/sync')]:
            self.assertEqual(client.open('/api'+path, method=method, json=self.config).status_code, 401)
        self.session.assert_not_called()
        for path in ['/prowlarr/preview', '/prowlarr/sync']:
            response = client.post('/api'+path+'?api_key=test-api-key', json={**self.config, 'ids':[1]})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('fixture-secret', response.get_data(as_text=True))
        response = client.get('/api/prowlarr?api_key=test-api-key')
        self.assertNotIn('fixture-secret', response.get_data(as_text=True))
