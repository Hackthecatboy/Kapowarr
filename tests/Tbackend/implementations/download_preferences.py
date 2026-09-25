"""Release preference validation, persistence, matching and selection."""

import sqlite3
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import SpecialVersion
from backend.base.helpers import CommaList
from backend.features.search_full import SearchCoordinator, choose_downloads
from backend.implementations.download_preferences import evaluate_preferences
from backend.implementations.matching import check_search_result_match
from backend.internals.db import DB_SCHEMA, KapowarrCursor, setup_db_adapters_and_converters
from backend.internals.settings import PublicSettingsValues, Settings


class DownloadPreferences(unittest.TestCase):
    def setUp(self):
        self.settings = PublicSettingsValues()
        self.release = dict(display_title='Example Comic 001 (2026) (Team-A).cbz',
                            size=50 * 1024 * 1024, link='https://example.test/release',
                            issue_number=1.0, volume_number=None, year=2026,
                            series='Example Comic', annual=False, special_version=None)
        self.volume = SimpleNamespace(title='Example Comic', alt_title=None, year=2026,
                                      volume_number=1, special_version=SpecialVersion.NORMAL)
        self.issues = [SimpleNamespace(id=1, calculated_issue_number=1.0, date='2026-01-01')]

    def test_defaults_leave_selection_unchanged(self):
        result = evaluate_preferences(self.release, self.settings)
        self.assertEqual(result, dict(rejection=None, rank=[0, 0], notes=[]))

    def test_size_boundaries_unknowns_and_whole_release_limit(self):
        settings = replace(self.settings, download_min_size_mb=20, download_max_size_mb=100)
        for size, rejected in [(19, True), (20, False), (100, False), (101, True)]:
            with self.subTest(size=size):
                result = evaluate_preferences({**self.release, 'size': size * 1024 * 1024}, settings)
                self.assertEqual(result['rejection'] is not None, rejected)
        for size in (-1, 0, None):
            result = evaluate_preferences({**self.release, 'size': size}, settings)
            self.assertIsNone(result['rejection'])
            self.assertIn('unknown', result['notes'][0])

    def test_format_order_and_unknown_metadata(self):
        settings = replace(self.settings, download_preferred_formats=CommaList(['cbz','cbr']))
        for title, rank in [('Release.CBZ',0), ('Release (CBR)',1), ('Release.pdf',2), ('Release',2), ('Release (cbz/cbr)',2)]:
            result = evaluate_preferences({**self.release,'display_title':title},settings)
            self.assertEqual(result['rank'][0], rank)
            self.assertIsNone(result['rejection'])
        result = evaluate_preferences({**self.release,'display_title':'Release', 'link':'https://host/?token=cbz'},settings)
        self.assertEqual(result['rank'][0],2)

    def test_literal_terms_boundaries_and_exclusion_priority(self):
        settings = replace(self.settings, download_preferred_terms=CommaList(['team-a']), download_excluded_terms=CommaList(['team-a']))
        self.assertIn('Excluded', evaluate_preferences(self.release, settings)['rejection'])
        result = evaluate_preferences({**self.release, 'display_title':'Release (Team-Alpha)'}, settings)
        self.assertIsNone(result['rejection'])
        self.assertEqual(result['rank'][1],0)
        literal = replace(self.settings, download_excluded_terms=CommaList(['a+b']))
        self.assertIsNotNone(evaluate_preferences({**self.release,'display_title':'Release (A+B)'},literal)['rejection'])
        self.assertIsNone(evaluate_preferences({**self.release,'display_title':'Release aaab'},literal)['rejection'])

    def test_matching_and_rss_selection_use_same_policy(self):
        settings = replace(self.settings, download_max_size_mb=20)
        with patch('backend.implementations.download_preferences.Settings', return_value=SimpleNamespace(sv=settings)), patch('backend.implementations.matching.blocklist_contains', return_value=False):
            match = check_search_result_match(self.release,self.volume,self.issues,{1.0:2026},1.0)
            self.assertFalse(match['match'])
            self.assertIn('maximum',match['match_issue'])
            self.assertEqual(choose_downloads([self.release],[(1,1.0)],self.issues),[])

    def test_ranking_and_rss_choose_preferred_release_without_overlap(self):
        settings = replace(self.settings, download_preferred_formats=CommaList(['cbz','cbr']), download_preferred_terms=CommaList(['team-a']))
        other = {**self.release,'display_title':'Example Comic 001 (2026).cbr','link':'other'}
        with patch('backend.implementations.download_preferences.Settings',return_value=SimpleNamespace(sv=settings)):
            chosen=choose_downloads([other,self.release],[(1,1.0)],self.issues)
            self.assertEqual(chosen,[self.release])
            same_format = {**other, 'display_title': 'Example Comic 001 (2026).cbz'}
            self.assertEqual(choose_downloads([same_format,self.release],[(1,1.0)],self.issues),[self.release])
            coordinator=object.__new__(SearchCoordinator)
            coordinator.volume_data=self.volume
            self.assertLess(coordinator._rank_search_result({**self.release,'match':True}),coordinator._rank_search_result({**other,'match':True}))
            # Downloaded issues stay outside the candidate set; no automatic upgrade.
            self.assertEqual(choose_downloads([self.release],[],self.issues),[])

    def test_enqueue_rechecks_preferences_and_requires_explicit_override(self):
        from backend.implementations.download_preppers.usenet.Newznab import NewznabPrepper
        from backend.implementations.download_preppers.torrent.Torznab import TorznabPrepper
        settings = replace(self.settings, download_excluded_terms=CommaList(['team-a']))
        volume = Mock()
        volume.get_data.return_value = self.volume
        volume.get_issues.return_value = self.issues
        with patch('backend.implementations.download_preferences.Settings', return_value=SimpleNamespace(sv=settings)), \
                patch('backend.implementations.matching.blocklist_contains', return_value=False), \
                patch('backend.implementations.download_preppers.usenet.Newznab.get_release', return_value=self.release), \
                patch('backend.implementations.download_preppers.usenet.Newznab.Volume', return_value=volume):
            for cls in (NewznabPrepper, TorznabPrepper):
                with self.subTest(cls=cls), patch.object(cls, 'create_download') as create:
                    with self.assertRaises(InvalidKeyValue):
                        cls(self.release['link'],1,1,1).get_downloads()
                    create.assert_not_called()
                    cls(self.release['link'],1,1,1,force_match=True).get_downloads()
                    create.assert_called_once()

    def test_settings_round_trip_validation_and_atomic_failure(self):
        setup_db_adapters_and_converters()
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript(DB_SCHEMA)
        cursor=db.cursor(factory=KapowarrCursor)
        settings=object.__new__(Settings)
        settings.clear_cache()
        self.addCleanup(settings.clear_cache)
        with patch('backend.internals.settings.get_db',return_value=cursor), patch('backend.internals.settings.commit',side_effect=db.commit):
            settings._insert_missing_settings()
            settings.update(dict(download_min_size_mb=10,download_max_size_mb=100,
                                 download_preferred_formats=['CBZ','cbr','cbz'],
                                 download_preferred_terms=[' Team-A '],download_excluded_terms=['bad']),from_public=True)
            settings.clear_cache()
            self.assertEqual(list(settings.sv.download_preferred_formats),['cbz','cbr'])
            self.assertEqual(list(settings.sv.download_preferred_terms),['team-a'])
            self.assertEqual(settings.sv.download_max_size_mb,100)
            for values in [dict(download_min_size_mb=-1),dict(download_max_size_mb=True),dict(download_preferred_formats=['zip']),dict(download_max_size_mb=5),dict(download_min_size_mb=101)]:
                with self.subTest(values=values),self.assertRaises(InvalidKeyValue):
                    settings.update(values,from_public=True)
                self.assertEqual(settings.sv.download_max_size_mb,100)
                self.assertEqual(settings.sv.download_min_size_mb,10)
