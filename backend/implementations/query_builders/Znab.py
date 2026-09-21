"""Use existing comic title/year/issue variations for both Znab protocols."""

from backend.base.definitions import DownloadType
from backend.implementations.query_builder_manager import QueryBuilders
from backend.implementations.query_builders.DDL import DDLQueryBuilder


@QueryBuilders.register_builder(DownloadType.TORRENT)
class TorznabQueryBuilder(DDLQueryBuilder):
    pass


@QueryBuilders.register_builder(DownloadType.USENET)
class NewznabQueryBuilder(DDLQueryBuilder):
    pass
