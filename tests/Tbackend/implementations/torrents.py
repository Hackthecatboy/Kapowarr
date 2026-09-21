"""Torrent metadata, adapters, ownership and seeding lifecycle regression tests."""

import base64
import hashlib
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bencoding import bencode

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (DownloadService, DownloadState as DS,
                                      SeedingHandling)
from backend.features.torrent_downloads import run_torrent
from backend.features.usenet_downloads import import_completed
from backend.implementations.download_clients.Torrent import TorrentDownload
from backend.implementations.external_clients.torrent.qBittorrent import \
    qBittorrent
from backend.implementations.external_clients.torrent.Transmission import \
    Transmission
from backend.implementations.managed_job import JobNeedsReview
from backend.implementations.torrent_support import (magnet_payload,
                                                     resolve_torrent,
                                                     torrent_payload)
from backend.internals.db import (DB_SCHEMA, KapowarrCursor,
                                  setup_db_adapters_and_converters)
from backend.internals.db_migration import DatabaseMigrationHandler

INFO = {b'name': b'Example Comic 001 (2026).cbz', b'length': 3,
        b'piece length': 16384, b'pieces': b'x' * 20}
META = bencode({b'announce': b'https://tracker.example/private-key', b'info': INFO})
HASH = hashlib.sha1(bencode(INFO)).hexdigest()
MAGNET = 'magnet:?xt=urn:btih:' + HASH + '&tr=https%3A%2F%2Ftracker.example%2Fprivate-key'


class TorrentMetadata(unittest.TestCase):
    def test_magnet_hex_and_base32_identity_preserve_private_trackers(self):
        self.assertEqual(magnet_payload(MAGNET).info_hash, HASH)
        self.assertEqual(magnet_payload(MAGNET).magnet, MAGNET)
        encoded = base64.b32encode(bytes.fromhex(HASH)).decode()
        self.assertEqual(magnet_payload(
            'magnet:?xt=urn:btih:' + encoded).info_hash, HASH)
        for link in ('magnet:?xt=urn:btmh:1220abc', 'magnet:?xt=urn:btih:invalid',
                     MAGNET + '&xt=urn:btih:' + 'a' * 40):
            with self.assertRaises(JobNeedsReview):
                magnet_payload(link)

    def test_file_hash_uses_exact_info_bytes_and_retains_metainfo(self):
        payload = torrent_payload(META)
        self.assertEqual(payload.info_hash, HASH)
        self.assertEqual(payload.metainfo, META)
        # A differently ordered dictionary must not be canonicalized before hashing.
        raw = b'd4:name1:x6:lengthi3e12:piece lengthi16e6:pieces20:' + b'x' * 20 + b'e'
        self.assertEqual(torrent_payload(b'd4:info' + raw +
                         b'e').info_hash, hashlib.sha1(raw).hexdigest())

    def test_bad_metadata_paths_and_v2_only_are_rejected(self):
        bad_info = [dict(INFO, **{}), {b'name': b'x', b'meta version': 2}]
        bad_info[0][b'name'] = b'../outside'
        for info in bad_info:
            with self.assertRaises(JobNeedsReview):
                torrent_payload(bencode({b'info': info}))
        for body in (b'<html>login</html>', META + b'junk', b'l' * 100 + b'e' * 100):
            with self.assertRaises(JobNeedsReview):
                torrent_payload(body)
        for path in ([b'..', b'outside'], [b'/absolute'], [b'a\\b']):
            info = {b'name': b'Comic', b'files': [{b'length': 3, b'path': path}],
                    b'pieces': b'x' * 20, b'piece length': 16}
            with self.assertRaises(JobNeedsReview):
                torrent_payload(bencode({b'info': info}))

    def test_indexer_redirects_to_torrent_or_magnet_without_conversion_service(self):
        with patch('backend.implementations.torrent_support.http', side_effect=[
                (302, {'Location': '/download'}, b''), (200, {}, META)]) as request:
            self.assertEqual(resolve_torrent(
                'https://indexer.example/api').info_hash, HASH)
            self.assertEqual(
                request.call_args.args[2], 'https://indexer.example/download')
        with patch('backend.implementations.torrent_support.http', return_value=(302, {'Location': MAGNET}, b'')) as request:
            self.assertEqual(resolve_torrent(
                'https://indexer.example/api').magnet, MAGNET)
            request.assert_called_once()
        with patch('backend.implementations.torrent_support.http') as request:
            resolve_torrent(MAGNET)
            request.assert_not_called()


