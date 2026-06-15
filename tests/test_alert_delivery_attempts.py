import asyncio
import re
from datetime import datetime

from sqlalchemy import inspect, select

from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from app.parsers.schemas import ListingCard
from app.repositories.alert_delivery_attempt_repository import AlertDeliveryAttemptRepository
from app.repositories.alert_repository import AlertRepository
from app.services.alert_delivery_attempts import (
    compute_alert_payload_hash,
    sanitize_alert_delivery_error,
)
from app.services.monitor_service import MonitorService


class DummyParser:
    pass


class DummyScorer:
    pass


def make_card(external_id: str = "42") -> ListingCard:
    return ListingCard(
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        title="Listing",
        price=100,
        address="Address",
        raw={"external_id": external_id},
    )


class Channel:
    def __init__(self, name: str, result=True):
        self.channel_name = name
        self.result = result
        self.calls = 0

    async def send_listing_alert(self, message: str, payload: dict | None = None):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class MultiNotifier:
    def __init__(self, *channels: Channel):
        self.channels = list(channels)


def deliver(db_session, notifier, card=None, pending_channels=None):
    service = MonitorService(parser=DummyParser(), scorer=DummyScorer(), notifier=notifier)
    return asyncio.run(
        service._deliver_pending_alerts(
            AlertRepository(db_session),
            card or make_card(),
            "message",
            {"b": 2, "a": {"nested": True}},
            pending_channels=pending_channels,
        )
    )


def attempts(db_session):
    return db_session.scalars(select(AlertDeliveryAttempt).order_by(AlertDeliveryAttempt.id)).all()


def test_alert_delivery_attempt_model_registered_and_basic_create_read(db_session):
    assert "alert_delivery_attempts" in inspect(db_session.bind).get_table_names()
    repo = AlertDeliveryAttemptRepository(db_session)
    row = repo.create_attempt(
        listing_external_id="1",
        channel="jsonl",
        dedupe_key="jsonl:new:1",
        payload_hash="0" * 64,
        status="success",
        attempt_count=1,
        sent_at=datetime(2026, 6, 15),
    )
    db_session.commit()

    loaded = db_session.get(AlertDeliveryAttempt, row.id)
    assert loaded is not None
    assert loaded.channel == "jsonl"
    assert loaded.status == "success"


def test_success_attempt_creates_attempt_and_alert_sent(db_session):
    result = deliver(db_session, MultiNotifier(Channel("jsonl", True)), pending_channels=["jsonl"])
    db_session.commit()

    [attempt] = attempts(db_session)
    assert result["successful"] == ["jsonl"]
    assert attempt.status == "success"
    assert attempt.sent_at is not None
    assert attempt.next_retry_at is None
    assert attempt.attempt_count == 1
    assert re.fullmatch(r"[0-9a-f]{64}", attempt.payload_hash)
    assert db_session.scalar(select(AlertSent).where(AlertSent.dedupe_key == "jsonl:new:42")) is not None


def test_failed_attempt_is_persisted_without_alert_sent_and_sanitizes_error(db_session):
    result = deliver(
        db_session,
        MultiNotifier(Channel("google_sheets", RuntimeError("api_key=secret Authorization: Bearer token"))),
        pending_channels=["google_sheets"],
    )
    db_session.commit()

    [attempt] = attempts(db_session)
    assert result["failed"] == ["google_sheets"]
    assert attempt.status == "failed"
    assert attempt.sent_at is None
    assert attempt.next_retry_at is None
    assert "RuntimeError" in attempt.last_error
    assert "secret" not in attempt.last_error
    assert "token" not in attempt.last_error
    assert db_session.scalar(select(AlertSent)) is None


def test_skipped_attempt_is_persisted_without_alert_sent(db_session):
    result = deliver(db_session, MultiNotifier(Channel("email", False)), pending_channels=["email"])
    db_session.commit()

    [attempt] = attempts(db_session)
    assert result["skipped"] == ["email"]
    assert attempt.status == "skipped"
    assert attempt.sent_at is None
    assert attempt.next_retry_at is None
    assert db_session.scalar(select(AlertSent)) is None


