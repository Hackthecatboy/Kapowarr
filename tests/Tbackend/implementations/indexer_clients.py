import sqlite3
import unittest
from unittest.mock import patch

from backend.base.custom_exceptions import CredentialInvalid, InvalidKeyValue
from backend.base.definitions import (DownloadType, GCDownloadService,
                                      IndexerClientField as ICF)
from backend.implementations.indexer_client_manager import (BaseIndexerClient,
                                                            IndexerClients)
from backend.implementations.indexer_clients.ddl.GetComics import \
    GetComicsIndexer
from backend.internals.db import (DB_SCHEMA, KapowarrCursor,
                                  setup_db_adapters_and_converters)
from backend.internals.db_migration import DatabaseMigrationHandler


class FixtureIndexer(BaseIndexerClient):
    """Exercise provider storage without requiring a running indexer."""

    client_type = 'Fixture'
    download_type = DownloadType.USENET
    required_tokens = (
        ICF.TITLE, ICF.ENABLED, ICF.URL, ICF.API_TOKEN, ICF.CATEGORIES
    )
    allow_multiple_instances = True

    @classmethod
    def test(cls, url, **extra_fields):
        if extra_fields.get('api_token') == 'rejected':
            raise CredentialInvalid

    async def search(self, query):
        raise NotImplementedError

    async def discover(self, last_check):
        raise NotImplementedError

    async def shutdown(self):
        pass


class IndexerStorage(unittest.TestCase):
    def setUp(self):
        setup_db_adapters_and_converters()
        self.db = sqlite3.connect(
            ':memory:', detect_types=sqlite3.PARSE_DECLTYPES)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        patcher = patch(
            'backend.implementations.indexer_client_manager.get_db',
            return_value=self.cursor
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        registry = patch.dict(IndexerClients.clients, {
            DownloadType.USENET: {'Fixture': FixtureIndexer},
            DownloadType.DDL: {'GetComics': GetComicsIndexer}
        })
        registry.start()
        self.addCleanup(registry.stop)
        self.settings = {
            'enabled': True,
            'title': 'Comics indexer',
            'url': 'https://indexer.example',
            'api_token': 'secret-fixture-value',
            'categories': [7030, 100001, 7030]
        }

    def add_indexer(self, **overrides):
        return IndexerClients.add(
            DownloadType.USENET, 'Fixture',
            **{**self.settings, **overrides}
        )

    def test_non_getcomics_add_edit_reload_and_delete(self):
        client = self.add_indexer()
        self.assertEqual(
            client.get_indexer_data()['categories'], [
                7030, 100001])
        client.update_indexer({
            **self.settings, 'title': 'Updated', 'api_token': 'replacement',
            'categories': [7030], 'enabled': False
        })
        saved = IndexerClients.get_client(client.id).get_indexer_data()
        self.assertEqual(saved, client.get_indexer_data())
        self.assertEqual(saved['title'], 'Updated')
        self.assertEqual(saved['api_token'], 'replacement')
        self.assertEqual(saved['categories'], [7030])
        self.assertFalse(saved['enabled'])
        self.assertIsNone(saved['gc_service_preference'])
        self.assertIsNone(saved['gc_avoid_large_downloads'])
        self.assertEqual(IndexerClients.get_all_data(), [saved])
        client.delete_indexer()
        self.assertEqual(IndexerClients.get_all_data(), [])

    def test_failed_connection_test_does_not_save_update(self):
        client = self.add_indexer()
        before = client.get_indexer_data()
        with self.assertRaises(CredentialInvalid):
            client.update_indexer({**self.settings, 'api_token': 'rejected'})
        self.assertEqual(client.get_indexer_data(), before)
        self.assertEqual(
            IndexerClients.get_client(
                client.id).get_indexer_data(),
            before)

    def test_categories_validate_before_contacting_provider(self):
        for categories in ('7030', None, [True], [0], [-1], [1.5], ['7030']):
            with self.subTest(categories=categories):
                with patch.object(FixtureIndexer, 'test') as probe:
                    with self.assertRaises(InvalidKeyValue):
                        self.add_indexer(categories=categories)
                    probe.assert_not_called()
        self.assertEqual(IndexerClients.get_all_data(), [])

    def test_empty_categories_and_api_key_are_supported(self):
        client = self.add_indexer(categories=[], api_token='')
        self.assertEqual(client.get_indexer_data()['categories'], [])
        self.assertEqual(client.get_indexer_data()['api_token'], '')

    def test_test_endpoint_does_not_require_display_fields(self):
        result = IndexerClients.test(
            DownloadType.USENET, 'Fixture', self.settings['url'],
            api_token='test', categories=[7030]
        )
        self.assertTrue(result['success'])
        self.assertEqual(IndexerClients.get_all_data(), [])

    def test_configuration_values_are_not_logged(self):
        with patch('backend.implementations.indexer_client_manager.LOGGER') as logger:
            client = self.add_indexer()
            client.update_indexer(self.settings)
        self.assertNotIn('secret-fixture-value', str(logger.mock_calls))

    def test_getcomics_settings_still_round_trip(self):
        preferences = [service.value for service in GCDownloadService]
        with patch.object(GetComicsIndexer, 'test'):
            client = IndexerClients.add(
                DownloadType.DDL, 'GetComics', True, 'GetComics',
                'https://getcomics.org', gc_service_preference=preferences,
                gc_avoid_large_downloads=False
            )
            data = client.get_indexer_data()
            data['gc_service_preference'] = list(reversed(preferences))
            data['gc_avoid_large_downloads'] = True
            client.update_indexer(data)
        saved = IndexerClients.get_client(client.id).get_indexer_data()
        self.assertEqual(
            list(
                saved['gc_service_preference']), list(
                reversed(preferences)))
        self.assertTrue(saved['gc_avoid_large_downloads'])
        self.assertEqual(client.get_indexer_data(), saved)
        self.assertIsNone(saved['api_token'])
        self.assertEqual(saved['categories'], [])


class IndexerMigration(unittest.TestCase):
    def test_existing_configuration_survives_migration(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('''CREATE TABLE indexer_clients(
                id INTEGER PRIMARY KEY, enabled BOOL, download_type INTEGER,
                client_type TEXT, title TEXT, url TEXT,
                gc_service_preference TEXT, gc_avoid_large_downloads BOOL
            )''')
            original = (7, 1, 1, 'GetComics', 'My source',
                        'https://getcomics.org', 'Mega,MediaFire', 0)
            db.execute(
                'INSERT INTO indexer_clients VALUES(?,?,?,?,?,?,?,?)',
                original)
            with patch('backend.internals.db_migration.get_db', return_value=db.cursor()):
                DatabaseMigrationHandler.handlers[51]()
            saved = db.execute('SELECT * FROM indexer_clients').fetchone()
            self.assertEqual(saved[:8], original)
            self.assertEqual(saved[8:], (None, '[]'))

    def test_tables_created_before_migration_are_supported(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript(DB_SCHEMA)
            with patch('backend.internals.db_migration.get_db', return_value=db.cursor()):
                DatabaseMigrationHandler.handlers[51]()
            columns = [row[1]
                       for row in db.execute(
                           'PRAGMA table_info(indexer_clients)')]
            self.assertEqual(columns.count('api_token'), 1)
            self.assertEqual(columns.count('categories'), 1)
