import asyncio
import json
from pathlib import Path

from app.notifiers.composite import CompositeNotifier
from app.notifiers.email import EmailNotifier
from app.notifiers.jsonl_outbox import JsonlOutboxNotifier


class FakeSMTP:
    sent_messages = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, *_args, **_kwargs):
        return None

    def send_message(self, msg):
        self.sent_messages.append(msg)


def test_email_notifier_noop_when_not_configured():
    notifier = EmailNotifier(enabled=False)
    asyncio.run(notifier.send_listing_alert("hello", {}))


def test_email_notifier_sends_with_fake_smtp(monkeypatch):
    import app.notifiers.email as email_module

    FakeSMTP.sent_messages = []
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSMTP)

    notifier = EmailNotifier(
        enabled=True,
        host="smtp.test",
        port=465,
        username="u",
        password="p",
        sender="from@test",
        recipient="to@test",
    )
    payload = {"search_name": "test", "price": 100, "area_m2": 20, "title": "Office"}
    asyncio.run(notifier.send_listing_alert("body", payload))
    assert len(FakeSMTP.sent_messages) == 1


def test_jsonl_outbox_writes_valid_json_line(tmp_path: Path):
    out = tmp_path / "alerts" / "alerts.jsonl"
    notifier = JsonlOutboxNotifier(enabled=True, path=str(out))
    payload = {"external_id": "42", "title": "T", "price": 1, "area_m2": 2}
    asyncio.run(notifier.send_listing_alert("msg", payload))

    line = out.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["external_id"] == "42"


def test_composite_continues_when_one_channel_fails():
    class Ok:
        channel_name = "jsonl"

        async def send_listing_alert(self, message: str, payload: dict):
            return None

    class Bad:
        channel_name = "email"

        async def send_listing_alert(self, message: str, payload: dict):
            raise RuntimeError("fail")

    notifier = CompositeNotifier([Bad(), Ok()])
    sent = asyncio.run(notifier.send_listing_alert("msg", {}))
    assert sent == ["jsonl"]
