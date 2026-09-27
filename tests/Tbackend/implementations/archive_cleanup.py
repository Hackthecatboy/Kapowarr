"""Archive extraction must report imported files and retain failed sources."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from backend.implementations import conversion, converters


class ArchiveCleanup(unittest.TestCase):
    def test_extraction_only_reports_files_and_rescans(self):
        proposal = Mock(filepath='/library/pack.zip', target_format='folder')
        proposal.perform_conversion.return_value = ['/library/issue.cbz']
        with patch.object(conversion, '_get_convertable_files', return_value=[proposal]), \
                patch.object(conversion.ConvertersManager, 'select_converter', return_value=None), \
                patch.object(conversion.FilesDB, 'delete_filepath') as delete_record, \
                patch.object(conversion, 'scan_files') as scan, \
                patch.object(conversion, 'mass_process_files') as process:
            result = conversion.mass_convert(1, update_websocket_files=True)
        self.assertEqual(result, ['/library/issue.cbz'])
        delete_record.assert_called_once_with('/library/pack.zip')
        scan.assert_called_once_with(1, filepath_filter=result, update_websocket=True)
        process.assert_called_once_with(1)

    def test_declined_extraction_keeps_archive_record(self):
        proposal = Mock(filepath='/library/pack.zip', target_format='folder')
        proposal.perform_conversion.return_value = [proposal.filepath]
        with patch.object(conversion, '_get_convertable_files', return_value=[proposal]), \
                patch.object(conversion.FilesDB, 'delete_filepath') as delete_record:
            self.assertEqual(conversion.mass_convert(1), [])
        delete_record.assert_not_called()

    def test_zip_removed_after_import_but_retained_when_import_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'pack.zip'
            output = root / 'issue.cbz'
            def extract(folder, volume_id):
                output.write_bytes((Path(folder) / 'issue.cbz').read_bytes())
                return [str(output)]
            with patch.object(converters.FilesDB, 'volume_of_file', return_value=1), \
                    patch.object(converters, 'Volume', return_value=SimpleNamespace(vd=SimpleNamespace(folder=tmp))), \
                    patch.object(converters, 'extract_files_from_folder', side_effect=extract), \
                    patch.object(converters, 'scan_files') as scan, \
                    patch.object(converters, 'mass_rename', return_value=[str(output)]):
                with ZipFile(source, 'w') as archive:
                    archive.writestr('issue.cbz', b'comic-data')
                self.assertEqual(converters.zip_to_folder(str(source)), [str(output)])
                self.assertFalse(source.exists())
                self.assertEqual(output.read_bytes(), b'comic-data')
                with ZipFile(source, 'w') as archive:
                    archive.writestr('issue.cbz', b'comic-data')
                scan.side_effect = OSError('Import unavailable')
                with self.assertRaises(OSError):
                    converters.zip_to_folder(str(source))
                self.assertTrue(source.exists())

    def test_conversion_failure_logs_source_and_preserves_error(self):
        proposal = converters.ProposedConversion(
            '/library/pack.zip', Mock(side_effect=OSError('Import failed')), 'folder')
        with patch.object(converters.LOGGER, 'exception') as log:
            with self.assertRaises(OSError):
                proposal.perform_conversion()
        self.assertEqual(log.call_args.args[1], '/library/pack.zip')
