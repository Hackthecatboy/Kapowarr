"""Automatic searches reserve queued issues until import or explicit removal."""

import sqlite3
import unittest
from unittest.mock import patch

from backend.implementations.volumes import Volume


class PendingIssues(unittest.TestCase):
    def test_pending_ranges_and_review_jobs_exclude_only_their_issues(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''
            CREATE TABLE issues(id, calculated_issue_number, volume_id, monitored);
            CREATE TABLE issues_files(issue_id, file_id);
            CREATE TABLE download_queue(volume_id, covered_issues, external_phase);
            INSERT INTO issues VALUES(1,1.0,1,1),(2,2.0,1,1),(3,3.0,1,1),(4,4.0,1,0);
            INSERT INTO download_queue VALUES(2,'1','submitted');
        ''')
        with patch('backend.implementations.volumes.get_db', return_value=db.cursor()):
            volume = Volume(1)
            self.assertEqual(volume.get_open_issues(), [(1, 1.0), (2, 2.0), (3, 3.0)])
            for phase in ('queued', 'submitted', 'importing', 'imported'):
                db.execute('DELETE FROM download_queue WHERE volume_id=1')
                db.execute('INSERT INTO download_queue VALUES(1,?,?)', ('1,2', phase))
                self.assertEqual(volume.get_open_issues(), [(3, 3.0)])
            db.execute('UPDATE download_queue SET covered_issues=\'2\' WHERE volume_id=1')
            self.assertEqual(volume.get_open_issues(), [(1, 1.0), (3, 3.0)])
            for value in (None, '', 'broken', '1,2,3', '3,1'):
                db.execute('UPDATE download_queue SET covered_issues=? WHERE volume_id=1', (value,))
                self.assertEqual(volume.get_open_issues(), [])
            db.execute('DELETE FROM download_queue WHERE volume_id=1')
            db.execute('INSERT INTO issues_files VALUES(1,42)')
            self.assertEqual(volume.get_open_issues(), [(2, 2.0), (3, 3.0)])
