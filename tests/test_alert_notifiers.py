import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.notifiers.composite import CompositeNotifier
from app.notifiers.email import EmailNotifier
from app.notifiers.google_sheets_webhook import GoogleSheetsWebhookNotifier
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




def test_jsonl_outbox_maps_summary_to_llm_summary(tmp_path: Path):
    out = tmp_path / "alerts" / "alerts.jsonl"
    notifier = JsonlOutboxNotifier(enabled=True, path=str(out))
    payload = {"external_id": "42", "summary": "LLM short summary"}
    asyncio.run(notifier.send_listing_alert("msg", payload))

    line = out.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["llm_summary"] == "LLM short summary"
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


def test_google_sheets_webhook_noop_when_disabled():
    notifier = GoogleSheetsWebhookNotifier(enabled=False, webhook_url="https://example.com")
    asyncio.run(notifier.send_listing_alert("hello", {}))


def test_google_sheets_webhook_posts_expected_json(monkeypatch):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import app.notifiers.google_sheets_webhook as module

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    notifier = GoogleSheetsWebhookNotifier(
        enabled=True,
        webhook_url="https://example.com/webhook",
        secret="top",
        timeout_sec=5,
    )
    payload = {"search_name": "s", "external_id": "1", "title": "T", "price": 10, "area_m2": 20, "rooms": "2", "address": "A", "published_label": "today", "published_at": "2025-01-01T00:00:00", "url": "u", "summary": "sum", "score": 90, "tags": ["hot"]}
    sent = asyncio.run(notifier.send_listing_alert("msg", payload))

    assert captured["url"] == "https://example.com/webhook"
    assert captured["body"]["secret"] == "top"
    assert captured["body"]["external_id"] == "1"
    assert captured["body"]["message"] == "msg"
    assert "sent_at" in captured["body"]
    assert sent is True


def test_google_sheets_webhook_raises_on_non_2xx(monkeypatch):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": True})

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import app.notifiers.google_sheets_webhook as module

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    notifier = GoogleSheetsWebhookNotifier(enabled=True, webhook_url="https://example.com")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(notifier.send_listing_alert("msg", {}))


def test_google_sheets_webhook_ok_false_returns_false_and_logs_warning(monkeypatch, caplog):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "Unauthorized"})

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import app.notifiers.google_sheets_webhook as module

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    notifier = GoogleSheetsWebhookNotifier(
        enabled=True,
        webhook_url="https://example.com",
        secret="webhook-secret",
    )
    with caplog.at_level("WARNING"):
        sent = asyncio.run(notifier.send_listing_alert("msg", {"password": "hidden"}))

    assert sent is False
    assert "ok=false" in caplog.text
    assert "Unauthorized" in caplog.text
    assert "webhook-secret" not in caplog.text
    assert "hidden" not in caplog.text


def test_google_sheets_webhook_non_json_returns_false_and_logs_compact_warning(monkeypatch, caplog):
    html_body = "<html><body>Drive Error secret=abc123 password=hidden</body></html>"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html_body,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import app.notifiers.google_sheets_webhook as module

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    notifier = GoogleSheetsWebhookNotifier(
        enabled=True,
        webhook_url="https://example.com",
        secret="webhook-secret",
    )
    with caplog.at_level("WARNING"):
        sent = asyncio.run(notifier.send_listing_alert("msg", {"password": "hidden"}))

    assert sent is False
    assert "non-JSON" in caplog.text
    assert "text/html" in caplog.text
    assert html_body not in caplog.text
    assert "webhook-secret" not in caplog.text
    assert "hidden" not in caplog.text


def test_composite_does_not_mark_google_sheets_success_when_disabled():
    notifier = CompositeNotifier([GoogleSheetsWebhookNotifier(enabled=False, webhook_url="https://example.com")])
    sent = asyncio.run(notifier.send_listing_alert("msg", {}))
    assert sent == []
