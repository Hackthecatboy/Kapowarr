"""Reuse stored release metadata and comic matching for Torznab downloads."""

from backend.base.definitions import DownloadService, DownloadType
from backend.implementations.download_clients.Torrent import TorrentDownload
from backend.implementations.download_prepper_manager import DownloadPreppers
from backend.implementations.download_preppers.usenet.Newznab import \
    NewznabPrepper


@DownloadPreppers.register_prepper(DownloadType.TORRENT, 'Torznab')
class TorznabPrepper(NewznabPrepper):
    def create_download(self, result):
        return TorrentDownload(
            result['link'], self.volume_id, result['issue_number'],
            DownloadService.TORRENT, result['indexer_title'], result['link'],
            result['display_title'], None, self.force_match
        )
