"""Provider/API integration and search-only download boundaries."""

import asyncio
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from Tbackend.implementations.znab import CAPS, ITEM, feed

from backend.base.custom_exceptions import (CredentialInvalid,
                                            EnqueuingDownloadFailure)
from backend.base.definitions import (DownloadType, QueryKeys,
                                      SearchAction, SpecialVersion)
from backend.features.download_queue import DownloadHandler
from backend.features.search_discover import _get_all_new_releases
from backend.features.search_full import SearchCoordinator, choose_downloads
from backend.implementations.indexer_client_manager import IndexerClients
from backend.implementations.query_builder_manager import QueryBuilders
from backend.implementations.znab import ZnabClient
from backend.internals.db import (DB_SCHEMA, KapowarrCursor,
                                  setup_db_adapters_and_converters)
from frontend.api import api


class ZnabIntegration(unittest.TestCase):
    def setUp(self):
        IndexerClients.trigger_client_registration()
        QueryBuilders.trigger_builder_registration()
        setup_db_adapters_and_converters()
        self.db = sqlite3.connect(':memory:', detect_types=sqlite3.PARSE_DECLTYPES)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DB_SCHEMA)
        cursor = self.db.cursor(factory=KapowarrCursor)
        self.start_patch('backend.implementations.indexer_client_manager.get_db', return_value=cursor)
        self.start_patch('backend.implementations.release_store.get_db', return_value=cursor)
        settings = self.start_patch('frontend.api.Settings')
        settings.return_value.sv.api_key = 'kapowarr-key'
        self.start_patch('frontend.api.StartTypeHandlers.diffuse_timer')
        app = Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        self.http = app.test_client()
        self.transport = self._start(patch.object(ZnabClient, '_request', side_effect=self.response))
        self.payload = dict(download_type=3, client_type='Newznab', enabled=True,
                            title='Comics', url='https://indexer.example/1/api',
                            api_token='indexer-secret', categories=[7030])

    def start_patch(self, *args, **kwargs):
        return self._start(patch(*args, **kwargs))

    def _start(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def response(params):
        return CAPS if params['t'] == 'caps' else feed(ITEM)

    def request(self, method, path, **kwargs):
        return self.http.open('/api' + path, method=method,
                              query_string={'api_key': 'kapowarr-key'}, **kwargs)

    def add(self, protocol=DownloadType.USENET):
        data = {**self.payload, 'download_type': protocol.value,
                'client_type': 'Newznab' if protocol == DownloadType.USENET else 'Torznab'}
        response = self.request('POST', '/indexers', json=data)
        self.assertEqual(response.status_code, 201, response.json)
        return IndexerClients.get_client(response.json['result']['id'])

    def test_authenticated_settings_round_trip_for_both_protocols(self):
        options = self.request('GET', '/indexers/options').json['result']
        self.assertIn('Torznab', options['2'])
        self.assertIn('Newznab', options['3'])
        for protocol in (DownloadType.TORRENT, DownloadType.USENET):
            client = self.add(protocol)
            data = client.get_indexer_data()
            data.update(title='Updated', categories=[7030, 100001], api_token='replacement')
            response = self.request('PUT', '/indexers/' + str(client.id), json=data)
            self.assertEqual(response.status_code, 200, response.json)
            saved = self.request('GET', '/indexers/' + str(client.id)).json['result']
            self.assertEqual(saved['api_token'], 'replacement')
            self.assertEqual(saved['categories'], [7030, 100001])
            self.assertEqual(saved['title'], 'Updated')
            self.assertEqual(self.request('DELETE', '/indexers/' + str(client.id)).status_code, 200)
        self.assertEqual(self.request('GET', '/indexers').json['result'], [])

    def test_routes_require_authentication_and_do_not_expose_saved_token(self):
        client = self.add()
        for method, path in [('GET', '/indexers'), ('POST', '/indexers'),
                             ('GET', '/indexers/options'), ('POST', '/indexers/test'),
                             ('GET', '/indexers/' + str(client.id)),
                             ('PUT', '/indexers/' + str(client.id)),
                             ('DELETE', '/indexers/' + str(client.id))]:
            with self.subTest(method=method, path=path):
                response = self.http.open('/api' + path, method=method, json=self.payload)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('indexer-secret', response.get_data(as_text=True))

    def test_connection_failure_cannot_save_configuration(self):
        self.transport.side_effect = CredentialInvalid
        result = self.request('POST', '/indexers/test', json=self.payload)
        self.assertFalse(result.json['result']['success'])
        self.assertEqual(self.request('POST', '/indexers', json=self.payload).status_code, 400)
        self.assertEqual(IndexerClients.get_all_data(), [])

    def test_registered_queries_return_comic_results_with_search_only_capability(self):
        for protocol in (DownloadType.TORRENT, DownloadType.USENET):
            client = self.add(protocol)
            builder = QueryBuilders.get_builder(protocol)()
            query = builder.next_query(SearchAction.SEARCH_ISSUE, QueryKeys(
                ['Example Comic'], 2026, 1, SpecialVersion.NORMAL, '1'))
            result = asyncio.run(client.search(query))
            release = result.results[0]
            self.assertEqual(release['series'], 'Example Comic')
            self.assertEqual(release['issue_number'], 1.0)
            self.assertEqual(release['indexer_id'], client.id)
            self.assertTrue(release['download_supported'])
            params = self.transport.call_args.args[0]
            self.assertEqual(params['q'], query['query'])
            self.assertEqual(params['cat'], '7030')
            self.assertEqual(params['limit'], '50')
            self.assertEqual(params['offset'], '0')

    def test_coordinator_matches_results_and_handles_multiple_exhausted_indexers(self):
        self.add(DownloadType.TORRENT)
        self.add(DownloadType.USENET)
        issue = SimpleNamespace(id=1, calculated_issue_number=1.0,
                                issue_number='1', date='2026-09-20')
        data = SimpleNamespace(title='Example Comic', alt_title=None, year=2026,
                               volume_number=1, special_version=SpecialVersion.NORMAL)
        with patch('backend.features.search_full.Volume') as volume, \
                patch('backend.implementations.matching.blocklist_contains', return_value=False):
            volume.return_value.get_data.return_value = data
            volume.return_value.get_issues.return_value = [issue]
            results = asyncio.run(SearchCoordinator(1, [1]).search())
            self.assertEqual(len(results), 1)  # duplicate links across providers
            self.assertTrue(results[0]['match'])
            self.assertTrue(results[0]['download_supported'])
            self.transport.side_effect = lambda params: CAPS if params['t'] == 'caps' else feed('')
            self.assertEqual(asyncio.run(SearchCoordinator(1, [1]).search()), [])

    def test_all_saved_indexers_are_loaded_and_searched(self):
        torrent = self.add(DownloadType.TORRENT)
        usenet = self.add(DownloadType.USENET)
        clients = IndexerClients.get_all_clients()
        self.assertEqual([client.id for client in clients], [torrent.id, usenet.id])
        issue = SimpleNamespace(id=1, calculated_issue_number=1.0,
                                issue_number='1', date='2026-09-20')
        data = SimpleNamespace(title='Example Comic', alt_title=None, year=2026,
                               volume_number=1, special_version=SpecialVersion.NORMAL)
        self.transport.reset_mock()
        with patch('backend.features.search_full.Volume') as volume, \
                patch('backend.implementations.matching.blocklist_contains', return_value=False):
            volume.return_value.get_data.return_value = data
            volume.return_value.get_issues.return_value = [issue]
            coordinator = SearchCoordinator(1, [1])
            self.assertEqual(len(coordinator.indexers), 2)
            asyncio.run(coordinator.search())
        searches = [call.args[0] for call in self.transport.call_args_list
                    if call.args[0]['t'] == 'search']
        self.assertEqual(len(searches), 2)

    def test_one_indexer_match_does_not_stop_another_indexers_fallback(self):
        from backend.base.definitions import QueryResult
        from unittest.mock import AsyncMock

        early = self.add(DownloadType.TORRENT)
        later = self.add(DownloadType.USENET)
        issue = SimpleNamespace(id=1, calculated_issue_number=1.0,
                                issue_number='1', date='2026-09-20')
        data = SimpleNamespace(title='Example Comic', alt_title=None, year=2026,
                               volume_number=1, special_version=SpecialVersion.NORMAL)
        early_result = asyncio.run(early.search(dict(query='Example', page=1))).results[0]
        later_result = {**early_result, 'link': 'https://indexer.example/later',
                        'indexer_id': later.id, 'indexer_title': later.title}
        # The second indexer needs the final title-only variation.
        for downloadable_only in (False, True):
            with self.subTest(downloadable_only=downloadable_only), \
                    patch('backend.features.search_full.Volume') as volume, \
                    patch('backend.features.search_full.IndexerClients.get_all_clients', return_value=[early, later]), \
                    patch('backend.implementations.matching.blocklist_contains', return_value=False), \
                    patch.object(early, 'search', new=AsyncMock(return_value=QueryResult([early_result], False))) as early_search, \
                    patch.object(later, 'search', new=AsyncMock(side_effect=[
                        QueryResult([], False), QueryResult([], False),
                        QueryResult([later_result], False)])) as later_search:
                volume.return_value.get_data.return_value = data
                volume.return_value.get_issues.return_value = [issue]
                wanted = [1]
                results = asyncio.run(SearchCoordinator(1, wanted, downloadable_only).search())
                self.assertEqual({r['indexer_id'] for r in results}, {early.id, later.id})
                self.assertTrue(all(r['match'] for r in results))
                self.assertEqual(early_search.await_count, 1)
                self.assertEqual(later_search.await_count, 3)
                self.assertEqual(later_search.call_args.args[0]['query'], 'Example Comic')
                self.assertEqual(wanted, [1])

    def test_torznab_search_metadata_routes_to_torrent_prepper(self):
        from backend.implementations.download_prepper_manager import \
            DownloadPreppers
        from backend.implementations.release_store import get_release
        client = self.add(DownloadType.TORRENT)
        release = asyncio.run(client.search(dict(query='Example', page=1))).results[0]
        self.assertEqual(get_release(client.id, release['link'])['display_title'], release['display_title'])
        DownloadPreppers.trigger_prepper_registration()
        prepper = DownloadPreppers.get_prepper(DownloadType.TORRENT, 'Torznab')
        volume = Mock()
        volume.get_data.return_value = SimpleNamespace(title='Example Comic', alt_title=None, year=2026, volume_number=1, special_version=SpecialVersion.NORMAL)
        volume.get_issues.return_value = [SimpleNamespace(id=1, calculated_issue_number=1.0, date='2026-01-01')]
        with patch('backend.implementations.download_preppers.usenet.Newznab.Volume', return_value=volume), \
                patch('backend.implementations.matching.blocklist_contains', return_value=False), \
                patch('backend.implementations.download_preppers.torrent.Torznab.TorrentDownload') as download:
            prepper(release['link'], client.id, 1, 1).get_downloads()
            self.assertEqual(download.call_args.args[0], release['link'])
            self.assertEqual(download.call_args.args[2], 1.0)

    def test_provider_pages_use_capability_limit_and_failure_is_contained(self):
        client = self.add()
        asyncio.run(client.search(dict(query='Example', page=2)))
        self.assertEqual(self.transport.call_args.args[0]['offset'], '50')
        self.transport.side_effect = CredentialInvalid
        with patch('backend.implementations.znab_indexer.LOGGER') as logger:
            result = asyncio.run(client.search(dict(query='Example', page=1)))
        self.assertEqual(result.results, [])
        self.assertNotIn('indexer-secret', str(logger.mock_calls))

    def test_automatic_search_and_discovery_exclude_search_only_providers(self):
        client = self.add(DownloadType.TORRENT)
        self._start(patch.object(type(client), 'supports_downloads', False))
        with patch('backend.features.search_full.Volume') as volume:
            volume.return_value.get_issues.return_value = []
            with patch('backend.features.search_full.SearchActionPlanner'):
                self.assertEqual(len(SearchCoordinator(1, []).indexers), 1)
                self.assertEqual(SearchCoordinator(1, [], downloadable_only=True).indexers, [])
        with patch('backend.features.search_discover.Settings') as settings:
            settings.return_value.sv.last_rss_sync = 0
            self.transport.reset_mock()
            self.assertEqual(asyncio.run(_get_all_new_releases()), [])
            self.transport.assert_not_called()
        release = asyncio.run(client.search(dict(query='Example', page=1))).results[0]
        self.assertEqual(choose_downloads([release], [(1, 1.0)], []), [])

    def test_discovery_filters_old_dates_and_includes_undated_releases(self):
        client = self.add()
        old = ITEM.replace('20 Sep 2026', '13 Sep 2026').replace('/download/1', '/download/old')
        undated = ITEM.replace('<pubDate>Sun, 20 Sep 2026 10:00:00 +0000</pubDate>', '').replace('/download/1', '/download/undated')
        self.transport.side_effect = lambda params: CAPS if params['t'] == 'caps' else feed(ITEM + old + undated)
        results = asyncio.run(client.discover(datetime(2026, 9, 19, tzinfo=timezone.utc)))
        self.assertEqual(len(results), 2)
        self.assertFalse(any('/old' in result['link'] for result in results))
        self.assertNotIn('q', self.transport.call_args.args[0])

    def test_direct_enqueue_rejects_search_only_without_prepper_or_blocklisting(self):
        client = self.add(DownloadType.TORRENT)
        self._start(patch.object(type(client), 'supports_downloads', False))
        handler = object.__new__(DownloadHandler)
        handler.link_in_queue = Mock(return_value=False)
        with patch('backend.features.download_queue.DownloadPreppers.get_prepper') as prepper, \
                patch('backend.features.download_queue.add_to_blocklist') as blocklist, \
                patch('backend.features.download_queue.LOGGER') as logger:
            with self.assertRaises(EnqueuingDownloadFailure):
                handler.add('https://indexer.example/get?apikey=indexer-secret', client.id, 1)
            prepper.assert_not_called()
            blocklist.assert_not_called()
            self.assertNotIn('indexer-secret', str(logger.mock_calls))
