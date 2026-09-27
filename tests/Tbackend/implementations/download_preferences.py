"""Release preference validation, persistence, matching and selection."""

import sqlite3
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import SpecialVersion
from backend.base.helpers import CommaList
from backend.features.search_full import SearchCoordinator, choose_downloads, auto_search
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

    def test_source_order_controls_selection_and_can_be_reversed(self):
        nzb = {**self.release, 'source_type': 'usenet', 'link': 'nzb'}
        ddl = {**self.release, 'source_type': 'ddl', 'link': 'ddl'}
        for order, expected in [(['usenet','torrent','ddl'], nzb), (['ddl','torrent','usenet'], ddl)]:
            settings = replace(self.settings, download_source_order=CommaList(order))
            with patch('backend.implementations.download_preferences.Settings', return_value=SimpleNamespace(sv=settings)):
                self.assertEqual(choose_downloads([ddl,nzb], [(1,1.0)], self.issues), [expected])

    def test_auto_search_queries_later_sources_only_for_uncovered_issues(self):
        settings = replace(self.settings, download_source_order=CommaList(['usenet','torrent','ddl']))
        issues = self.issues + [SimpleNamespace(id=2, calculated_issue_number=2.0, date='2026-02-01')]
        volume = Mock()
        volume.get_data.return_value = SimpleNamespace(**vars(self.volume), monitored=True)
        volume.get_issues.return_value = issues
        volume.get_open_issues.return_value = [(1,1.0),(2,2.0)]
        calls = []
        def coordinator(volume_id, wanted, downloadable_only, source_type):
            calls.append((source_type, wanted))
            async def search():
                if source_type == 'usenet':
                    return [{**self.release, 'source_type': source_type, 'match': True}]
                return [{**self.release, 'source_type': source_type, 'issue_number': 2.0, 'link':'second', 'match':True}]
            return SimpleNamespace(search=search)
        with patch('backend.features.search_full.Volume',return_value=volume), \
                patch('backend.features.search_full.Settings',return_value=SimpleNamespace(sv=settings)), \
                patch('backend.implementations.download_preferences.Settings',return_value=SimpleNamespace(sv=settings)), \
                patch('backend.features.search_full.SearchCoordinator',side_effect=coordinator):
            selected = auto_search(1)
        self.assertEqual(calls,[('usenet',[1,2]),('torrent',[2])])
        self.assertEqual([r['issue_number'] for r in selected],[1.0,2.0])

    def test_auto_search_falls_back_on_empty_or_rejected_results(self):
        settings = replace(self.settings, download_source_order=CommaList(['usenet','torrent','ddl']))
        volume = Mock()
        volume.get_data.return_value = SimpleNamespace(**vars(self.volume), monitored=True)
        volume.get_issues.return_value = self.issues
        volume.get_open_issues.return_value = [(1,1.0)]
        calls = []
        def coordinator(volume_id, wanted, downloadable_only, source_type):
            calls.append(source_type)
            async def search():
                if source_type == 'usenet':
                    return [{**self.release, 'match':False}]
                if source_type == 'torrent':
                    return []
                return [{**self.release, 'match':True, 'source_type':'ddl'}]
            return SimpleNamespace(search=search)
        with patch('backend.features.search_full.Volume',return_value=volume), \
                patch('backend.features.search_full.Settings',return_value=SimpleNamespace(sv=settings)), \
                patch('backend.implementations.download_preferences.Settings',return_value=SimpleNamespace(sv=settings)), \
                patch('backend.features.search_full.SearchCoordinator',side_effect=coordinator):
            self.assertEqual(len(auto_search(1)),1)
        self.assertEqual(calls,['usenet','torrent','ddl'])

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
            settings.update(dict(usenet_completion_delay=60,download_source_order=['usenet','torrent','ddl'],download_min_size_mb=10,download_max_size_mb=100,
                                 download_preferred_formats=['CBZ','cbr','cbz'],
                                 download_preferred_terms=[' Team-A '],download_excluded_terms=['bad']),from_public=True)
            settings.clear_cache()
            self.assertEqual(list(settings.sv.download_preferred_formats),['cbz','cbr'])
            self.assertEqual(list(settings.sv.download_preferred_terms),['team-a'])
            self.assertEqual(settings.sv.download_max_size_mb,100)
            self.assertEqual(settings.sv.usenet_completion_delay,60)
            self.assertEqual(list(settings.sv.download_source_order),['usenet','torrent','ddl'])
            for values in ({'usenet_completion_delay': -1}, {'usenet_completion_delay': True},
                           {'usenet_completion_delay': 3601}, {'download_source_order':['ddl']},
                           {'download_source_order':['ddl','ddl','usenet']}):
                with self.assertRaises(InvalidKeyValue):
                    settings.update(values, from_public=True)
            for values in [dict(download_min_size_mb=-1),dict(download_max_size_mb=True),dict(download_preferred_formats=['zip']),dict(download_max_size_mb=5),dict(download_min_size_mb=101)]:
                with self.subTest(values=values),self.assertRaises(InvalidKeyValue):
                    settings.update(values,from_public=True)
                self.assertEqual(settings.sv.download_max_size_mb,100)
            self.assertEqual(settings.sv.usenet_completion_delay,60)
            self.assertEqual(list(settings.sv.download_source_order),['usenet','torrent','ddl'])
            for values in ({'usenet_completion_delay': -1}, {'usenet_completion_delay': True},
                           {'usenet_completion_delay': 3601}, {'download_source_order':['ddl']},
                           {'download_source_order':['ddl','ddl','usenet']}):
                with self.assertRaises(InvalidKeyValue):
                    settings.update(values, from_public=True)
                self.assertEqual(settings.sv.download_min_size_mb,10)
