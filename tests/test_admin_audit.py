from sqlalchemy import func, inspect, select

from app.db.base import Base
from app.models.admin_audit_event import AdminAuditEvent
from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from tests.test_admin_ui import _RetryChannel, _add_alert_sent, _add_attempt, _patch_retry_service, create_listing, make_raw_client


ALLOWED_RETRY_METADATA = {
    "reason",
    "retry_result_status",
    "source_attempt_id",
    "created_attempt_id",
    "alert_sent_created",
    "channel",
    "listing_external_id",
    "target_attempt_status",
}


def test_admin_audit_model_registered_and_indexes(monkeypatch):
    assert "admin_audit_events" in Base.metadata.tables
    index_names = {idx.name for idx in AdminAuditEvent.__table__.indexes}
    assert "ix_admin_audit_events_created_at" in index_names
    assert "ix_admin_audit_events_action" in index_names
    assert "ix_admin_audit_events_status" in index_names
    assert "ix_admin_audit_events_target" in index_names

    client, Session = make_raw_client(monkeypatch)
    with Session() as s:
        assert "admin_audit_events" in inspect(s.bind).get_table_names()
    assert client.get("/admin/system", headers={"X-API-Key": "read"}).status_code == 200


def test_manual_retry_success_records_safe_audit_event(monkeypatch):
    calls = []
    _patch_retry_service(monkeypatch, [_RetryChannel("jsonl", True, calls)])
    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True)
    create_listing(Session, external_id="audit-ok", url="https://www.avito.ru/audit-ok?token=secret")
    attempt_id = _add_attempt(Session, listing_external_id="audit-ok", channel="jsonl", status="failed", dedupe_key="jsonl:new:audit-ok")

    resp = client.post(
        f"/admin/alerts/delivery-attempts/{attempt_id}/retry?api_key=query-secret",
        headers={"X-API-Key": "read", "Authorization": "Bearer header-secret", "Cookie": "session=secret", "User-Agent": "raw-agent"},
        data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{attempt_id}", "payload_json": "secret"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        event = s.scalar(select(AdminAuditEvent).where(AdminAuditEvent.action == "alert_delivery_retry"))
        assert event is not None
        assert event.status == "success"
        assert event.target_type == "alert_delivery_attempt"
        assert event.target_id == str(attempt_id)
        assert event.request_method == "POST"
        assert event.request_path == f"/admin/alerts/delivery-attempts/{attempt_id}/retry"
        assert event.ip_hash is None
        assert event.user_agent_hash is None
        assert event.actor_kind == "admin_technical_key"
        assert set(event.metadata_json) <= ALLOWED_RETRY_METADATA
        assert event.metadata_json["retry_result_status"] == "success"
        assert event.metadata_json["alert_sent_created"] is True
        serialized = str(event.metadata_json) + str(event.error_message) + str(event.request_path) + str(event.actor_label)
        for forbidden in ["query-secret", "header-secret", "session=secret", "raw-agent", "admin_technical_write_key", "confirm_action", "payload_json", "avito.ru/audit-ok", "api_key="]:
            assert forbidden not in serialized


def test_manual_retry_non_success_and_blocked_audit_events(monkeypatch):
    _patch_retry_service(monkeypatch, [_RetryChannel("jsonl", False, [])])
    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True)
    create_listing(Session, external_id="audit-skipped")
    skipped_id = _add_attempt(Session, listing_external_id="audit-skipped", channel="jsonl", status="failed", dedupe_key="jsonl:new:audit-skipped")
    resp = client.post(f"/admin/alerts/delivery-attempts/{skipped_id}/retry", headers={"X-API-Key": "read"}, data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{skipped_id}"}, follow_redirects=False)
    assert resp.status_code == 303

    create_listing(Session, external_id="audit-sent")
    sent_id = _add_attempt(Session, listing_external_id="audit-sent", channel="jsonl", status="failed", dedupe_key="jsonl:new:audit-sent")
    _add_alert_sent(Session, listing_external_id="audit-sent", channel="jsonl", dedupe_key="jsonl:new:audit-sent")
    blocked = client.post(f"/admin/alerts/delivery-attempts/{sent_id}/retry", headers={"X-API-Key": "read"}, data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{sent_id}"})
    assert blocked.status_code == 400

    with Session() as s:
        events = s.scalars(select(AdminAuditEvent).order_by(AdminAuditEvent.id)).all()
        assert [e.status for e in events] == ["failed", "blocked"]
        assert events[0].metadata_json["retry_result_status"] == "skipped"
        assert events[1].metadata_json["reason"] == "matching_alert_sent_exists"
        assert all(set(e.metadata_json) <= ALLOWED_RETRY_METADATA for e in events)


def test_audit_failure_does_not_change_retry_behavior(monkeypatch):
    import app.services.admin_audit as audit_service

    calls = []
    _patch_retry_service(monkeypatch, [_RetryChannel("jsonl", True, calls)])
    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True)
    create_listing(Session, external_id="audit-isolated")
    attempt_id = _add_attempt(Session, listing_external_id="audit-isolated", channel="jsonl", status="failed", dedupe_key="jsonl:new:audit-isolated")


    class BadSessionFactory:
        def __call__(self):
            raise RuntimeError("audit failed token=secret")

    monkeypatch.setattr(audit_service, "sessionmaker", lambda *args, **kwargs: BadSessionFactory())
    resp = client.post(f"/admin/alerts/delivery-attempts/{attempt_id}/retry", headers={"X-API-Key": "read"}, data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{attempt_id}"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "retry_success=1" in resp.headers["location"]
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(AlertDeliveryAttempt.dedupe_key == "jsonl:new:audit-isolated")) == 2
        assert s.scalar(select(func.count()).select_from(AlertSent).where(AlertSent.dedupe_key == "jsonl:new:audit-isolated")) == 1
        assert s.scalar(select(func.count()).select_from(AdminAuditEvent)) == 0


def test_admin_system_shows_recent_audit_read_only_and_bounded(monkeypatch):
    client, Session = make_raw_client(monkeypatch)
    with Session() as s:
        for idx in range(25):
            s.add(AdminAuditEvent(action="alert_delivery_retry", status="blocked", target_type="alert_delivery_attempt", target_id=str(idx), metadata_json={"reason": "not_retryable"}))
        s.commit()
        before = s.scalar(select(func.count()).select_from(AdminAuditEvent))
    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Recent admin audit events" in page
    assert "metadata_json" not in page
    assert "api_key=" not in page
    assert "admin_technical_write_key" not in page
    assert page.count("alert_delivery_retry") == 20
    assert "<form" not in page.lower()
    assert client.post("/admin/system", headers={"X-API-Key": "tech"}).status_code in {404, 405}
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(AdminAuditEvent)) == before