class TorrentAdapters(unittest.TestCase):
    def client(self, cls):
        result = object.__new__(cls)
        result._base_url, result._username, result._password = 'http://client.example', 'user', 'password'
        return result

    def qb_info(self, phase, progress=1, left=0):
        return dict(hash=HASH, state=phase, progress=progress, amount_left=left,
                    total_size=3, dlspeed=1, content_path='/downloads/job/Comic.cbz',
                    save_path='/downloads/job', tags='one,two')

    def test_qbittorrent_only_explicit_completed_states_are_importable(self):
        client = self.client(qBittorrent)
        for phase in ('metaDL', 'checkingUP', 'checkingDL', 'checkingResumeData', 'moving', 'unknown', 'stalledDL'):
            with patch.object(client, '_request', return_value=(200, {}, __import__('json').dumps([self.qb_info(phase)]).encode())):
                self.assertEqual(client.get_download(
                    HASH)['state'], DS.DOWNLOADING_STATE)
        for phase, expected in [('stoppedUP', DS.IMPORTING_STATE), ('pausedUP', DS.IMPORTING_STATE),
                                ('uploading', DS.SEEDING_STATE), ('queuedUP', DS.SEEDING_STATE),
                                ('missingFiles', DS.FAILED_STATE), ('stoppedDL', DS.PAUSED_STATE)]:
            with patch.object(client, '_request', return_value=(200, {}, __import__('json').dumps([self.qb_info(phase)]).encode())):
                info = client.get_download(HASH)
                self.assertEqual(info['state'], expected)
                self.assertEqual(info['tags'], ['one', 'two'])
                self.assertEqual(info['storage'], '/downloads/job/Comic.cbz')
        with patch.object(client, '_request', return_value=(200, {}, __import__('json').dumps([self.qb_info('stoppedUP', .999, 1)]).encode())):
            self.assertEqual(client.get_download(HASH)['state'], DS.DOWNLOADING_STATE)

    def test_qbittorrent_uploads_torrent_bytes_and_checks_add_response(self):
        client = self.client(qBittorrent)
        with patch.object(client, '_request', return_value=(200, {}, b'Ok.')) as request:
            self.assertEqual(client.add_torrent(
                torrent_payload(META), '/target', 'owned'), HASH)
            self.assertEqual(request.call_args.kwargs['files']['torrents'][1], META)
            self.assertEqual(request.call_args.kwargs['data']['savepath'], '/target')
            self.assertEqual(request.call_args.kwargs['data']['tags'], 'owned')
        with patch.object(client, '_request', return_value=(200, {}, b'Fails.')):
            with self.assertRaises(ClientNotWorking):
                client.add_torrent(magnet_payload(MAGNET), '/target', 'owned')
        with patch.object(client, '_request', return_value=(200, {}, b'[]')):
            self.assertIsNone(client.get_download(HASH))

    def transmission_info(self, state, done=1):
        return dict(hash_string=HASH, status=state, percent_done=done,
                    left_until_done=0 if done == 1 else 1, metadata_percent_complete=1,
                    total_size=3, rate_download=0, download_dir='/downloads/job',
                    name='Comic.cbz', labels=['owned'], error=0)

    def test_transmission_checks_metadata_rechecks_and_completion(self):
        client = self.client(Transmission)
        for phase, expected in [(0, DS.IMPORTING_STATE), (1, DS.DOWNLOADING_STATE),
                                (2, DS.DOWNLOADING_STATE), (5, DS.SEEDING_STATE),
                                (6, DS.SEEDING_STATE), (99, DS.DOWNLOADING_STATE)]:
            with patch.object(client, '_call', return_value={'torrents': [self.transmission_info(phase)]}):
                self.assertEqual(client.get_download(HASH)['state'], expected)
        for change in ({'percent_done': .999}, {'metadata_percent_complete': 0}, {'left_until_done': 1}):
            with patch.object(client, '_call', return_value={'torrents': [{**self.transmission_info(0), **change}]}):
                self.assertEqual(client.get_download(HASH)['state'], DS.PAUSED_STATE)
        with patch.object(client, '_call', return_value={'torrents': []}):
            self.assertIsNone(client.get_download(HASH))

    def test_transmission_submission_and_duplicate_response(self):
        client = self.client(Transmission)
        with patch.object(client, '_call', return_value={'torrent_added': {'hash_string': HASH}}) as request:
            client.add_torrent(torrent_payload(META), '/target', 'owned')
            self.assertEqual(base64.b64decode(
                request.call_args.args[1]['metainfo']), META)
            self.assertEqual(request.call_args.args[1]['labels'], ['owned'])
        with patch.object(client, '_call', return_value={'torrent_duplicate': {'hash_string': HASH}}):
            with self.assertRaises(JobNeedsReview):
                client.add_torrent(magnet_payload(MAGNET), '/target', 'owned')
        with patch.object(client, '_call', return_value={}):
            with self.assertRaises(ClientNotWorking):
                client.add_torrent(magnet_payload(MAGNET), '/target', 'owned')

    def test_transmission_session_challenge_is_bounded(self):
        with patch('backend.implementations.external_clients.torrent.Transmission.http', return_value=(409, {'X-Transmission-Session-Id': 'sid'}, b'')) as request:
            with self.assertRaises(ClientNotWorking):
                Transmission.test('http://client.example')
            self.assertEqual(request.call_count, 2)


