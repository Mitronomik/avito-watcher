from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func

from app.api.admin_v1.ordering import parse_ordering
from app.api.admin_v1.pagination import DEFAULT_LIMIT, MAX_LIMIT, parse_pagination
from app.api.admin_v1.redaction import REDACTED, redact_api_response
from app.core.config import settings
from app.main import create_app
from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.listing_analysis import ListingAnalysis
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun
from app.models.search_job import SearchJob


def _client(monkeypatch, *, read_key: str = "read", technical_key: str = "tech") -> TestClient:
    monkeypatch.setattr(settings, "admin_ui_read_key", read_key)
    monkeypatch.setattr(settings, "admin_ui_technical_write_key", technical_key)
    monkeypatch.setattr(settings, "admin_ui_allow_query_api_key", True)
    return TestClient(create_app(admin_ui_enabled=True))


def _get_ok(client: TestClient, path: str = "/api/admin/v1/status"):
    return client.get(path, headers={"X-API-Key": "read"})


def test_admin_api_auth_fails_closed_and_rejects_non_read_transports(monkeypatch):
    client = _client(monkeypatch, read_key="")
    body = client.get("/api/admin/v1/status", headers={"X-API-Key": "read"}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "forbidden"

    client = _client(monkeypatch)
    for response in [
        client.get("/api/admin/v1/status"),
        client.get("/api/admin/v1/status", headers={"X-API-Key": "bad"}),
        client.get("/api/admin/v1/status", headers={"X-API-Key": "tech"}),
        client.get("/api/admin/v1/status?api_key=read"),
        client.get("/api/admin/v1/status", headers={"Authorization": "Bearer read"}),
    ]:
        assert response.status_code == 403
        assert response.json()["ok"] is False
        assert response.json()["error"]["code"] == "forbidden"

    assert _get_ok(client).status_code == 200




def test_admin_api_meta_contract_shape_permissions_and_determinism(monkeypatch):
    from app.api.admin_v1.meta_contract import (
        META_CONTRACT_VERSION,
        PERMISSION_ADMIN_HUMAN_REVIEW_WRITE,
        PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE,
        PERMISSION_API_META_READ,
        PERMISSION_API_STATUS_READ,
        PERMISSION_IDS,
    )

    client = _client(monkeypatch)
    first = _get_ok(client, "/api/admin/v1/meta").json()
    second = _get_ok(client, "/api/admin/v1/meta").json()
    data = first["data"]
    assert first["ok"] is True
    assert data["api_version"] == "admin-v1"
    assert data["meta_contract_version"] == META_CONTRACT_VERSION == "v1"
    assert data["service"] == "avito-watcher"
    assert data["status"] == "ok"
    assert set(data["capabilities"]) == {"admin_api_v1", "read_api", "write_api", "technical_api_actions", "decision_card", "risk_attention", "readiness_checklist", "report_export", "workflow_state_read", "workflow_actions_execute"}
    assert data["capabilities"]["read_api"] is True
    assert data["capabilities"]["write_api"] is False
    assert data["capabilities"]["technical_api_actions"] is False
    assert [role["id"] for role in data["roles"]] == ["reader", "reviewer", "technical"]
    assert list(data["permissions"]) == list(PERMISSION_IDS)
    assert data["permissions"][PERMISSION_API_STATUS_READ]["available_now"] is True
    assert data["permissions"][PERMISSION_API_META_READ]["implemented"] is True
    assert data["permissions"][PERMISSION_ADMIN_HUMAN_REVIEW_WRITE]["implemented"] is False
    assert data["permissions"][PERMISSION_ADMIN_HUMAN_REVIEW_WRITE]["available_now"] is False
    assert data["permissions"][PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE]["roles"]["technical"] is True
    assert data["permissions"][PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE]["available_now"] is False
    assert {"review_status", "human_verdict", "next_action", "outcome_status", "agent_task_status", "source_type", "verification_status", "workflow_state", "workflow_action", "decision_recommendation", "risk_category", "risk_severity", "readiness_status", "readiness_item_status", "readiness_group", "readiness_item_id"} == set(data["enums"])
    assert "success" in {item["value"] for item in data["enums"]["agent_task_status"]["values"]}
    assert "succeeded" not in {item["value"] for item in data["enums"]["agent_task_status"]["values"]}
    for enum in data["enums"].values():
        assert enum["unknown_value"]["display"] == "fallback"
        assert set(enum["unknown_value"]["label"]) == {"ru", "en"}
        for item in enum["values"]:
            assert set(item["label"]) == {"ru", "en"}
    assert data["legacy_labels"]["sent_to_expert"]["ru"] == "Сформировать экспертное заключение системы"
    assert data["legacy_labels"]["sent_to_expert"]["en"] == "Prepare system expert memo"
    assert "Отправить эксперту" not in str(data)
    assert "Send to expert" not in str(data)
    assert first["meta"]["generated_at"] != ""
    first_static = dict(first)
    second_static = dict(second)
    first_static["meta"] = {"api_version": first["meta"]["api_version"]}
    second_static["meta"] = {"api_version": second["meta"]["api_version"]}
    assert first_static == second_static


def test_admin_api_meta_contract_errors_capabilities_and_secret_safety(monkeypatch):
    client = _client(monkeypatch)
    response = _get_ok(client, "/api/admin/v1/meta")
    data = response.json()["data"]
    assert list(data["errors"]) == ["unauthorized", "forbidden", "not_found", "validation_error", "pagination_limit_exceeded", "internal_error"]
    for code, error in data["errors"].items():
        assert error["code"] == code
        assert isinstance(error["http_status"], int)
        assert set(error["label"]) == {"ru", "en"}
        assert set(error["description"]) == {"ru", "en"}
        assert isinstance(error["retryable"], bool)
    serialized = response.text
    for forbidden in [
        "ADMIN_UI_READ_KEY", "ADMIN_UI_TECHNICAL_WRITE_KEY", "admin_technical_write_key",
        "GOOGLE_SHEETS_WEBHOOK_URL", "GOOGLE_SHEETS_WEBHOOK_SECRET", "OPENAI_API_KEY",
        "LLM_API_KEY", "TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD", "DATABASE_URL", "POSTGRES_PASSWORD",
        "technical_key_exists", "read_key_exists", "webhook_url", "provider_config", "stacktrace", "traceback",
    ]:
        assert forbidden not in serialized
    openapi = client.get("/openapi.json").text
    assert "ADMIN_UI_READ_KEY" not in openapi
    assert "ADMIN_UI_TECHNICAL_WRITE_KEY" not in openapi
    assert "/api/admin/v1/alerts" not in openapi
    assert "/api/admin/v1/evidence" not in openapi


def test_admin_api_meta_contract_uses_no_db_dependency(monkeypatch):
    from app.db import session as db_session_module

    def fail_session(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("meta must not require a DB session")

    monkeypatch.setattr(db_session_module, "SessionLocal", fail_session)
    client = _client(monkeypatch)
    assert _get_ok(client, "/api/admin/v1/meta").status_code == 200


def test_admin_api_success_and_minimal_endpoint_envelopes(monkeypatch):
    client = _client(monkeypatch)
    status_body = _get_ok(client).json()
    assert status_body["ok"] is True
    assert status_body["data"] == {"status": "ok", "service": "avito-watcher", "api": "admin-v1"}
    assert status_body["meta"]["api_version"] == "admin-v1"
    assert "generated_at" in status_body["meta"]
    assert not ({"settings", "env", "database", "worker", "migrations", "provider", "technical_actions"} & set(status_body["data"]))

    meta_body = _get_ok(client, "/api/admin/v1/meta").json()
    assert meta_body["ok"] is True
    assert meta_body["data"]["api_version"] == "admin-v1"
    assert meta_body["data"]["meta_contract_version"] == "v1"
    assert meta_body["data"]["service"] == "avito-watcher"
    assert meta_body["data"]["status"] == "ok"
    assert {"roles", "permissions", "enums", "labels", "legacy_labels", "errors", "capabilities"} <= set(meta_body["data"])


def test_admin_api_error_envelope_scoped_and_safe(monkeypatch):
    client = _client(monkeypatch)
    body = client.get("/api/admin/v1/missing", headers={"X-API-Key": "read"}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"
    serialized = str(body).lower()
    assert "traceback" not in serialized
    assert "read" not in serialized

    html_response = client.get("/admin/system", headers={"X-API-Key": "bad"})
    assert html_response.status_code == 403
    assert html_response.headers["content-type"].startswith("application/json")
    assert "ok" not in html_response.json()


def test_admin_api_does_not_install_global_exception_catch_all(monkeypatch):
    client = _client(monkeypatch)
    assert Exception not in client.app.exception_handlers
    assert client.get("/admin/system", headers={"X-API-Key": "bad"}).status_code == 403


def test_redaction_recurses_case_insensitive_urls_and_preserves_original():
    original = {
        "Api_Key": "secret",
        "nested": {"token": "secret", "url": "https://example.test/hook?token=abc&ok=1"},
        "items": [{"COOKIE": "secret"}, "https://example.test/path?user_content_key=abc"],
    }
    redacted = redact_api_response(original)
    assert redacted["Api_Key"] == REDACTED
    assert redacted["nested"]["token"] == REDACTED
    assert "token=%5BREDACTED%5D" in redacted["nested"]["url"]
    assert redacted["items"][0]["COOKIE"] == REDACTED
    assert "user_content_key=%5BREDACTED%5D" in redacted["items"][1]
    assert original["Api_Key"] == "secret"
    assert redact_api_response(123) == 123


def test_pagination_helper_bounds_and_meta():
    assert parse_pagination().limit == DEFAULT_LIMIT
    assert parse_pagination().offset == 0
    assert parse_pagination(limit=MAX_LIMIT, offset=2).meta(has_more=True) == {
        "pagination": {"limit": MAX_LIMIT, "offset": 2, "has_more": True}
    }
    for kwargs in [{"limit": -1}, {"offset": -1}, {"limit": MAX_LIMIT + 1}]:
        try:
            parse_pagination(**kwargs)
        except HTTPException as exc:
            assert exc.status_code in {400, 422}
        else:  # pragma: no cover
            raise AssertionError("pagination should reject invalid input")


def test_ordering_helper_allowlist_and_direction():
    allowed = {"created_at": object(), "price": object()}
    default = parse_ordering(order_by=None, order_dir=None, allowed_fields=allowed, default_field="created_at")
    assert (default.field, default.direction) == ("created_at", "desc")
    assert parse_ordering(order_by="price", order_dir="asc", allowed_fields=allowed, default_field="created_at").field == "price"
    for kwargs in [
        {"order_by": "price;drop table listings", "order_dir": "asc"},
        {"order_by": "price", "order_dir": "sideways"},
    ]:
        try:
            parse_ordering(**kwargs, allowed_fields=allowed, default_field="created_at")
        except HTTPException as exc:
            assert exc.status_code == 422
        else:  # pragma: no cover
            raise AssertionError("ordering should reject invalid input")


def test_status_and_meta_have_no_db_side_effects(monkeypatch, db_session):
    client = _client(monkeypatch)
    before = {
        model: db_session.scalar(func.count(model.id))
        for model in [AdminAuditEvent, AlertSent, AgentTask, ListingAnalysis, MarketEvidenceItem, MarketResearchRun, SearchJob]
    }
    assert _get_ok(client).status_code == 200
    assert _get_ok(client, "/api/admin/v1/meta").status_code == 200
    after = {model: db_session.scalar(func.count(model.id)) for model in before}
    assert after == before


def test_scope_regression_routes_and_no_migration(monkeypatch):
    client = _client(monkeypatch)
    paths = {route.path for route in client.app.routes}
    assert "/api/admin/v1/status" in paths
    assert "/api/admin/v1/meta" in paths
    assert "/api/admin/v1/listings" in paths
    assert "/api/admin/v1/listings/{listing_id}" in paths
    assert "/api/admin/v1/listings/{listing_id}/workflow" in paths
    assert "/api/admin/v1/listings/{listing_id}/decision-source" in paths
    assert "/api/admin/v1/listings/{listing_id}/decision-card" in paths
    assert "/api/admin/v1/listings/{listing_id}/risk-attention" in paths
    assert "/api/admin/v1/review-queue" in paths
    assert not any(path.startswith("/api/admin/v1/evidence") for path in paths)
    assert not any(getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"} for route in client.app.routes if route.path.startswith("/api/admin/v1"))
