"""Queue admission rejects repeated identities before creating client jobs."""

import base64
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.features.download_queue import DownloadHandler
from backend.implementations.download_clients.Torrent import TorrentDownload
from backend.implementations.managed_job import JobNeedsReview
from backend.implementations.torrent_support import magnet_payload

HASH = '0123456789abcdef0123456789abcdef01234567'
MAGNET = 'magnet:?xt=urn:btih:' + HASH
MODULE = 'backend.features.download_queue.'


def torrent(link, external_id=None):
    result = object.__new__(TorrentDownload)
    result._download_link = result._web_link = link
    result._external_id = external_id
    return result


class QueueDeduplication(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.handler = object.__new__(DownloadHandler)
        self.handler.queue = []
        self.handler._process_queue = Mock()
        self.stack.enter_context(patch(MODULE + 'get_db'))
        self.stack.enter_context(patch(MODULE + 'WebSocket'))
        self.stack.enter_context(patch(MODULE + 'IndexerClients.get_client'))
        prepper = self.stack.enter_context(patch(MODULE + 'DownloadPreppers.get_prepper'))
        self.downloads = prepper.return_value.return_value.get_downloads
        self.prepare = self.stack.enter_context(patch.object(
            self.handler, '_DownloadHandler__prepare_downloads_for_queue', return_value=[]))

    def test_changed_magnet_labels_trackers_and_encoding_do_not_add_rows(self):
        encoded = base64.b32encode(bytes.fromhex(HASH)).decode()
        self.handler.queue = [torrent('magnet:?xt=urn:btih:' + encoded)]
        self.downloads.return_value = [torrent(MAGNET + '&dn=new&tr=https://tracker.test')]
        self.assertEqual(self.handler.add('https://indexer.test/new-url', 1, 1), [])
        self.assertEqual(self.prepare.call_args.args[0], [])

    def test_http_mirror_matches_restored_external_hash(self):
        self.handler.queue = [torrent('https://indexer.test/old-url', HASH.upper())]
        self.downloads.return_value = [torrent('https://indexer.test/new-url')]
        with patch(MODULE + 'resolve_torrent', return_value=magnet_payload(MAGNET)):
            self.handler.add('https://indexer.test/new-url', 1, 1)
        self.assertEqual(self.prepare.call_args.args[0], [])

    def test_rejected_job_cached_identity_still_blocks_repeats(self):
        existing = torrent('https://indexer.test/first')
        existing.payload = magnet_payload(MAGNET)
        self.handler.queue = [existing]
        self.downloads.return_value = [torrent(MAGNET)]
        self.handler.add(MAGNET, 1, 1)
        self.assertEqual(self.prepare.call_args.args[0], [])

    def test_distinct_hashes_are_not_discarded(self):
        self.handler.queue = [torrent(MAGNET)]
        other = torrent('magnet:?xt=urn:btih:' + 'a' * 40)
        self.downloads.return_value = [other]
        self.handler.add(other.download_link, 1, 1)
        self.assertEqual(self.prepare.call_args.args[0], [other])

    def test_one_batch_filters_duplicate_hashes(self):
        first = torrent(MAGNET)
        self.downloads.return_value = [first, torrent(MAGNET + '&dn=alias')]
        self.handler.add('https://indexer.test/bundle', 1, 1)
        self.assertEqual(self.prepare.call_args.args[0], [first])

    def test_unavailable_metadata_keeps_worker_review_handling(self):
        download = torrent('https://indexer.test/unavailable')
        self.downloads.return_value = [download]
        with patch(MODULE + 'resolve_torrent', side_effect=JobNeedsReview('Unavailable')):
            self.handler.add(download.download_link, 1, 1)
        self.assertEqual(self.prepare.call_args.args[0], [download])

    def test_concurrent_requests_register_same_link_only_once(self):
        download = SimpleNamespace(download_link='https://indexer.test/file',
                                   web_link='https://indexer.test/file',
                                   as_dict=lambda: {'id': 1})
        def prepare(*args, **kwargs):
            sleep(0.02)  # Both callers reach admission while preparation is slow.
            return [download]
        self.prepare.side_effect = prepare
        self.downloads.return_value = [download]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.handler.add(download.web_link, 1, 1), range(2)))
        self.assertEqual(sorted(map(len, results)), [0, 1])
        self.assertEqual(len(self.handler.queue), 1)
        self.prepare.assert_called_once()