def test_unknown_attempt_is_persisted_without_alert_sent(db_session):
    result = deliver(db_session, MultiNotifier(Channel("email", {"unexpected": True})), pending_channels=["email"])
    db_session.commit()

    [attempt] = attempts(db_session)
    assert result["unknown"] == ["email"]
    assert attempt.status == "unknown"
    assert attempt.sent_at is None
    assert attempt.next_retry_at is None
    assert db_session.scalar(select(AlertSent)) is None


def test_multi_channel_delivery_attempts_are_isolated(db_session):
    result = deliver(
        db_session,
        MultiNotifier(
            Channel("jsonl", True),
            Channel("google_sheets", RuntimeError("simulated google sheets failure")),
            Channel("email", False),
        ),
        pending_channels=["jsonl", "google_sheets", "email"],
    )
    db_session.commit()

    rows = attempts(db_session)
    assert {row.channel: row.status for row in rows} == {
        "jsonl": "success",
        "google_sheets": "failed",
        "email": "skipped",
    }
    assert result["attempted"] == ["jsonl", "google_sheets", "email"]
    assert result["successful"] == ["jsonl"]
    assert result["failed"] == ["google_sheets"]
    assert result["skipped"] == ["email"]
    assert db_session.scalars(select(AlertSent)).all()[0].channel == "jsonl"


def test_existing_alert_sent_channel_is_not_pending_and_creates_no_attempt(db_session):
    repo = AlertRepository(db_session)
    repo.create(listing_external_id="42", dedupe_key="jsonl:new:42", channel="jsonl")
    db_session.commit()
    channel = Channel("jsonl", True)

    service = MonitorService(parser=DummyParser(), scorer=DummyScorer(), notifier=MultiNotifier(channel))
    pending = service._pending_alert_channels(AlertRepository(db_session), make_card())
    result = asyncio.run(
        service._deliver_pending_alerts(
            AlertRepository(db_session), make_card(), "message", {"x": 1}, pending_channels=pending
        )
    )

    assert pending == []
    assert result["attempted"] == []
    assert attempts(db_session) == []
    assert channel.calls == 0


def test_attempt_count_is_ordinal_per_dedupe_key_and_channel(db_session):
    repo = AlertDeliveryAttemptRepository(db_session)
    counts = []
    for _ in range(3):
        count = repo.next_attempt_count(dedupe_key="email:new:1", channel="email")
        counts.append(count)
        repo.create_attempt(
            listing_external_id="1",
            channel="email",
            dedupe_key="email:new:1",
            payload_hash="1" * 64,
            status="failed",
            attempt_count=count,
        )
    assert counts == [1, 2, 3]


def test_payload_hash_is_stable_canonical_and_payload_only():
    first = compute_alert_payload_hash({"b": 2, "a": {"x": 1}})
    second = compute_alert_payload_hash({"a": {"x": 1}, "b": 2})
    different = compute_alert_payload_hash({"a": {"x": 2}, "b": 2})

    assert first == second
    assert first != different
    assert "x" not in first
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert compute_alert_payload_hash({"payload": "same"}) == compute_alert_payload_hash({"payload": "same"})


def test_error_sanitizer_redacts_secrets_urls_and_traceback_text():
    raw = (
        "Traceback line\napi_key=secret token=secret Authorization: Bearer secret "
        "smtp_password=secret webhook=https://host/path?secret=secret "
        "url=https://host/path?secret=secret&ok=value "
        "telegram token=secret cookie=secret"
    )
    sanitized = sanitize_alert_delivery_error(raw)

    assert "\n" not in sanitized
    assert "=secret" not in sanitized
    assert "Bearer secret" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "ok=value" in sanitized
    assert len(sanitized) <= 1000


def test_baseline_no_delivery_creates_no_attempts(db_session):
    # Directly exercises PR20a no-attempt semantics: an empty pending set means baseline/no-delivery
    # paths do not write either the success dedupe table or the delivery attempt ledger.
    result = deliver(db_session, MultiNotifier(Channel("jsonl", True)), pending_channels=[])

    assert result["attempted"] == []
    assert attempts(db_session) == []
    assert db_session.scalar(select(AlertSent)) is None
