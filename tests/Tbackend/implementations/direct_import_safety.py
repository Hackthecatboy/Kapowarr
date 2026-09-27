"""Direct import recovery and archive preservation, using disposable fixtures."""
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.base.files import move_file_without_overwrite
from backend.features.direct_import import HeldDirectImport, checkpoint
from backend.features.post_processing import PostProcessor
from backend.implementations import converters


class DirectImportSafety(unittest.TestCase):
    def test_collision_keeps_both_editions_and_copy_failure_keeps_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target = root / 'download.cbz', root / 'issue.cbz'
            source.write_bytes(b'new'); target.write_bytes(b'old')
            result = move_file_without_overwrite(str(source), str(target))
            self.assertEqual(target.read_bytes(), b'old')
            self.assertEqual(Path(result).read_bytes(), b'new')
            self.assertNotEqual(result, str(target))
            source.write_bytes(b'retry')
            with patch('backend.base.files.copyfileobj', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    move_file_without_overwrite(str(source), str(target))
            self.assertEqual(source.read_bytes(), b'retry')
            self.assertFalse((root / 'issue (2).cbz').exists())

    def test_mixed_archive_is_retained_before_any_files_move(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); extracted = root / 'extract'; extracted.mkdir()
            files = [extracted / 'match.cbz', extracted / 'other.cbz']
            for path in files:
                path.write_bytes(b'comic')
            volume = Mock()
            volume.get_data.return_value = SimpleNamespace(folder=tmp, year=2026)
            volume.get_ending_year.return_value = 2026
            with patch.object(converters, 'Volume', return_value=volume), \
                    patch.object(converters, 'list_files', return_value=list(map(str, files))), \
                    patch.object(converters, 'extract_filename_data', return_value={}), \
                    patch.object(converters, 'folder_extraction_filter', side_effect=[True, False]):
                with self.assertRaisesRegex(ValueError, 'retained'):
                    converters.extract_files_from_folder(str(extracted), 1)
            self.assertTrue(all(path.exists() for path in files))
            self.assertFalse((root / 'match.cbz').exists())

    def test_queue_and_history_only_finalize_after_import(self):
        download = SimpleNamespace(id=1, files=['source'])
        processor = PostProcessor(download)
        processor.ctx = Mock()
        processor.ctx.convert_file.side_effect = OSError('conversion failed')
        with patch('backend.features.direct_import.checkpoint'):
            with self.assertRaises(OSError):
                processor.success()
        processor.ctx.remove_from_queue.assert_not_called()
        processor.ctx.add_to_history.assert_not_called()
        processor.ctx.convert_file.side_effect = None
        with patch('backend.features.direct_import.checkpoint'):
            processor.success()
        processor.ctx.remove_from_queue.assert_called_once()
        processor.ctx.add_to_history.assert_called_once()

    def test_checkpoint_restores_without_contacting_download_service(self):
        from backend.base.definitions import DownloadClientIdentifier, DownloadService
        snapshot = dict(id=1, volume_id=1, issue_id=None, web_link=None,
                        web_title=None, web_sub_title=None, download_link='expired',
                        pure_link='expired', download_service=DownloadService.GETCOMICS.value,
                        source_name='test', type=DownloadClientIdentifier.DDL.value,
                        file='/library/archive.zip', title='Archive', download_folder='/downloads',
                        size=123, status='importing', progress=100, speed=0)
        download = Mock(id=1, files=['/library/archive.zip'], filename_body='Archive')
        download.as_dict.return_value = snapshot
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE download_queue(id INTEGER, external_phase TEXT, external_token TEXT)')
        db.execute("INSERT INTO download_queue VALUES(1,'queued','')")
        with patch('backend.features.direct_import.get_db', return_value=db.cursor()):
            checkpoint(download)
        phase, payload = db.execute('SELECT external_phase,external_token FROM download_queue').fetchone()
        self.assertEqual(phase, 'ddl_importing')
        with patch('backend.implementations.download_clients.base.Session') as network:
            held = HeldDirectImport(json.loads(payload))
        network.assert_not_called()
        self.assertEqual(held.files, ['/library/archive.zip'])
        self.assertTrue(held.as_dict()['can_forget'])
        with self.assertRaises(RuntimeError):
            held.run()
        held.stop()
        self.assertTrue(held.can_forget)

    def test_extracted_member_cannot_overwrite_library_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); extracted = root / 'extract'; extracted.mkdir()
            incoming = extracted / 'issue.cbz'; incoming.write_bytes(b'new')
            existing = root / 'issue.cbz'; existing.write_bytes(b'old')
            volume = Mock()
            volume.get_data.return_value = SimpleNamespace(folder=tmp, year=2026)
            volume.get_ending_year.return_value = 2026
            with patch.object(converters, 'Volume', return_value=volume), \
                    patch.object(converters, 'list_files', return_value=[str(incoming)]), \
                    patch.object(converters, 'extract_filename_data', return_value={}), \
                    patch.object(converters, 'folder_extraction_filter', return_value=True), \
                    patch.object(converters, 'set_detected_extension', side_effect=lambda path: path):
                result = converters.extract_files_from_folder(str(extracted), 1)
            self.assertEqual(existing.read_bytes(), b'old')
            self.assertEqual(Path(result[0]).read_bytes(), b'new')
            self.assertNotEqual(result[0], str(existing))

    def test_extension_conversion_cannot_overwrite_existing_comic(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / 'issue.zip'; source.write_bytes(b'new')
            existing = Path(tmp) / 'issue.cbz'; existing.write_bytes(b'old')
            result = converters.zip_to_cbz(str(source))
            self.assertEqual(existing.read_bytes(), b'old')
            self.assertEqual(Path(result[0]).read_bytes(), b'new')
