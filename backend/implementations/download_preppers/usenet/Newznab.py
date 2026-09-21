"""Route a previously searched release through the normal comic matcher."""

from backend.base.custom_exceptions import InvalidKeyValue
from backend.base.definitions import (DownloadPrepper,
                                      DownloadService, DownloadType)
from backend.base.file_extraction import refine_special_version
from backend.base.helpers import extract_year_from_date
from backend.implementations.download_clients.Usenet import UsenetDownload
from backend.implementations.download_prepper_manager import DownloadPreppers
from backend.implementations.matching import check_search_result_match
from backend.implementations.release_store import get_release
from backend.implementations.volumes import Volume


@DownloadPreppers.register_prepper(DownloadType.USENET, 'Newznab')
class NewznabPrepper(DownloadPrepper):
    @property
    def web_title(self):
        return self.release['display_title'] if self.release else None

    def __init__(self, link, indexer_id, volume_id, issue_id=None, force_match=False):
        self.release = get_release(indexer_id, link)
        self.volume_id, self.issue_id, self.force_match = volume_id, issue_id, force_match
        if not self.release:
            # Configuration/expired metadata is not a broken release to blocklist.
            raise InvalidKeyValue('link', 'Run a new indexer search before downloading')

    def get_downloads(self):
        volume = Volume(self.volume_id)
        data, issues = volume.get_data(), volume.get_issues()
        result = refine_special_version(data, dict(self.release))
        wanted = None
        if self.issue_id is not None:
            issue = next((i for i in issues if i.id == self.issue_id), None)
            if issue is None:
                raise InvalidKeyValue('issue_id', self.issue_id)
            wanted = issue.calculated_issue_number
        match = check_search_result_match(result, data, issues,
                                          {i.calculated_issue_number: extract_year_from_date(i.date) for i in issues}, wanted)
        if not self.force_match and not match['match']:
            raise InvalidKeyValue('link', 'Release does not match this volume or issue')
        return [self.create_download(result)]

    def create_download(self, result):
        return UsenetDownload(
            result['link'], self.volume_id, result['issue_number'],
            DownloadService.USENET, result['indexer_title'], result['link'],
            result['display_title'], None, self.force_match
        )
