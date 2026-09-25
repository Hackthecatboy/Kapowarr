"""Bounded log reads and authentication for the in-app viewer."""

import logging
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from flask import Flask

from backend.base.logging import (
    ErrorColorFormatter, RedactingFormatter, get_log_file_contents,
    get_recent_logs, redact_log_text,
)
from frontend.api import api


class LogViewer(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'Kapowarr.log'
        patcher = patch('backend.base.logging.get_log_filepath', return_value=str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rotation_is_chronological_and_missing_files_are_empty(self):
        self.assertEqual(get_recent_logs(), ('', False))
        self.path.with_suffix('.log.1').write_text('older\n', encoding='utf-8')
        self.path.write_text('newer\n', encoding='utf-8')
        self.assertEqual(get_recent_logs(), ('older\nnewer', False))

    def test_only_newest_thousand_lines_are_returned(self):
        self.path.write_text(''.join(f'line {n}\n' for n in range(1200)), encoding='utf-8')
        text, truncated = get_recent_logs()
        self.assertTrue(truncated)
        self.assertEqual(len(text.splitlines()), 1000)
        self.assertTrue(text.startswith('line 200\n'))
        self.assertTrue(text.endswith('line 1199'))

    def test_byte_limit_and_invalid_utf8(self):
        self.path.write_bytes((b'x' * 2048 + b'\n') * 1000 + b'last\xff\n')
        text, truncated = get_recent_logs()
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), 256 * 1024)
        self.assertTrue(text.endswith('last\ufffd'))

    def test_historical_logs_are_redacted_for_viewing_and_download(self):
        self.path.with_suffix('.log.1').write_text(
            'GET /api?apikey=old-secret&cat=7030\n', encoding='utf-8')
        self.path.write_text('X-Api-Key: current-secret\n', encoding='utf-8')
        for text in (get_recent_logs()[0], get_log_file_contents().getvalue()):
            self.assertNotIn('old-secret', text)
            self.assertNotIn('current-secret', text)
            self.assertIn('&cat=7030', text)
            self.assertEqual(text.count('[REDACTED]'), 2)
        self.assertIn('current-secret', self.path.read_text())

    def test_api_requires_authentication_and_disables_caching(self):
        app = Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        client = app.test_client()
        self.path.write_text('<script>not markup</script>\n', encoding='utf-8')
        with patch('frontend.api.Settings') as settings, \
                patch('frontend.api.StartTypeHandlers.diffuse_timer'):
            settings.return_value.sv.api_key = 'fixture-key'
            with patch('frontend.api.get_recent_logs') as read:
                self.assertEqual(client.get('/api/system/logs/recent').status_code, 401)
                read.assert_not_called()
            response = client.get('/api/system/logs/recent?api_key=fixture-key')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.json['result']['text'], '<script>not markup</script>')


class LogRedaction(unittest.TestCase):
    def test_credentials_are_hidden_without_removing_search_details(self):
        examples = [
            'GET /5/api?apikey=fixture-secret&q=Example&cat=7030 HTTP/1.1 200',
            'GET /download?link=fixture-secret&indexer=5',
            '{"api_key": "fixture-secret", "category": 7030}',
            "{'password': 'fixture-secret', 'username': 'admin'}",
            'X-Api-Key: fixture-secret',
            'Authorization: Bearer fixture-secret',
            "{'Authorization': 'Basic fixture-secret'}",
            'http://user:fixture-secret@localhost:9696/5/api',
        ]
        for text in examples:
            with self.subTest(text=text):
                cleaned = redact_log_text(text)
                self.assertNotIn('fixture-secret', cleaned)
                self.assertIn('[REDACTED]', cleaned)
                self.assertEqual(redact_log_text(cleaned), cleaned)
        self.assertIn('&q=Example&cat=7030 HTTP/1.1 200', redact_log_text(examples[0]))
        self.assertEqual(redact_log_text('Indexer 2: query Example category 7030'),
                         'Indexer 2: query Example category 7030')

    def test_dependency_arguments_and_exception_traces_are_redacted(self):
        try:
            raise ValueError('URL /api?apikey=exception-secret')
        except ValueError:
            record = logging.LogRecord('urllib3.connectionpool', logging.ERROR,
                                       __file__, 1, 'GET %s',
                                       ('/api?apikey=request-secret&q=Example',),
                                       sys.exc_info())
        for formatter in (RedactingFormatter(), ErrorColorFormatter()):
            output = formatter.format(record)
            self.assertNotIn('request-secret', output)
            self.assertNotIn('exception-secret', output)
            self.assertIn('ValueError', output)
            self.assertIn('q=Example', output)
        self.assertEqual(record.args, ('/api?apikey=request-secret&q=Example',))
