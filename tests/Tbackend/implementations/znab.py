# SPDX-License-Identifier: GPL-3.0
# Namespace/enclosure and capability-test scenarios adapted from
# silasfelinus/Kapowarr tests/Tbackend/torznab.py at 2a283b1d.
# Reworked for the shared v1.3.2 protocol layer on 2026-09-20.

import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import DownloadType
from backend.implementations.znab import (ZnabClient, parse_capabilities,
                                          parse_feed, parse_xml)

CAPS = b'''<caps><limits max="50"/>
<searching><search available="yes" supportedParams="q"/></searching>
<categories><category id="7000" name="Books">
<subcat id="7030" name="Comics"/></category></categories></caps>'''


def feed(items, metadata=''):
    return ('''<rss xmlns:z="http://torznab.com/schemas/2015/feed">
    <channel>''' + metadata + items + '</channel></rss>').encode()


ITEM = '''<item><title>Example Comic 001 (2026)</title>
<enclosure url="/download/1?apikey=fixture" length="500"/>
<z:attr name="size" value="1024"/><z:attr name="seeders" value="5"/>
<pubDate>Sun, 20 Sep 2026 10:00:00 +0000</pubDate></item>'''


class ZnabParsing(unittest.TestCase):
    def parse(
        self,
        content,
        protocol=DownloadType.TORRENT,
        offset=0,
        limit=100):
        return parse_feed(
            content,
            'https://indexer.example/1/api',
            protocol,
            offset,
            limit)

    def test_capabilities_and_nested_categories(self):
        caps = parse_capabilities(CAPS)
        self.assertTrue(caps.search_available)
        self.assertEqual(caps.max_limit, 50)
        self.assertEqual(caps.categories, {7000: 'Books', 7030: 'Comics'})

    def test_namespaced_attributes_and_relative_enclosures(self):
        release = self.parse(feed(ITEM)).releases[0]
        self.assertEqual(
            release.link,
            'https://indexer.example/download/1?apikey=fixture')
        self.assertEqual(release.size, 1024)
        self.assertEqual(release.seeders, 5)
        self.assertIsNotNone(release.published.tzinfo)

    def test_newznab_namespace_uses_same_parser(self):
        content = feed(ITEM).replace(
            b'http://torznab.com/schemas/2015/feed',
            b'http://www.newznab.com/DTD/2010/feeds/attributes/')
        self.assertEqual(
            self.parse(
                content,
                DownloadType.USENET).releases[0].size,
            1024)

    def test_invalid_size_falls_back_to_enclosure(self):
        page = self.parse(feed(ITEM.replace('value="1024"', 'value="unknown"')))
        self.assertEqual(page.releases[0].size, 500)

    def test_magnet_is_torrent_only(self):
        item = '<item><title>Example 1</title><z:attr name="magneturl" value="magnet:?xt=urn:btih:abc"/></item>'
        self.assertEqual(len(self.parse(feed(item)).releases), 1)
        self.assertEqual(
            self.parse(
                feed(item),
                DownloadType.USENET).releases,
            [])

    def test_duplicate_or_unsafe_results_are_skipped(self):
        unsafe = '<item><title>Unsafe</title><link>file:///etc/passwd</link></item>'
        self.assertEqual(
            len(self.parse(feed(ITEM + ITEM + unsafe)).releases),
            1)

    def test_pagination_uses_raw_count_before_deduplication(self):
        page = self.parse(
            feed(
                ITEM + ITEM,
                '<z:response offset="0" total="3"/>'))
        self.assertEqual(len(page.releases), 1)
        self.assertTrue(page.next_page_available)

    def test_last_and_empty_pages_stop(self):
        self.assertFalse(
            self.parse(
                feed(
                    ITEM,
                    '<z:response offset="2" total="3"/>'),
                offset=2).next_page_available)
        self.assertFalse(
            self.parse(
                feed(
                    '',
                    '<z:response offset="0" total="100"/>')).next_page_available)

    def test_wrong_offset_is_an_error(self):
        with self.assertRaises(ClientNotWorking):
            self.parse(
                feed(
                    ITEM,
                    '<z:response offset="0" total="100"/>'),
                offset=50)

    def test_authentication_error_xml_is_not_an_empty_feed(self):
        with self.assertRaises(CredentialInvalid):
            self.parse(b'<error code="100" description="Incorrect key"/>')

    def test_bad_xml_and_api_errors_are_not_empty_feeds(self):
        for document in (
            b'<error code="500"/>',
            b'<html/>',
            b'<rss/>',
                b'broken'):
            with self.subTest(document=document), self.assertRaises(ClientNotWorking):
                self.parse(document)

    def test_dtd_is_rejected(self):
        for document in (
            b'<!DOCTYPE rss><rss/>',
                '<!DOCTYPE rss><rss/>'.encode('utf-16')):
            with self.assertRaises(ClientNotWorking):
                parse_xml(document)


class ZnabHTTP(unittest.TestCase):
    """Exercise real HTTP against a local fixture, without external services."""

    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls.response = CAPS
        cls.status = 200

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.calls.append(self.path)
                self.send_response(cls.status)
                self.end_headers()
                self.wfile.write(cls.response)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        type(self).calls.clear()
        type(self).status = 200
        type(self).response = CAPS
        self.client = ZnabClient(
            'http://127.0.0.1:{}/17/api'.format(self.server.server_port),
            'fixture-key', DownloadType.TORRENT)
        self.addCleanup(self.client.close)
        self.client.session.trust_env = False

    def test_caps_request_preserves_prowlarr_path(self):
        self.assertTrue(self.client.test().search_available)
        url = urlsplit(self.calls[-1])
        self.assertEqual(url.path, '/17/api')
        self.assertEqual(
            parse_qs(
                url.query), {
                't': ['caps'], 'apikey': ['fixture-key']})

    def test_search_passes_query_categories_and_pagination(self):
        type(self).response = feed(ITEM, '<z:response offset="50" total="51"/>')
        self.client.search(
            'Example #1', [7030, 7030, 100001],
            offset=50, limit=50)
        params = parse_qs(urlsplit(self.calls[-1]).query)
        self.assertEqual(params['q'], ['Example #1'])
        self.assertEqual(params['cat'], ['7030,100001'])
        self.assertEqual(params['offset'], ['50'])
        self.assertEqual(params['limit'], ['50'])

    def test_recent_feed_omits_query_and_optional_categories(self):
        type(self).response = feed('')
        self.client.search()
        params = parse_qs(urlsplit(self.calls[-1]).query)
        self.assertNotIn('q', params)
        self.assertNotIn('cat', params)

    def test_http_authentication_errors_propagate(self):
        for status in (401, 403):
            type(self).status = status
            with self.assertRaises(CredentialInvalid):
                self.client.test()

    def test_server_failure_is_not_reported_as_success(self):
        type(self).status = 503
        with self.assertRaises(ClientNotWorking):
            self.client.test()

    def test_disabled_search_and_non_indexer_responses_fail(self):
        for document in (
            b'{}',
            b'<caps/>',
            CAPS.replace(
                b'available="yes"',
                b'available="no"')):
            type(self).response = document
            with self.assertRaises(ClientNotWorking):
                self.client.test()
