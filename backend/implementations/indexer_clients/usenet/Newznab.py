from backend.base.definitions import DownloadType, IndexerClientField as ICF
from backend.implementations.indexer_client_manager import IndexerClients
from backend.implementations.znab_indexer import ZnabIndexer


@IndexerClients.register_client(
    DownloadType.USENET, 'Newznab',
    (ICF.TITLE, ICF.ENABLED, ICF.URL, ICF.API_TOKEN, ICF.CATEGORIES),
    allow_multiple_instances=True
)
class NewznabIndexer(ZnabIndexer):
    supports_downloads = True
