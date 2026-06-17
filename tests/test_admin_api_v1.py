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
    assert meta_body["data"] == {"api_version": "admin-v1", "service": "avito-watcher", "status": "ok"}
    assert not (
        {
            "permissions",
            "enums",
            "labels",
            "roles",
            "role_matrix",
            "errors",
            "workflow_actions",
            "capabilities",
            "technical_actions",
            "domain_endpoints",
        }
        & set(meta_body["data"])
    )


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
    assert not any(path.startswith("/api/admin/v1/list") for path in paths)
    assert not any(path.startswith("/api/admin/v1/review") for path in paths)
    assert not any(path.startswith("/api/admin/v1/evidence") for path in paths)
    assert not any(getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"} for route in client.app.routes if route.path.startswith("/api/admin/v1"))
