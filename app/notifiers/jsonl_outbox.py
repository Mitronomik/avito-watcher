import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


class JsonlOutboxNotifier:
    channel_name = "jsonl"

    def __init__(self, enabled: bool | None = None, path: str | None = None) -> None:
        self.enabled = settings.jsonl_outbox_enabled if enabled is None else enabled
        self.path = settings.jsonl_outbox_path if path is None else path

    async def send_listing_alert(self, message: str, payload: dict) -> bool:
        if not self.enabled:
            logger.info("JSONL outbox disabled; skipping listing alert")
            return False

        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "external_id": payload.get("external_id"),
            "title": payload.get("title"),
            "price": payload.get("price"),
            "area_m2": payload.get("area_m2"),
            "address": payload.get("address"),
            "published_label": payload.get("published_label"),
            "url": payload.get("url"),
            "llm_summary": payload.get("summary", payload.get("llm_summary")),
            "search_name": payload.get("search_name"),
            "message": message,
            "payload": payload,
        }

        outbox_path = Path(self.path)
        try:
            outbox_path.parent.mkdir(parents=True, exist_ok=True)
            with outbox_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception:
            logger.exception("Failed to append alert to JSONL outbox", extra={"path": str(outbox_path)})
            raise
