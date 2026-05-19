import logging

from app.notifiers.base import AlertChannel

logger = logging.getLogger(__name__)


class CompositeNotifier:
    def __init__(self, channels: list[AlertChannel]) -> None:
        self.channels = channels

    async def send_listing_alert(self, message: str, payload: dict) -> list[str]:
        successful: list[str] = []
        for channel in self.channels:
            try:
                delivered = await channel.send_listing_alert(message, payload)
            except Exception:
                logger.exception("Alert channel failed", extra={"channel": channel.channel_name})
                continue
            if delivered is False:
                continue
            successful.append(channel.channel_name)
        return successful
