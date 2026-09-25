"""Managed Usenet adapter, persistence, path and worker regression tests."""

import sqlite3
import unittest
from backend.internals.settings import PublicSettingsValues
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.base.custom_exceptions import (ClientNotWorking,
                                            CredentialInvalid, InvalidKeyValue)
from backend.base.definitions import (DownloadService, DownloadState as DS,
                                      DownloadType, SpecialVersion)
from backend.features.usenet_downloads import import_completed, run_usenet
from backend.implementations.download_clients.Usenet import UsenetDownload
from backend.implementations.download_preppers.usenet.Newznab import \
    NewznabPrepper
from backend.implementations.external_client_manager import ExternalClients
from backend.implementations.external_clients.usenet.NZBGet import NZBGet
from backend.implementations.external_clients.usenet.SABnzbd import SABnzbd
from backend.implementations.managed_job import JobNeedsReview, JobPathNeedsReview, submit_once
from backend.implementations.release_store import get_release, remember_release
from backend.internals.db import (DB_SCHEMA, KapowarrCursor,
                                  setup_db_adapters_and_converters)
from backend.internals.db_migration import DatabaseMigrationHandler


class UsenetAdapters(unittest.TestCase):
    def client(self, cls):
        client = object.__new__(cls)
        client._id = 1
        client._base_url = 'https://client.example'
        client._username, client._password, client._api_token = 'user', 'secret', 'secret'
        return client

    def test_sab_connection_requires_authenticated_queue_and_category(self):
        for bad in ({}, {'queue': {}}, {'queue': {'version': '4.5', 'slots': None}}):
            with self.subTest(bad=bad), patch.object(SABnzbd, '_request', return_value=bad):
                with self.assertRaises(ClientNotWorking):
                    SABnzbd.test('https://client.example', api_token='secret')
        with patch.object(SABnzbd, '_request', side_effect=[
                {'queue': {'version': '4.5', 'slots': []}}, {'categories': ['kapowarr']}]) as request:
            SABnzbd.test('https://client.example', api_token='secret')
            self.assertEqual(request.call_args_list[0].args[2], 'queue')
        with patch.object(SABnzbd, '_request', side_effect=[
                {'queue': {'version': '4.5', 'slots': []}}, {'categories': ['tv']}]):
            with self.assertRaises(ClientNotWorking):
                SABnzbd.test('https://client.example', api_token='secret')

    def test_sab_rejects_auth_errors_and_invalid_submission_ids(self):
        with patch('backend.implementations.external_clients.usenet.SABnzbd.request_json',
                   return_value={'status': False, 'error': 'API Key Incorrect'}):
            with self.assertRaises(CredentialInvalid):
                SABnzbd.test('https://client.example', api_token='secret')
        client = self.client(SABnzbd)
        for ids in (None, [], [1], ['a', 'b']):
            with patch.object(client, '_call', return_value={'status': True, 'nzo_ids': ids}):
                with self.assertRaises(ClientNotWorking):
                    client.add_download('https://indexer.example/nzb',
                                        '/downloads', 'Comic')
        with patch.object(client, '_call', return_value={'status': True, 'nzo_ids': ['job']}) as call:
            self.assertEqual(client.add_download(
                'https://indexer.example/nzb', '/downloads', 'Comic'), 'job')
            self.assertEqual(call.call_args.kwargs['cat'], 'kapowarr')
            self.assertEqual(call.call_args.kwargs['pp'], 3)

    def test_sab_only_completed_history_is_importable(self):
        client = self.client(SABnzbd)
        for phase in ('Queued', 'Downloading', 'Verifying', 'Repairing', 'Extracting', 'Moving', 'Running', 'Unknown', 'Failed', 'Completed'):
            with self.subTest(phase=phase), patch.object(client, '_call', side_effect=[
                    {'queue': {'slots': []}}, {'history': {'slots': [dict(nzo_id='job', status=phase, bytes=100, storage='/downloads/job')]}}]):
                result = client.get_download('job')
                self.assertEqual(result['state'] ==
                                 DS.IMPORTING_STATE, phase == 'Completed')
                self.assertEqual(result['state'] == DS.FAILED_STATE, phase == 'Failed')
        with patch.object(client, '_call', return_value={'queue': {'slots': [dict(nzo_id='job', status='Downloading', percentage='100')]}}):
            self.assertEqual(client.get_download('job')['state'], DS.DOWNLOADING_STATE)

    def test_sab_missing_and_malformed_jobs_are_distinct(self):
        client = self.client(SABnzbd)
        with patch.object(client, '_call', side_effect=[{'queue': {'slots': []}}, {'history': {'slots': []}}]):
            self.assertIsNone(client.get_download('job'))
        with patch.object(client, '_call', return_value={'queue': {'slots': None}}):
            with self.assertRaises(ClientNotWorking):
                client.get_download('job')

    def test_sab_cleanup_targets_only_tracked_id_and_preserves_files(self):
        client = self.client(SABnzbd)
        with patch.object(client, '_call', side_effect=[{'queue': {'slots': []}},
                                                        {'history': {'slots': [{'nzo_id': 'job'}, {'nzo_id': 'unrelated'}]}}, {'status': True}]) as call:
            client.delete_download('job', False)
            self.assertEqual(call.call_args.kwargs['value'], 'job')
            self.assertEqual(call.call_args.kwargs['del_files'], 0)

    def test_nzbget_connection_validates_rpc_and_category(self):
        for body in ({}, {'id': 2, 'result': '24'}, {'id': 1, 'error': {'code': 1}}, []):
            with patch('backend.implementations.external_clients.usenet.NZBGet.request_json', return_value=body):
                with self.assertRaises(ClientNotWorking):
                    NZBGet.test('https://client.example')
        with patch.object(NZBGet, '_request', side_effect=['24.2', [], [{'Name': 'Category1.Name', 'Value': 'kapowarr'}]]):
            NZBGet.test('https://client.example', 'user', 'secret')
        with patch.object(NZBGet, '_request', side_effect=['24.2', [], []]):
            with self.assertRaises(ClientNotWorking):
                NZBGet.test('https://client.example', 'user', 'secret')

    def test_nzbget_submission_requires_integer_id_and_complete_parameters(self):
        client = self.client(NZBGet)
        for value in (True, False, 0, -1, None, '12'):
            with patch.object(client, '_call', return_value=value):
                with self.assertRaises(ClientNotWorking):
                    client.add_download('https://indexer.example/nzb',
                                        '/downloads', 'Comic')
        with patch.object(client, '_call', return_value=12) as call:
            self.assertEqual(client.add_download(
                'https://indexer.example/nzb', '/downloads', 'Comic'), '12')
            self.assertEqual(call.call_args.args[1][2:], [
                             'kapowarr', 0, False, False, '', 0, 'score'])

    def test_nzbget_repair_is_not_completion_and_sizes_use_high_half(self):
        client = self.client(NZBGet)
        for phase in ('DOWNLOADING', 'REPAIRING', 'UNPACKING', 'MOVING', 'EXECUTING_SCRIPT'):
            with patch.object(client, '_call', return_value=[dict(NZBID=12, Status=phase, FileSizeHi=1, FileSizeLo=4, RemainingSizeLo=0)]):
                result = client.get_download('12')
                self.assertEqual(result['size'], 2**32 + 4)
                self.assertEqual(result['state'], DS.DOWNLOADING_STATE)
        for phase in ('SUCCESS/ALL', 'FAILURE/UNPACK', 'WARNING/REPAIRABLE', 'DELETED/MANUAL', 'UNKNOWN'):
            with patch.object(client, '_call', side_effect=[[], [dict(NZBID=12, Status=phase, FinalDir='/final/job', DestDir='/temp/job')]]):
                result = client.get_download('12')
                self.assertEqual(result['state'] ==
                                 DS.IMPORTING_STATE, phase == 'SUCCESS/ALL')
                if phase == 'SUCCESS/ALL':
                    self.assertEqual(result['storage'], '/final/job')

    def test_nzbget_cleanup_refuses_unsafe_preserve_files_requests(self):
        client = self.client(NZBGet)
        with patch.object(client, '_call', side_effect=[[], [dict(NZBID=12, Status='FAILURE/UNPACK')]]) as call:
            with self.assertRaises(ClientNotWorking):
                client.delete_download('12', False)
            self.assertEqual(call.call_count, 2)
        with patch.object(client, '_call', side_effect=[[], [dict(NZBID=12, Status='SUCCESS/ALL')], True]) as call:
            client.delete_download('12', False)
            self.assertEqual(call.call_args.args, ('editqueue', [
                             'HistoryFinalDelete', 0, '', [12]]))


