import asyncio
import logging
from typing import Any

from telegram import Bot

from app.core.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_SEND_ATTEMPTS = 3
TELEGRAM_RETRY_DELAY_SEC = 1
TELEGRAM_TIMEOUT_SEC = 10


class TelegramNotifier:
    channel_name = "telegram"
    def __init__(self, bot: Any | None = None, chat_id: str | None = None) -> None:
        self.chat_id = chat_id if chat_id is not None else settings.telegram_chat_id
        self.bot = bot
        if self.bot is None and settings.telegram_bot_token:
            self.bot = Bot(token=settings.telegram_bot_token)

    async def send_listing_alert(self, message: str, payload: dict | None = None) -> bool:
        if not self.bot or not self.chat_id:
            logger.info("Telegram is not configured; skipping listing alert")
            return False

        last_exc: Exception | None = None
        for attempt in range(1, TELEGRAM_SEND_ATTEMPTS + 1):
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    disable_web_page_preview=True,
                    connect_timeout=TELEGRAM_TIMEOUT_SEC,
                    read_timeout=TELEGRAM_TIMEOUT_SEC,
                    write_timeout=TELEGRAM_TIMEOUT_SEC,
                )
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Telegram send_message failed on attempt %s/%s",
                    attempt,
                    TELEGRAM_SEND_ATTEMPTS,
                    exc_info=True,
                )
                if attempt < TELEGRAM_SEND_ATTEMPTS:
                    await asyncio.sleep(TELEGRAM_RETRY_DELAY_SEC)

        if last_exc is not None:
            raise last_exc
        return False