class ManagedTorrents(unittest.TestCase):
    def setUp(self):
        setup_db_adapters_and_converters()
        self.db = sqlite3.connect(':memory:', detect_types=sqlite3.PARSE_DECLTYPES)
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DB_SCHEMA)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        for path in ('backend.implementations.managed_job', 'backend.implementations.download_clients.Torrent',
                     'backend.features.usenet_downloads'):
            self.patch(path + '.get_db', return_value=self.cursor)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'downloads'
        self.root.mkdir()
        self.patch('backend.implementations.download_clients.Usenet.Settings',
                   return_value=SimpleNamespace(sv=SimpleNamespace(download_folder=str(self.root))))
        self.patch('backend.implementations.download_clients.Torrent.RemoteMappings.local_to_remote',
                   side_effect=lambda client, path: path)
        self.patch('backend.implementations.download_clients.Torrent.RemoteMappings.remote_to_local',
                   side_effect=lambda client, path: path)
        self.client = Mock(id=1)
        self.client.get_download.return_value = None
        self.client.add_torrent.return_value = HASH
        self.download = self.new_download()
        self.cursor.execute(
            "INSERT INTO download_queue(id, volume_id, client_type, download_link, source_type, source_name) VALUES(1, 1, 'torrent', ?, 'Torrent', 'Comics')", (MAGNET,))
        self.db.commit()

    def patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def new_download(self):
        result = TorrentDownload(MAGNET, 1, None, DownloadService.TORRENT,
                                 'Comics', MAGNET, 'Comic', None, external_client=self.client)
        result.id = 1
        return result

    def owned_info(self, phase=DS.SEEDING_STATE):
        return dict(state=phase, size=3, speed=0, progress=100,
                    tags=['kapowarr-' + self.download.token],
                    save_path=str(self.download.target_folder),
                    storage=str(self.download.target_folder / 'Comic.cbz'))

    def test_submit_identity_token_and_restart_without_resubmission(self):
        self.download.run()
        self.assertEqual(self.download.external_id, HASH)
        token = self.download.token
        restored = self.new_download()
        restored.run()
        self.assertEqual(restored.external_id, HASH)
        self.assertEqual(restored.token, token)
        self.client.add_torrent.assert_called_once()
        self.assertEqual(self.client.add_torrent.call_args.args[1], str(
            self.root / ('kapowarr-' + token)))

    def test_existing_remote_torrent_is_not_adopted(self):
        self.client.get_download.return_value = {'state': DS.SEEDING_STATE}
        with self.assertRaises(JobNeedsReview):
            self.download.run()
        self.client.add_torrent.assert_not_called()
        self.client.delete_download.assert_not_called()

    def test_lost_submission_response_is_not_replayed(self):
        self.client.add_torrent.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            self.download.run()
        with self.assertRaises(JobNeedsReview):
            self.new_download().run()
        self.client.add_torrent.assert_called_once()

    def test_completed_paths_ownership_and_cancel_boundary(self):
        self.download.run()
        self.download.target_folder.mkdir()
        (self.download.target_folder / 'Comic.cbz').write_bytes(b'abc')
        info = self.owned_info()
        self.client.get_download.return_value = info
        self.download.update_status()
        self.assertEqual(self.download.files, [info['storage']])
        for bad in ({**info, 'tags': ['someone-else']}, {**info, 'save_path': str(self.root)},
                    {**info, 'storage': str(self.root)}):
            self.client.get_download.return_value = bad
            with self.assertRaises(JobNeedsReview):
                self.download.update_status()
        self.client.get_download.return_value = {**info, 'tags': ['someone-else']}
        self.download.cancel_remote()
        self.client.delete_download.assert_not_called()
        self.client.get_download.return_value = info
        self.download.phase = 'imported'
        self.download.remove_from_client(False)
        self.client.delete_download.assert_called_once_with(HASH, False)

    def test_delayed_visibility_is_tolerated_but_missing_job_is_not_resubmitted(self):
        self.download.run()
        for _ in range(3):
            self.download.update_status()
        with self.assertRaises(JobNeedsReview):
            self.download.update_status()
        self.client.add_torrent.assert_called_once()

    def run_worker(self, mode, phases, restored=False, fail_import=False):
        download = self.download
        download.run = Mock()
        download.phase = 'imported' if restored else 'submitted'
        remaining = iter(phases)
        download.update_status = Mock(
            side_effect=lambda: setattr(download, 'state', next(remaining)))
        download.remove_from_client = Mock()
        download._sleep_event = Mock()
        handler = SimpleNamespace(queue=[download], settings=SimpleNamespace(
            sv=SimpleNamespace(seeding_handling=mode, delete_completed_downloads=True)))
        observed = []

        def copied(dl):
            observed.append(dl.state)
            if fail_import:
                raise OSError
            dl.phase = 'imported'
        if fail_import:
            download._sleep_event.wait.side_effect = lambda _: download.stop(
                DS.SHUTDOWN_STATE)
        with patch('backend.features.torrent_downloads.import_completed', side_effect=copied) as importer, \
                patch('backend.features.torrent_downloads.WebSocket'), \
                patch('backend.features.torrent_downloads.PostProcessingContext') as context:
            run_torrent(handler, download)
        return importer, context, handler, observed

    def test_copy_seeding_imports_once_then_cleans_after_seeding_stops(self):
        importer, context, handler, observed = self.run_worker(
            SeedingHandling.COPY, [DS.SEEDING_STATE, DS.SEEDING_STATE, DS.IMPORTING_STATE])
        self.assertEqual(observed, [DS.SEEDING_STATE])
        self.assertEqual(handler.queue, [])
        self.download.remove_from_client.assert_called_once_with(delete_files=False)
        context.return_value.add_to_history.assert_called_once()

    def test_complete_mode_waits_and_restart_does_not_copy_twice(self):
        _, _, _, observed = self.run_worker(SeedingHandling.COMPLETE, [
                                            DS.SEEDING_STATE, DS.IMPORTING_STATE])
        self.assertEqual(observed, [DS.IMPORTING_STATE])
        self.download.state = DS.QUEUED_STATE
        importer, _, _, _ = self.run_worker(
            SeedingHandling.COPY, [DS.SEEDING_STATE, DS.IMPORTING_STATE], restored=True)
        importer.assert_not_called()

    def test_import_failure_never_removes_remote_or_local_queue(self):
        _, context, handler, _ = self.run_worker(
            SeedingHandling.COPY, [DS.SEEDING_STATE], fail_import=True)
        self.download.remove_from_client.assert_not_called()
        context.return_value.remove_from_queue.assert_not_called()
        self.assertEqual(handler.queue, [self.download])

    def test_migration_holds_legacy_torrents_and_preserves_usenet(self):
        self.cursor.execute(
            "INSERT INTO download_queue(id, volume_id, client_type, download_link, source_type, source_name, external_id, external_phase) VALUES(2, 1, 'usenet', 'url', 'Usenet', 'Comics', 'nzb-id', 'submitted')")
        with patch('backend.internals.db_migration.get_db', return_value=self.cursor):
            DatabaseMigrationHandler.handlers[53]()
            DatabaseMigrationHandler.handlers[53]()
        self.assertEqual(self.cursor.execute(
            'SELECT external_phase FROM download_queue WHERE id=1').fetchone()[0], 'submitting')
        self.assertEqual(tuple(self.cursor.execute(
            'SELECT external_id, external_phase FROM download_queue WHERE id=2').fetchone()), ('nzb-id', 'submitted'))

    def test_shared_import_accepts_single_file_payload_and_keeps_original(self):
        self.download.run()
        self.download.target_folder.mkdir()
        source = self.download.target_folder / 'Example Comic 001 (2026).cbz'
        source.write_bytes(b'fixture')
        self.download.files = [str(source)]
        library = Path(self.tmp.name) / 'library'
        library.mkdir()

        def scan(volume_id, filepath_filter, **kwargs):
            self.cursor.execute(
                "INSERT INTO issues(id, volume_id, comicvine_id, issue_number, calculated_issue_number) VALUES(1, 1, 1, '1', 1)")
            self.cursor.execute(
                'INSERT INTO files(id, filepath, size) VALUES(1, ?, 7)', (filepath_filter[0],))
            self.cursor.execute(
                'INSERT INTO issues_files(file_id, issue_id) VALUES(1, 1)')
        with patch('backend.features.usenet_downloads.Volume', return_value=SimpleNamespace(vd=SimpleNamespace(folder=str(library)))), \
                patch('backend.features.usenet_downloads.scan_files', side_effect=scan):
            import_completed(self.download)
        self.assertTrue(source.exists())
        self.assertEqual(Path(self.download.files[0]).read_bytes(), b'fixture')
        self.assertEqual(self.download.phase, 'imported')


