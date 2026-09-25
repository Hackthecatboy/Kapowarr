"""Bridge Znab protocol operations to v1.3.2's indexer interface."""

from asyncio import get_running_loop
from datetime import datetime, timezone
from functools import partial
from typing import Any, List

from backend.base.custom_exceptions import (ClientNotWorking,
                                            CredentialInvalid, InvalidKeyValue)
from backend.base.definitions import (Constants, QueryResult,
                                      SearchQuery, SearchResultData)
from backend.base.file_extraction import extract_filename_data
from backend.base.logging import LOGGER
from backend.implementations.indexer_client_manager import BaseIndexerClient
from backend.implementations.release_store import remember_release
from backend.implementations.znab import ZnabClient, ZnabPage, ZnabRelease


class ZnabIndexer(BaseIndexerClient):
    # Enabled for search; changed when a download prepper is implemented.
    supports_downloads = False

    def __init__(self, indexer_id: int) -> None:
        super().__init__(indexer_id)
        self._page_limit = None

    @classmethod
    def _client(cls, url: str, api_token: str) -> ZnabClient:
        try:
            client = ZnabClient(url, api_token, cls.download_type)
        except ValueError:
            raise InvalidKeyValue('url', 'Expected a full API URL without query parameters') from None
        # requests inherits HTTP(S)_PROXY/NO_PROXY set by Kapowarr's proxy settings.
        # Avoid the DDL session's URL logging and FlareSolverr credential forwarding.
        client.session.headers['User-Agent'] = Constants.DEFAULT_USERAGENT
        return client

    @classmethod
    def test(cls, url: str, **extra_fields: Any) -> None:
        with cls._client(url, extra_fields.get('api_token') or '') as client:
            client.test()

    def _page(self, query: str, page: int) -> ZnabPage:
        with self._client(self._url, self._api_token or '') as client:
            if self._page_limit is None:
                self._page_limit = min(100, client.test().max_limit)
            return client.search(query, self._categories,
                                 offset=(page - 1) * self._page_limit,
                                 limit=self._page_limit)

    def _result(self, release: ZnabRelease) -> SearchResultData:
        result = {
            **extract_filename_data(release.title, assume_volume_number=False, fix_year=True),
            'link': release.link, 'display_title': release.title, 'size': release.size,
            'indexer_id': self.id, 'indexer_title': self.title,
            'download_supported': self.supports_downloads
        }
        if self.supports_downloads:
            remember_release(result)
        return result

    async def search(self, query: SearchQuery) -> QueryResult:
        try:
            page = await get_running_loop().run_in_executor(
                None, partial(self._page, query['query'], query['page']))
        except (ClientNotWorking, CredentialInvalid) as error:
            reason = error.reason.value if isinstance(error, ClientNotWorking) else 'invalid_credentials'
            LOGGER.warning('Indexer %s search failed (%s); check its connection settings', self.id, reason)
            return QueryResult([], False)
        LOGGER.debug('Indexer %s query %r page %s categories %s returned %s releases',
                     self.id, query['query'], query['page'], self._categories, len(page.releases))
        return QueryResult([self._result(r) for r in page.releases], page.next_page_available)

    async def discover(self, last_check: datetime) -> List[SearchResultData]:
        # Cap work per sync. Undated releases are included, as publication dates
        # are optional; existing library matching determines what is still needed.
        cutoff = last_check.astimezone(timezone.utc)
        results = []
        seen = set()
        for number in range(1, 11):
            try:
                page = await get_running_loop().run_in_executor(
                    None, partial(self._page, '', number))
            except (ClientNotWorking, CredentialInvalid):
                LOGGER.warning('Indexer %s discovery failed', self.id)
                break
            for release in page.releases:
                if release.link not in seen and (release.published is None or release.published > cutoff):
                    results.append(self._result(release))
                    seen.add(release.link)
            if not page.next_page_available:
                break
        return results

    async def shutdown(self) -> None:
        # Each blocking operation owns and closes its own HTTP session.
        return