class ManagedUsenet(unittest.TestCase):
    def setUp(self):
        self.patch('backend.implementations.download_preferences.Settings', return_value=SimpleNamespace(sv=PublicSettingsValues()))
        setup_db_adapters_and_converters()
        self.db = sqlite3.connect(':memory:', detect_types=sqlite3.PARSE_DECLTYPES)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DB_SCHEMA)
        self.addCleanup(self.db.close)
        self.cursor = self.db.cursor(factory=KapowarrCursor)
        for module in ('managed_job', 'release_store', 'external_client_manager'):
            self.patch('backend.implementations.' + module +
                       '.get_db', return_value=self.cursor)
        self.patch('backend.features.usenet_downloads.get_db', return_value=self.cursor)
        self.patch('backend.features.post_processing.get_db', return_value=self.cursor)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'downloads'
        self.root.mkdir()
        self.library = Path(self.tmp.name) / 'library'
        self.library.mkdir()
        self.patch('backend.implementations.download_clients.Usenet.Settings',
                   return_value=SimpleNamespace(sv=SimpleNamespace(download_folder=str(self.root))))
        self.patch('backend.features.usenet_downloads.Volume', return_value=SimpleNamespace(
            vd=SimpleNamespace(folder=str(self.library))))
        self.client = Mock(id=1)
        self.client.add_download.return_value = 'job-123'
        self.download = UsenetDownload('https://indexer.example/nzb?apikey=secret', 1, None,
                                       DownloadService.USENET, 'Comics', None, 'Example Comic (2026)', None,
                                       external_client=self.client)
        self.download.id = 1
        self.cursor.execute(
            "INSERT INTO download_queue(id, volume_id, client_type, download_link, source_type, source_name) VALUES(1, 1, 'usenet', 'https://indexer.example/nzb', 'Usenet', 'Comics')")
        self.db.commit()

    def patch(self, *args, **kwargs):
        patcher = patch(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_submission_persists_id_and_restart_never_resubmits(self):
        self.download.run()
        self.assertEqual(self.download.external_id, 'job-123')
        self.assertEqual(tuple(self.cursor.execute(
            'SELECT external_id, external_phase FROM download_queue').fetchone()), ('job-123', 'submitted'))
        self.download._external_id = None
        self.download.run()
        self.client.add_download.assert_called_once()
        self.assertEqual(self.download.external_id, 'job-123')

    def test_lost_submission_response_is_held_across_restart(self):
        self.client.add_download.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            submit_once(self.download)
        with self.assertRaises(JobNeedsReview):
            submit_once(self.download)
        self.client.add_download.assert_called_once()
        self.assertEqual(self.cursor.execute(
            'SELECT external_phase FROM download_queue').fetchone()[0], 'submitting')

    def test_interrupted_import_is_held_without_resubmission(self):
        self.cursor.execute(
            "UPDATE download_queue SET external_id='job-123', external_phase='importing'")
        with self.assertRaises(JobNeedsReview):
            self.download.run()
        self.client.add_download.assert_not_called()

    def test_remote_mapping_and_output_path_boundary(self):
        job = self.root / 'job'
        job.mkdir()
        self.client.get_download.return_value = dict(
            state=DS.IMPORTING_STATE, storage='/remote/job', size=100, progress=100, speed=0)
        with patch('backend.implementations.download_clients.Usenet.RemoteMappings.remote_to_local', return_value=str(job)) as mapping:
            self.download.update_status()
            mapping.assert_called_once_with(1, '/remote/job')
            self.assertEqual(self.download.files, [str(job)])
        for bad in (str(self.root), str(self.library), str(self.root / 'missing'), 'relative/job'):
            with patch('backend.implementations.download_clients.Usenet.RemoteMappings.remote_to_local', return_value=bad):
                with self.assertRaises(JobNeedsReview):
                    self.download.update_status()
        symlink = self.root / 'link'
        symlink.symlink_to(job, target_is_directory=True)
        with patch('backend.implementations.download_clients.Usenet.RemoteMappings.remote_to_local', return_value=str(symlink)):
            with self.assertRaises(JobNeedsReview):
                self.download.update_status()

    def test_completed_single_comic_path_and_boundaries(self):
        comic = self.root / 'release.cbz'
        comic.write_bytes(b'comic')
        sibling = self.root / 'unrelated.cbz'
        sibling.write_bytes(b'keep')
        self.client.get_download.return_value = dict(
            state=DS.IMPORTING_STATE, storage='/remote/release.cbz',
            size=5, progress=100, speed=0)
        with patch('backend.implementations.download_clients.Usenet.RemoteMappings.remote_to_local', return_value=str(comic)):
            self.download.update_status()
        self.assertEqual(self.download.files, [str(comic)])

        def scan(volume_id, filepath_filter, **kwargs):
            self.assertEqual(len(filepath_filter), 1)
            self.cursor.execute("INSERT INTO issues(id, volume_id, comicvine_id, issue_number, calculated_issue_number) VALUES(1, 1, 1, '1', 1)")
            self.cursor.execute('INSERT INTO files(id, filepath, size) VALUES(1, ?, 5)', (filepath_filter[0],))
            self.cursor.execute('INSERT INTO issues_files(file_id, issue_id) VALUES(1, 1)')
        with patch('backend.features.usenet_downloads.scan_files', side_effect=scan):
            import_completed(self.download)
        self.assertEqual(Path(self.download.files[0]).read_bytes(), b'comic')
        self.assertEqual(comic.read_bytes(), b'comic')
        self.assertEqual(sibling.read_bytes(), b'keep')
        self.assertFalse((Path(self.download.files[0]).parent / sibling.name).exists())
        outside = self.library / 'outside.cbz'
        outside.write_bytes(b'outside')
        unsupported = self.root / 'release.txt'
        unsupported.write_text('unsupported')
        link = self.root / 'link.cbz'
        link.symlink_to(comic)
        for bad in (outside, unsupported, link, self.root / 'missing.cbz'):
            with self.subTest(path=bad), patch(
                    'backend.implementations.download_clients.Usenet.RemoteMappings.remote_to_local', return_value=str(bad)):
                with self.assertRaises(JobNeedsReview):
                    self.download.update_status()

    def test_missing_and_failed_jobs_do_not_disappear_or_resubmit(self):
        for status in (None, dict(state=DS.FAILED_STATE, progress=0, speed=0, size=0)):
            self.client.get_download.return_value = status
            with self.assertRaises(JobNeedsReview):
                self.download.update_status()
        self.client.add_download.assert_not_called()
        self.client.delete_download.assert_not_called()

    def test_import_retains_originals_and_marks_success_after_library_match(self):
        source = self.root / 'job'
        source.mkdir()
        comic = source / 'Example Comic 001 (2026).cbz'
        comic.write_bytes(b'fixture-comic')
        self.download.files = [str(source)]

        def scan(volume_id, filepath_filter, **kwargs):
            self.cursor.execute(
                "INSERT INTO issues(id, volume_id, comicvine_id, issue_number, calculated_issue_number) VALUES(1, 1, 1, '1', 1)")
            self.cursor.execute(
                'INSERT INTO files(id, filepath, size) VALUES(1, ?, 13)', (filepath_filter[0],))
            self.cursor.execute(
                'INSERT INTO issues_files(file_id, issue_id) VALUES(1, 1)')
        with patch('backend.features.usenet_downloads.scan_files', side_effect=scan):
            import_completed(self.download)
        self.assertEqual(comic.read_bytes(), b'fixture-comic')
        self.assertEqual(Path(self.download.files[0]).read_bytes(), b'fixture-comic')
        self.assertEqual(self.download.phase, 'imported')
        self.client.delete_download.assert_not_called()

    def test_completed_payload_passes_real_library_scanner(self):
        import zipfile
        source = self.root / 'job'
        source.mkdir()
        comic = source / 'Example Comic 001 (2026).cbz'
        with zipfile.ZipFile(comic, 'w') as archive:
            archive.writestr('001.jpg', b'fixture-page')
        self.download.files = [str(source)]
        self.download._covered_issues = 1.0
        self.cursor.execute("INSERT INTO volumes(id, comicvine_id, title, year, root_folder, folder, special_version) VALUES(1, 1, 'Example Comic', 2026, 1, ?, ?)", (str(self.library), SpecialVersion.NORMAL.value))
        self.cursor.execute("INSERT INTO issues(id, volume_id, comicvine_id, issue_number, calculated_issue_number, date) VALUES(1, 1, 1, '1', 1, '2026-01-01')")
        for module in ('backend.implementations.volumes', 'backend.implementations.file_matching', 'backend.internals.db_models'):
            self.patch(module + '.get_db', return_value=self.cursor)
        self.patch('backend.implementations.file_matching.commit', side_effect=self.db.commit)
        self.patch('backend.implementations.file_matching.WebSocket')
        settings = self.patch('backend.implementations.file_matching.Settings')
        settings.return_value.get_settings.return_value = SimpleNamespace(create_empty_volume_folders=False, unmonitor_deleted_issues=False, delete_empty_folders=False)
        import_completed(self.download)
        self.assertEqual(self.download.phase, 'imported')
        self.assertEqual(self.cursor.execute('SELECT issue_id FROM issues_files').fetchone()[0], 1)
        self.assertTrue(comic.exists())
        self.assertTrue(Path(self.download.files[0]).exists())

    def test_import_failure_and_existing_destination_never_delete_originals(self):
        source = self.root / 'job'
        source.mkdir()
        comic = source / 'Example.cbz'
        comic.write_bytes(b'fixture')
        self.download.files = [str(source)]
        with patch('backend.features.usenet_downloads.scan_files', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                import_completed(self.download)
        self.assertEqual(comic.read_bytes(), b'fixture')
        self.assertEqual(self.download.phase, 'importing')
        self.download.files = [str(source)]
        with self.assertRaises(JobNeedsReview):
            import_completed(self.download)
        self.client.delete_download.assert_not_called()

    def test_release_cache_roundtrip_and_unknown_link_rejected(self):
        release = dict(indexer_id=1, link='https://indexer.example/nzb',
                       display_title='Comic', issue_number=(1.0, 3.0))
        remember_release(release)
        self.assertEqual(get_release(1, release['link']), release)
        with self.assertRaises(InvalidKeyValue):
            NewznabPrepper('https://unknown.example/nzb', 1, 1)

    def test_prepper_uses_saved_metadata_and_enforces_issue_ownership(self):
        release = dict(indexer_id=1, indexer_title='Comics', link='https://indexer.example/nzb', display_title='Example Comic 001 (2026)',
                       issue_number=1.0, volume_number=None, year=2026, series='Example Comic', special_version=None, annual=False)
        remember_release(release)
        volume = Mock()
        volume.get_data.return_value = SimpleNamespace(
            title='Example Comic', alt_title=None, year=2026, volume_number=1, special_version=SpecialVersion.NORMAL)
        volume.get_issues.return_value = [SimpleNamespace(
            id=1, calculated_issue_number=1.0, date='2026-01-01')]
        with patch('backend.implementations.download_preppers.usenet.Newznab.Volume', return_value=volume), \
                patch('backend.implementations.matching.blocklist_contains', return_value=False), \
                patch('backend.implementations.download_preppers.usenet.Newznab.UsenetDownload') as download:
            NewznabPrepper(release['link'], 1, 1, 1).get_downloads()
            self.assertEqual(download.call_args.args[2], 1.0)
            with self.assertRaises(InvalidKeyValue):
                NewznabPrepper(release['link'], 1, 1, 999, True).get_downloads()

    def test_worker_imports_before_cleanup_and_retains_entry_on_import_error(self):
        handler = SimpleNamespace(queue=[self.download], settings=SimpleNamespace(
            sv=SimpleNamespace(delete_completed_downloads=True)))
        self.download.run = Mock()
        self.download.update_status = Mock(side_effect=lambda: setattr(
            self.download, 'state', DS.IMPORTING_STATE))
        self.download.remove_from_client = Mock()

        def completed(download):
            download.phase = 'imported'
        with patch('backend.features.usenet_downloads.import_completed', side_effect=completed) as importer, \
                patch('backend.features.usenet_downloads.PostProcessingContext') as context, \
                patch('backend.features.usenet_downloads.WebSocket'):
            run_usenet(handler, self.download)
            importer.assert_called_once()
            self.download.remove_from_client.assert_called_once_with(delete_files=False)
            context.return_value.add_to_history.assert_called_once()
            self.assertEqual(handler.queue, [])
        self.download.phase = 'submitted'
        self.download.state = DS.DOWNLOADING_STATE
        handler.queue = [self.download]
        self.download.remove_from_client.reset_mock()
        self.download._sleep_event = Mock()
        self.download._sleep_event.wait.side_effect = lambda *args: self.download.stop(
            DS.SHUTDOWN_STATE)
        with patch('backend.features.usenet_downloads.import_completed', side_effect=OSError), \
                patch('backend.features.usenet_downloads.PostProcessingContext') as context, \
                patch('backend.features.usenet_downloads.WebSocket'):
            run_usenet(handler, self.download)
            self.download.remove_from_client.assert_not_called()
            context.return_value.remove_from_queue.assert_not_called()
            self.assertEqual(handler.queue, [self.download])

    def recovery_handler(self):
        from backend.features.download_queue import DownloadHandler
        handler = object.__new__(DownloadHandler)
        handler.queue = [self.download]
        handler.settings = SimpleNamespace(sv=SimpleNamespace(
            delete_completed_downloads=False, seeding_handling=None))
        return handler

    def test_mapping_recovery_reuses_job_and_imports_once(self):
        handler = self.recovery_handler()
        self.cursor.execute("UPDATE download_queue SET external_id='existing-job', external_phase='submitted'")
        job = self.root / 'completed'
        job.mkdir()
        self.client.get_download.return_value = dict(
            state=DS.IMPORTING_STATE, storage='/remote/completed', size=5, progress=100, speed=0)
        self.download._sleep_event = Mock()
        with patch('backend.implementations.download_clients.Usenet.RemoteMappings.remote_to_local', return_value=str(self.root / 'missing')) as mapping, \
                patch('backend.features.usenet_downloads.import_completed') as importer, \
                patch('backend.features.usenet_downloads.PostProcessingContext') as context, \
                patch('backend.features.usenet_downloads.WebSocket'), \
                patch('backend.features.download_queue.WebSocket'):
            def fix_mapping(*args):
                self.assertTrue(self.download.can_retry)
                self.assertIn('mapped:', self.download.error)
                mapping.return_value = str(job)
                handler.retry_path_reviews(99)
                self.assertFalse(self.download.retry_requested.is_set())
                handler.retry_path_reviews(self.client.id)
                self.assertTrue(self.download.retry_requested.is_set())
                self.assertFalse(self.download.can_retry)
            self.download._sleep_event.wait.side_effect = fix_mapping
            importer.side_effect = lambda download: setattr(download, 'phase', 'imported')
            run_usenet(handler, self.download)
            importer.assert_called_once_with(self.download)
            context.return_value.add_to_history.assert_called_once()
            self.assertEqual(handler.queue, [])
        self.client.add_download.assert_not_called()
        self.client.delete_download.assert_not_called()
        self.assertEqual(self.download.external_id, 'existing-job')

    def test_recovery_endpoint_requires_auth_and_validates_action(self):
        from flask import Flask
        from frontend.api import api
        app = Flask(__name__)
        app.register_blueprint(api, url_prefix='/api')
        client = app.test_client()
        with patch('frontend.api.Settings') as settings, \
                patch('frontend.api.StartTypeHandlers.diffuse_timer'), \
                patch('frontend.api.DownloadHandler') as handler:
            settings.return_value.sv.api_key = 'fixture-key'
            url = '/api/activity/queue/1/recovery'
            self.assertEqual(client.post(url, json={'action': 'retry'}).status_code, 401)
            handler.return_value.recover.assert_not_called()
            self.assertEqual(client.post(url + '?api_key=fixture-key', json={'action': 'unsafe'}).status_code, 400)
            handler.return_value.recover.assert_not_called()
            for action in ('retry', 'forget'):
                self.assertEqual(client.post(url + '?api_key=fixture-key', json={'action': action}).status_code, 200)
                handler.return_value.recover.assert_called_with(1, action)

    def test_recovery_rejects_partial_imports_and_uncertain_submissions(self):
        handler = self.recovery_handler()
        self.download.state = DS.PAUSED_STATE
        self.download.error = 'Held for review'
        self.download.path_review = True
        for phase, external_id in [('importing', 'job'), ('submitting', None), ('queued', None), ('imported', 'job')]:
            self.download.phase = phase
            self.download._external_id = external_id
            with self.subTest(phase=phase), self.assertRaises(InvalidKeyValue):
                handler.recover(self.download.id, 'retry')
        self.download.phase = 'submitted'
        self.download._external_id = 'job'
        self.download.path_review = False
        with self.assertRaises(InvalidKeyValue):
            handler.recover(self.download.id, 'retry')
        self.assertFalse(self.download.retry_requested.is_set())

    def test_queue_only_removal_never_calls_client_for_either_worker(self):
        from backend.features.torrent_downloads import run_torrent
        for worker, module in [(run_usenet, 'usenet_downloads'), (run_torrent, 'torrent_downloads')]:
            with self.subTest(worker=module):
                handler = self.recovery_handler()
                self.download.state = DS.PAUSED_STATE
                self.download.error = 'Import was interrupted'
                self.download.phase = 'importing'
                self.download.forget_requested.clear()
                with patch('backend.features.download_queue.WebSocket'), \
                        patch(f'backend.features.{module}.WebSocket'), \
                        patch(f'backend.features.{module}.PostProcessingContext') as context:
                    handler.recover(self.download.id, 'forget')
                    worker(handler, self.download)
                    context.return_value.remove_from_queue.assert_called_once()
                    context.return_value.add_to_history.assert_not_called()
                self.assertEqual(handler.queue, [])
        self.client.add_download.assert_not_called()
        self.client.get_download.assert_not_called()
        self.client.delete_download.assert_not_called()

    def test_queue_reload_restores_remote_identity_without_network_submission(self):
        from backend.features.download_queue import DownloadHandler
        handler = object.__new__(DownloadHandler)
        handler.queue = []
        handler._process_queue = Mock()
        self.cursor.execute("UPDATE download_queue SET external_client_id=1, external_id='persisted-job', external_phase='submitted'")
        with patch('backend.features.download_queue.get_db', return_value=self.cursor), \
                patch('backend.features.download_queue.iter_commit', side_effect=lambda rows: rows), \
                patch('backend.features.download_queue.ExternalClients.get_client', return_value=self.client), \
                patch('backend.features.download_queue.Server'), \
                patch('backend.features.download_queue.WebSocket'):
            handler._DownloadHandler__load_downloads()
        self.assertEqual(len(handler.queue), 1)
        self.assertEqual(handler.queue[0].external_id, 'persisted-job')
        self.assertEqual(handler.queue[0].phase, 'submitted')
        self.client.add_download.assert_not_called()

    def test_review_reason_is_in_websocket_status_payload(self):
        from backend.internals.server import QueueStatusEvent
        self.download.error = 'Check remote mappings'
        self.download.state = DS.PAUSED_STATE
        self.download.path_review = True
        self.download.phase = 'submitted'
        self.download._external_id = 'job'
        payload = QueueStatusEvent(self.download).get_body()
        self.assertEqual(payload['error'], 'Check remote mappings')
        self.assertTrue(payload['can_retry'])
        self.assertTrue(payload['can_forget'])

    def test_queue_is_committed_and_visible_before_worker_starts(self):
        from backend.features.download_queue import DownloadHandler
        handler = object.__new__(DownloadHandler)
        handler.queue = []
        handler._process_queue = Mock()
        prepper = Mock()
        prepper.get_downloads.return_value = [self.download]

        def started():
            self.assertIn(self.download, handler.queue)
            self.assertFalse(self.db.in_transaction)
        thread = Mock()
        thread.start.side_effect = started
        with patch('backend.features.download_queue.get_db', return_value=self.cursor), \
                patch('backend.features.download_queue.IndexerClients.get_client', return_value=Mock(supports_downloads=True)), \
                patch('backend.features.download_queue.DownloadPreppers.get_prepper', return_value=Mock(return_value=prepper)), \
                patch('backend.features.download_queue.Server') as server, \
                patch('backend.features.download_queue.WebSocket'):
            server.return_value.get_db_thread.return_value = thread
            handler.add('https://indexer.example/nzb', 1, 1)
        thread.start.assert_called_once()

    def test_external_client_api_saves_edits_lists_and_deletes_both_protocol_adapters(self):
        from flask import Flask

        from frontend.api import api
        with patch.dict(ExternalClients.instances, {}, clear=True), \
                patch('frontend.api.Settings') as settings, \
                patch('frontend.api.StartTypeHandlers.diffuse_timer'), \
                patch.object(SABnzbd, 'test'), patch.object(NZBGet, 'test'):
            settings.return_value.sv.api_key = 'fixture-key'
            app = Flask(__name__)
            app.register_blueprint(api, url_prefix='/api')
            http = app.test_client()
            for name in ('SABnzbd', 'NZBGet'):
                payload = dict(download_type=3, client_type=name, enabled=True, title=name,
                               base_url='https://client.example', username='user' if name == 'NZBGet' else None,
                               password='fixture' if name == 'NZBGet' else None,
                               api_token='fixture' if name == 'SABnzbd' else None)
                unauthenticated = http.post('/api/externalclients', json=payload)
                self.assertEqual(unauthenticated.status_code, 401)
                response = http.post(
                    '/api/externalclients?api_key=fixture-key', json=payload)
                self.assertEqual(response.status_code, 201, response.json)
                client_id = response.json['result']['id']
                payload['title'] = 'Updated ' + name
                response = http.put(
                    '/api/externalclients/{}?api_key=fixture-key'.format(client_id), json=payload)
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['result']['title'], payload['title'])
                self.assertEqual(response.json['result']['download_type'], 3)
                response = http.delete(
                    '/api/externalclients/{}?api_key=fixture-key'.format(client_id))
                self.assertEqual(response.status_code, 200)

    def test_new_migration_preserves_existing_jobs_and_is_idempotent(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE download_queue(id INTEGER, download_link TEXT)')
            db.execute("INSERT INTO download_queue VALUES(1, 'existing')")
            with patch('backend.internals.db_migration.get_db', return_value=db.cursor()):
                DatabaseMigrationHandler.handlers[52]()
                DatabaseMigrationHandler.handlers[52]()
            self.assertEqual(db.execute(
                'SELECT * FROM download_queue').fetchone(), (1, 'existing', None, 'queued'))


class UsenetHTTP(unittest.TestCase):
    def test_transport_has_timeouts_no_redirects_and_bounded_response(self):
        import requests

        from backend.implementations.usenet_support import request_json
        with patch('backend.implementations.usenet_support.requests.Session') as session:
            response = session.return_value.__enter__.return_value.request.return_value.__enter__.return_value
            response.status_code = 200
            response.is_redirect = False
            response.iter_content.return_value = [b'x' * (4 * 1024 * 1024 + 1)]
            with self.assertRaises(ClientNotWorking):
                request_json('https://client.example', '/api', data={'apikey': 'secret'})
            request = session.return_value.__enter__.return_value.request
            self.assertEqual(request.call_args.kwargs['timeout'], (10, 30))
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
            self.assertNotIn('secret', request.call_args.args[1])
            request.side_effect = requests.Timeout('secret must not escape')
            with self.assertRaises(ClientNotWorking) as error:
                request_json('https://client.example', '/api', data={'apikey': 'secret'})
            self.assertNotIn('secret', str(error.exception))

    def test_real_http_transport_for_both_clients_and_error_boundaries(self):
        import json
        import os
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from urllib.parse import parse_qs

        from backend.implementations.usenet_support import request_json

        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/api':
                    args = parse_qs(body.decode())
                    calls.append(args)
                    if args.get('apikey') != ['fixture-key']:
                        self.send_response(401)
                        self.end_headers()
                        return
                    mode = args['mode'][0]
                    response = {'queue': {'version': '4.5', 'slots': []}
                                } if mode == 'queue' else {'categories': ['kapowarr']}
                else:
                    args = json.loads(body)
                    calls.append(args)
                    if not self.headers.get('Authorization', '').startswith('Basic '):
                        self.send_response(401)
                        self.end_headers()
                        return
                    values = {'version': '24.2', 'listgroups': [], 'config': [
                        {'Name': 'Category1.Name', 'Value': 'kapowarr'}]}
                    response = {'id': 1, 'result': values[args['method']]}
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<html>Not a client</html>')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:{}'.format(server.server_port)
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                SABnzbd.test(url, api_token='fixture-key')
                NZBGet.test(url, 'fixture-user', 'fixture-password')
                with self.assertRaises(CredentialInvalid):
                    SABnzbd.test(url, api_token='wrong')
                with self.assertRaises(ClientNotWorking):
                    request_json(url, '/not-a-client')
            self.assertEqual(len(calls), 6)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
