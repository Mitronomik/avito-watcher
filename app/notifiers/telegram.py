from telegram import Bot
from app.core.config import settings


class TelegramNotifier:
    def __init__(self) -> None:
        self.bot = Bot(token=settings.telegram_bot_token) if settings.telegram_bot_token else None

    async def send_listing_alert(self, text: str) -> None:
        if not self.bot or not settings.telegram_chat_id:
            print("Telegram is not configured:", text)
            return
        await self.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            disable_web_page_preview=False,
        )
