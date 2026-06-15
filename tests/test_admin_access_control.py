from sqlalchemy import func, select

from app.models.admin_audit_event import AdminAuditEvent
from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from app.services import admin_auth
from tests.test_admin_ui import _add_alert_sent, _add_attempt, create_listing, make_raw_client


def _counts(Session):
    with Session() as s:
        return {
            "attempts": s.scalar(select(func.count()).select_from(AlertDeliveryAttempt)),
            "sent": s.scalar(select(func.count()).select_from(AlertSent)),
            "audit": s.scalar(select(func.count()).select_from(AdminAuditEvent)),
        }


def test_read_access_fails_closed_without_explicit_read_key(monkeypatch):
    client, Session = make_raw_client(monkeypatch, read_key="", write_key="write", technical_write_key="tech", api_key="legacy")
    assert client.get("/admin/system", headers={"X-API-Key": "write"}).status_code == 403
    assert client.get("/admin/system", headers={"X-API-Key": "tech"}).status_code == 403
    assert client.get("/admin/system", headers={"X-API-Key": "legacy"}).status_code == 403
    assert _counts(Session)["audit"] == 0


def test_read_only_admin_routes_require_read_key_and_do_not_audit(monkeypatch):
    client, Session = make_raw_client(monkeypatch)
    attempt_id = _add_attempt(Session, status="failed")
    before = _counts(Session)

    assert client.get("/admin/system", headers={"X-API-Key": "read"}).status_code == 200
    assert client.get("/admin/alerts", headers={"X-API-Key": "read"}).status_code == 200
    assert client.get(f"/admin/alerts/delivery-attempts/{attempt_id}", headers={"X-API-Key": "read"}).status_code == 200
    assert client.get("/admin/review-queue", headers={"X-API-Key": "read"}).status_code == 200
    assert client.get("/admin/review-queue", headers={"X-API-Key": "bad"}).status_code == 403
    assert client.get("/admin/alerts", headers={"X-API-Key": "bad"}).status_code == 403
    assert _counts(Session) == before


def test_manual_retry_requires_read_key_even_with_valid_technical_key(monkeypatch):
    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True)
    create_listing(Session, external_id="retry-read-required")
    attempt_id = _add_attempt(Session, listing_external_id="retry-read-required", status="failed", dedupe_key="jsonl:new:retry-read-required")
    before = _counts(Session)

    resp = client.post(
        f"/admin/alerts/delivery-attempts/{attempt_id}/retry",
        data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{attempt_id}"},
    )
    assert resp.status_code == 403
    assert _counts(Session) == before


def test_manual_retry_requires_distinct_configured_technical_form_key(monkeypatch):
    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True, read_key="read", technical_write_key="tech")
    create_listing(Session, external_id="retry-tech-required")
    attempt_id = _add_attempt(Session, listing_external_id="retry-tech-required", status="failed", dedupe_key="jsonl:new:retry-tech-required")
    before = _counts(Session)

    for submitted in (None, "bad", "read"):
        data = {"confirm_action": f"retry_delivery_attempt_{attempt_id}"}
        if submitted is not None:
            data["admin_technical_write_key"] = submitted
        resp = client.post(f"/admin/alerts/delivery-attempts/{attempt_id}/retry", headers={"X-API-Key": "read"}, data=data)
        assert resp.status_code == 403

    # Technical key in URL/header is not accepted as the technical write key.
    resp = client.post(
        f"/admin/alerts/delivery-attempts/{attempt_id}/retry?admin_technical_write_key=tech&technical_key=tech&write_key=tech",
        headers={"X-API-Key": "read"},
        data={"confirm_action": f"retry_delivery_attempt_{attempt_id}"},
    )
    assert resp.status_code == 403
    after = _counts(Session)
    assert after["attempts"] == before["attempts"]
    assert after["sent"] == before["sent"]
    assert after["audit"] == before["audit"] + 4


