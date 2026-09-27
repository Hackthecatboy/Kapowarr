"""Category discovery is observational; completed payload review is explicit."""

import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import DownloadState as DS
from backend.features import torrent_discovery as discovery
from backend.features.download_queue import DownloadHandler
from backend.implementations.download_clients.Torrent import TorrentDownload
from frontend.api import api

HASH = 'a' * 40
MODULE = 'backend.features.torrent_discovery.'


class TorrentDiscovery(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        discovery._CACHE.clear()
        self.addCleanup(discovery._CACHE.clear)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        self.clients = stack.enter_context(patch(MODULE + 'ExternalClients.get_clients', return_value=[
            dict(id=1, title='qbit', client_type='qBittorrent', enabled=True)]))
        self.client = Mock()
        stack.enter_context(patch(MODULE + 'ExternalClients.get_client', return_value=self.client))
        self.scan = stack.enter_context(patch(MODULE + 'pack_inbox.scan', return_value={'items': []}))
        stack.enter_context(patch(MODULE + 'Settings', return_value=SimpleNamespace(
            sv=SimpleNamespace(download_folder=str(self.root)))))
        stack.enter_context(patch(MODULE + 'RemoteMappings.remote_to_local', side_effect=lambda _, path: path))
        self.folders = stack.enter_context(patch(MODULE + 'PackDownloadsDB.folders', return_value=[]))
        self.info = dict(id=HASH, title='Collection', state=DS.SEEDING_STATE,
                         category='kapowarr', size=100, speed=2, progress=100,
                         storage=str(self.root / 'collection'))
        self.client.get_category_downloads.return_value = [self.info]
        self.client.get_download.return_value = self.info
        self.handler = object.__new__(DownloadHandler)
        self.handler.queue = []

    def test_listing_is_cached_stable_and_never_imports_or_submits(self):
        first = discovery.listing(self.handler)
        second = discovery.listing(self.handler)
        self.assertEqual(first, second)
        self.assertEqual(first['downloads'][0]['id'], 'discovered-1-' + HASH)
        self.assertTrue(first['downloads'][0]['can_review'])
        self.client.get_category_downloads.assert_called_once()
        self.scan.assert_not_called()
        self.client.add_torrent.assert_not_called()
        self.client.delete_download.assert_not_called()

    def test_tracked_hash_is_hidden_only_for_same_client(self):
        job = object.__new__(TorrentDownload)
        job._external_id = HASH
        job.external_client = SimpleNamespace(id=1)
        self.handler.queue = [job]
        self.assertEqual(discovery.listing(self.handler)['downloads'], [])
        job.external_client = SimpleNamespace(id=2)
        self.assertEqual(len(discovery.listing(self.handler)['downloads']), 1)

    def test_disabled_clients_are_not_polled(self):
        self.clients.return_value[0]['enabled'] = False
        self.assertEqual(discovery.listing(self.handler)['downloads'], [])
        self.client.get_category_downloads.assert_not_called()

    def test_failure_retains_snapshot_but_disables_review(self):
        discovery.listing(self.handler)
        discovery._CACHE[1] = (float('-inf'), [self.info], '')
        self.client.get_category_downloads.side_effect = OSError('offline')
        result = discovery.listing(self.handler)
        self.assertTrue(result['errors'])
        self.assertFalse(result['downloads'][0]['can_review'])

    def test_directory_and_single_file_review_scan_only_selected_content(self):
        directory = Path(self.info['storage'])
        directory.mkdir()
        discovery.review(1, HASH)
        self.scan.assert_called_with(str(directory), filename=None)
        file = self.root / 'Comic.cbz'
        file.write_bytes(b'comic')
        self.info['storage'] = str(file)
        discovery.review(1, HASH)
        self.scan.assert_called_with(str(self.root), filename='Comic.cbz')
        self.assertTrue(file.exists())
        self.client.delete_download.assert_not_called()

    def test_review_rechecks_category_completion_and_path_boundaries(self):
        file = self.root / 'Comic.cbz'
        file.write_bytes(b'comic')
        self.info['storage'] = str(file)
        for change in (dict(category='other'), dict(state=DS.DOWNLOADING_STATE),
                       dict(storage=str(self.root)), dict(storage=str(self.root.parent)),
                       dict(storage=str(self.root / 'missing'))):
            self.client.get_download.return_value = dict(self.info, **change)
            with self.assertRaises(InvalidKeyValue):
                discovery.review(1, HASH)
        self.client.get_download.return_value = self.info
        link = self.root / 'link.cbz'
        link.symlink_to(file)
        self.info['storage'] = str(link)
        with self.assertRaises(InvalidKeyValue):
            discovery.review(1, HASH)
        self.scan.assert_not_called()

    def test_managed_pack_overlap_is_rejected_to_preserve_seeding_sources(self):
        folder = Path(self.info['storage'])
        folder.mkdir()
        self.folders.return_value = [str(folder)]
        with self.assertRaises(InvalidKeyValue):
            discovery.review(1, HASH)
        self.scan.assert_not_called()

    def test_discovery_endpoints_require_authentication(self):
        app = Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        client = app.test_client()
        with patch('frontend.api.Settings', return_value=SimpleNamespace(sv=SimpleNamespace(api_key='secret'))):
            self.assertIn(client.get('/api/activity/queue/discovered').status_code, (400, 401))
            self.assertIn(client.post('/api/activity/queue/discovered/review', json={}).status_code, (400, 401))
        self.scan.assert_not_called()
