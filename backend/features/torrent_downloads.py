"""Copy completed torrent content without interfering with active seeding."""

from backend.base.custom_exceptions import ClientNotWorking, CredentialInvalid
from backend.base.definitions import (Constants, DownloadState as DS,
                                      SeedingHandling)
from backend.base.logging import LOGGER
from backend.features.post_processing import PostProcessingContext
from backend.features.usenet_downloads import import_completed
from backend.implementations.managed_job import JobNeedsReview
from backend.internals.server import (QueueStatusEvent,
                                      RemovedFromQueueEvent, WebSocket)


def run_torrent(handler, download):
    ws = WebSocket()
    started, review = False, False
    while download.state != DS.SHUTDOWN_STATE:
        try:
            if download.state == DS.CANCELED_STATE:
                download.cancel_remote()
                PostProcessingContext(download).remove_from_queue()
                if download in handler.queue:
                    handler.queue.remove(download)
                ws.emit(RemovedFromQueueEvent(download))
                return
            if not review:
                if not started:
                    download.run()
                    started = True
                download.update_status()
                import_now = download.state == DS.IMPORTING_STATE or (
                    handler.settings.sv.seeding_handling == SeedingHandling.COPY
                    and download.state == DS.SEEDING_STATE)
                if download.phase != 'imported' and import_now:
                    import_completed(download)
                if download.phase == 'imported' and download.state == DS.IMPORTING_STATE:
                    if handler.settings.sv.delete_completed_downloads:
                        download.remove_from_client(delete_files=False)
                    context = PostProcessingContext(download)
                    context.add_to_history()
                    context.remove_from_queue()
                    if download in handler.queue:
                        handler.queue.remove(download)
                    ws.emit(RemovedFromQueueEvent(download))
                    return
                download.error = None
        except JobNeedsReview as error:
            download.error, review = str(error), True
            if download.state not in (DS.CANCELED_STATE, DS.SHUTDOWN_STATE):
                download.state = DS.PAUSED_STATE
        except (ClientNotWorking, CredentialInvalid):
            download.error = 'Client connection failed. Check credentials and availability.'
        except Exception:
            LOGGER.error(
                'Torrent job %s needs review after a processing error', download.id)
            download.error, review = 'Processing failed. Inspect the client and library copy; automatic retry is paused.', True
            if download.state not in (DS.CANCELED_STATE, DS.SHUTDOWN_STATE):
                download.state = DS.PAUSED_STATE
        ws.emit(QueueStatusEvent(download))
        download.sleep_event.wait(Constants.EXTERNAL_CLIENT_UPDATE_INTERVAL)
        download.sleep_event.clear()