def test_missing_configured_technical_key_and_disabled_ops_block_without_delivery_mutation(monkeypatch):
    disabled_client, DisabledSession = make_raw_client(monkeypatch, technical_ops_enabled=False, technical_write_key="tech")
    create_listing(DisabledSession, external_id="retry-disabled")
    disabled_id = _add_attempt(DisabledSession, listing_external_id="retry-disabled", status="failed", dedupe_key="jsonl:new:retry-disabled")
    before_disabled = _counts(DisabledSession)
    resp = disabled_client.post(f"/admin/alerts/delivery-attempts/{disabled_id}/retry", headers={"X-API-Key": "read"}, data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{disabled_id}"})
    assert resp.status_code == 403
    after_disabled = _counts(DisabledSession)
    assert after_disabled["attempts"] == before_disabled["attempts"]
    assert after_disabled["sent"] == before_disabled["sent"]
    assert after_disabled["audit"] == before_disabled["audit"] + 1

    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True, technical_write_key="")
    create_listing(Session, external_id="retry-no-tech")
    attempt_id = _add_attempt(Session, listing_external_id="retry-no-tech", status="failed", dedupe_key="jsonl:new:retry-no-tech")
    before = _counts(Session)
    resp = client.post(f"/admin/alerts/delivery-attempts/{attempt_id}/retry", headers={"X-API-Key": "read"}, data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{attempt_id}"})
    assert resp.status_code == 403
    after = _counts(Session)
    assert after["attempts"] == before["attempts"]
    assert after["sent"] == before["sent"]
    assert after["audit"] == before["audit"] + 1


def test_manual_retry_confirmation_block_and_domain_block_audit_safety(monkeypatch):
    client, Session = make_raw_client(monkeypatch, technical_ops_enabled=True)
    create_listing(Session, external_id="retry-confirm")
    attempt_id = _add_attempt(Session, listing_external_id="retry-confirm", status="failed", dedupe_key="jsonl:new:retry-confirm")
    before = _counts(Session)
    resp = client.post(f"/admin/alerts/delivery-attempts/{attempt_id}/retry?api_key=leak", headers={"X-API-Key": "read"}, data={"admin_technical_write_key": "tech", "confirm_action": "wrong", "payload_json": "secret"})
    assert resp.status_code == 400
    after = _counts(Session)
    assert after["attempts"] == before["attempts"]
    assert after["sent"] == before["sent"]
    assert after["audit"] == before["audit"] + 1

    create_listing(Session, external_id="retry-domain-block")
    blocked_id = _add_attempt(Session, listing_external_id="retry-domain-block", status="failed", dedupe_key="jsonl:new:retry-domain-block")
    _add_alert_sent(Session, listing_external_id="retry-domain-block", dedupe_key="jsonl:new:retry-domain-block")
    resp = client.post(f"/admin/alerts/delivery-attempts/{blocked_id}/retry?api_key=leak", headers={"X-API-Key": "read", "Cookie": "secret"}, data={"admin_technical_write_key": "tech", "confirm_action": f"retry_delivery_attempt_{blocked_id}", "payload_json": "secret"})
    assert resp.status_code == 400
    with Session() as s:
        event = s.scalar(select(AdminAuditEvent).where(AdminAuditEvent.target_id == str(blocked_id)))
        assert event.action == "alert_delivery_retry"
        assert event.target_type == "alert_delivery_attempt"
        assert event.status == "blocked"
        assert event.request_path == f"/admin/alerts/delivery-attempts/{blocked_id}/retry"
        serialized = f"{event.metadata_json} {event.request_path} {event.error_message}"
        for forbidden in ["read", "tech", "api_key=", "Cookie", "secret", "admin_technical_write_key", "confirm_action", "payload_json"]:
            assert forbidden not in serialized


def test_admin_auth_helpers_fail_closed_and_use_constant_time_compare(monkeypatch):
    monkeypatch.setattr(admin_auth.settings, "admin_ui_read_key", "read")
    monkeypatch.setattr(admin_auth.settings, "admin_ui_technical_write_key", "tech")
    monkeypatch.setattr(admin_auth.settings, "admin_ui_technical_ops_enabled", True)
    monkeypatch.setattr(admin_auth.secrets, "compare_digest", lambda a, b: a == b)
    assert admin_auth.is_valid_admin_read_key("read") is True
    admin_auth.require_admin_technical_access(read_key_header="read", read_key_query=None, technical_write_key="tech")
    monkeypatch.setattr(admin_auth.settings, "admin_ui_read_key", "")
    assert admin_auth.is_valid_admin_read_key("read") is False
