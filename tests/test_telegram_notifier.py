import asyncio

import pytest

from app.notifiers import telegram as telegram_module
from app.notifiers.telegram import TelegramNotifier


class FakeBot:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise RuntimeError("temporary telegram failure")


def test_telegram_notifier_successful_send():
    bot = FakeBot()
    notifier = TelegramNotifier(bot=bot, chat_id="123")

    asyncio.run(notifier.send_listing_alert("hello"))

    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == "123"
    assert bot.calls[0]["text"] == "hello"
    assert bot.calls[0]["disable_web_page_preview"] is True
    assert bot.calls[0]["connect_timeout"] == telegram_module.TELEGRAM_TIMEOUT_SEC
    assert bot.calls[0]["read_timeout"] == telegram_module.TELEGRAM_TIMEOUT_SEC
    assert bot.calls[0]["write_timeout"] == telegram_module.TELEGRAM_TIMEOUT_SEC


def test_telegram_notifier_retries_after_temporary_failure(monkeypatch):
    monkeypatch.setattr(telegram_module.asyncio, "sleep", lambda _delay: _noop())
    bot = FakeBot(failures=1)
    notifier = TelegramNotifier(bot=bot, chat_id="123")

    asyncio.run(notifier.send_listing_alert("hello"))

    assert len(bot.calls) == 2


def test_telegram_notifier_final_failure_raises(monkeypatch):
    monkeypatch.setattr(telegram_module.asyncio, "sleep", lambda _delay: _noop())
    bot = FakeBot(failures=3)
    notifier = TelegramNotifier(bot=bot, chat_id="123")

    with pytest.raises(RuntimeError, match="temporary telegram failure"):
        asyncio.run(notifier.send_listing_alert("hello"))

    assert len(bot.calls) == 3


def test_telegram_notifier_skips_when_not_configured():
    bot = FakeBot()
    notifier = TelegramNotifier(bot=bot, chat_id="")

    asyncio.run(notifier.send_listing_alert("hello"))

    assert bot.calls == []


async def _noop():
    return None