class TorrentHTTP(unittest.TestCase):
    def test_real_http_login_upload_rpc_challenge_and_delete(self):
        import json
        import os
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from urllib.parse import parse_qs, urlsplit

        calls = []
        qb_job = dict(hash=HASH, state='stoppedUP', progress=1, amount_left=0,
                      total_size=3, content_path='/downloads/job/Comic.cbz',
                      save_path='/downloads/job', tags='owned')

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, data, code=200, headers=None):
                self.send_response(code)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlsplit(self.path).path
                if self.headers.get('Cookie') != 'SID=fixture':
                    return self.reply(b'', 403)
                if path.endswith('/app/version'):
                    return self.reply(b'v5.0.0')
                if path.endswith('/categories'):
                    return self.reply(b'{"kapowarr":{}}')
                return self.reply(json.dumps([qb_job]).encode())

            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                calls.append((self.path, data))
                if self.path.endswith('/auth/login'):
                    if parse_qs(data.decode()).get('password') != ['fixture']:
                        return self.reply(b'Fails.')
                    return self.reply(b'Ok.', headers={'Set-Cookie': 'SID=fixture; Path=/'})
                if self.path.startswith('/api/v2/'):
                    if self.headers.get('Cookie') != 'SID=fixture':
                        return self.reply(b'', 403)
                    return self.reply(b'Ok.' if self.path.endswith('/add') else b'')
                if self.headers.get('X-Transmission-Session-Id') != 'fixture-sid':
                    return self.reply(b'', 409, {'X-Transmission-Session-Id': 'fixture-sid'})
                args = json.loads(data)
                result = {}
                if args['method'] == 'session_get':
                    result = {'rpc_version_semver': '6.0.0'}
                elif args['method'] == 'torrent_add':
                    result = {'torrent_added': {'hash_string': HASH}}
                elif args['method'] == 'torrent_get':
                    result = {'torrents': [dict(hash_string=HASH, status=0, percent_done=1,
                                                left_until_done=0, metadata_percent_complete=1, total_size=3,
                                                name='Comic.cbz', download_dir='/downloads/job', labels=['owned'])]}
                self.reply(json.dumps(
                    {'jsonrpc': '2.0', 'id': 1, 'result': result}).encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:{}'.format(server.server_port)
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                for cls in (qBittorrent, Transmission):
                    cls.test(url, 'user', 'fixture')
                    client = object.__new__(cls)
                    client._base_url, client._username, client._password = url, 'user', 'fixture'
                    self.assertEqual(client.add_torrent(
                        torrent_payload(META), '/downloads/job', 'owned'), HASH)
                    self.assertEqual(client.get_download(
                        HASH)['state'], DS.IMPORTING_STATE)
                    client.delete_download(HASH, False)
                with self.assertRaises(CredentialInvalid):
                    qBittorrent.test(url, 'user', 'wrong')
            qb_add = next(
                body for path, body in calls if path.endswith('/torrents/add'))
            self.assertIn(META, qb_add)
            qb_delete = next(
                body for path, body in calls if path.endswith('/torrents/delete'))
            self.assertEqual(parse_qs(qb_delete.decode())['deleteFiles'], ['false'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
