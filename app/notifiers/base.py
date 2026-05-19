from typing import Protocol


class AlertChannel(Protocol):
    channel_name: str

    async def send_listing_alert(self, message: str, payload: dict) -> bool | None:
        ...
