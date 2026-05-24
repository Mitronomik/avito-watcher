from datetime import UTC, datetime
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class GoogleSheetsWebhookNotifier:
    channel_name = "google_sheets"

    def __init__(
        self,
        enabled: bool | None = None,
        webhook_url: str | None = None,
        secret: str | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        self.enabled = (
            settings.google_sheets_webhook_enabled if enabled is None else enabled
        )
        self.webhook_url = (
            settings.google_sheets_webhook_url if webhook_url is None else webhook_url
        )
        self.secret = (
            settings.google_sheets_webhook_secret if secret is None else secret
        )
        self.timeout_sec = (
            settings.google_sheets_webhook_timeout_sec
            if timeout_sec is None
            else timeout_sec
        )

    async def send_listing_alert(self, message: str, payload: dict) -> bool | None:
        if not self.enabled or not self.webhook_url:
            logger.info("Google Sheets webhook is not configured; skipping listing alert")
            return False

        outbound = {
            "secret": self.secret,
            "search_name": payload.get("search_name"),
            "external_id": payload.get("external_id"),
            "title": payload.get("title"),
            "price": payload.get("price"),
            "area_m2": payload.get("area_m2"),
            "rooms": payload.get("rooms"),
            "address": payload.get("address"),
            "published_label": payload.get("published_label"),
            "published_at": payload.get("published_at"),
            "url": payload.get("url"),
            "summary": payload.get("summary"),
            "score": payload.get("score"),
            "tags": payload.get("tags"),
            "message": message,
            "sent_at": datetime.now(UTC).isoformat(),
        }

        timeout = httpx.Timeout(self.timeout_sec)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(self.webhook_url, json=outbound)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                logger.warning(
                    "Google Sheets webhook returned non-JSON response status=%s content_type=%s",
                    response.status_code,
                    response.headers.get("content-type", ""),
                )
                return False

            if isinstance(data, dict) and data.get("ok") is True:
                return True

            if isinstance(data, dict) and data.get("ok") is False:
                logger.warning(
                    "Google Sheets webhook responded with ok=false error=%s",
                    data.get("error"),
                )
                return False

            safe_keys = sorted(data.keys()) if isinstance(data, dict) else [type(data).__name__]
            logger.warning(
                "Google Sheets webhook returned unexpected JSON response status=%s keys=%s",
                response.status_code,
                safe_keys,
            )
            return False
