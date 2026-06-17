from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db import session as db_session_module
from app.db.base import Base
from app.main import create_app
from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis


def _client(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        with Session() as s:
            yield s

    monkeypatch.setattr(settings, "admin_ui_read_key", "read")
    monkeypatch.setattr(settings, "admin_ui_technical_write_key", "tech")
    monkeypatch.setattr(settings, "admin_ui_allow_query_api_key", True)
    app = create_app(admin_ui_enabled=True)
    app.dependency_overrides[db_session_module.get_db] = override_db
    return TestClient(app), Session


def _get(client: TestClient, listing_id: int | str):
    return client.get(f"/api/admin/v1/listings/{listing_id}/workflow", headers={"X-API-Key": "read"})


def _listing(id_: int, external_id: str, **kwargs):
    now = datetime(2026, 1, 1, 12, 0, 0)
    data = {"id": id_, "external_id": external_id, "url": f"https://www.avito.ru/item{id_}", "price": 100.0, "area_m2": 50.0, "published_at": now, "first_seen_at": now, "last_seen_at": now}
    data.update(kwargs)
    return Listing(**data)


def _analysis(id_: int, external_id: str, *, verdict: str | None = "review", status: str = "success", score: float | None = 10.0, days: int = 0):
    return ListingAnalysis(id=id_, listing_external_id=external_id, status=status, profile="p", input_hash=f"h{id_}", score=score, verdict=verdict, created_at=datetime(2026, 1, 1, 12, 0, 0) + timedelta(days=days))


def _seed(Session):
    with Session() as s:
        s.add_all([
            _listing(1, "no-analysis"),
            _listing(2, "missing-area", area_m2=None),
            _listing(3, "missing-price", price=None),
            _listing(4, "freshness", published_at=None, published_label=""),
            _listing(5, "review-high-score"),
            _listing(6, "strong"),
            _listing(7, "weak"),
            _listing(8, "latest-success"),
            _listing(9, "watchlist"),
            _listing(10, "rejected"),
            _listing(11, "closed"),
            _listing(12, "unsafe-url", url="https://example.com/item"),
        ])
        s.add_all([
            _analysis(1, "missing-area", verdict="strong"),
            _analysis(2, "missing-price", verdict="strong"),
            _analysis(3, "freshness", verdict="strong"),
            _analysis(4, "review-high-score", verdict="review", score=99),
            _analysis(5, "strong", verdict="strong", score=80),
            _analysis(6, "weak", verdict="weak", score=1),
            _analysis(7, "latest-success", verdict="strong", status="success", score=50, days=0),
            _analysis(8, "latest-success", verdict="review", status="failed", score=99, days=2),
            _analysis(9, "watchlist", verdict="strong"),
            _analysis(10, "rejected", verdict="strong"),
            _analysis(11, "closed", verdict="strong"),
            _analysis(12, "unsafe-url", verdict="strong"),
        ])
        now = datetime(2026, 1, 2, 12, 0, 0)
        s.add_all([
            HumanReview(id=1, listing_id=9, listing_external_id="watchlist", listing_analysis_id=9, review_context_key="w", watchlist=True, reviewed_at=now),
            HumanReview(id=2, listing_id=10, listing_external_id="rejected", listing_analysis_id=10, review_context_key="r", human_verdict="not_interesting", reviewed_at=now),
            HumanReview(id=3, listing_id=11, listing_external_id="closed", listing_analysis_id=11, review_context_key="c", review_status="closed", reviewed_at=now),
        ])
        s.commit()


def test_workflow_auth_and_errors(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    assert client.get("/api/admin/v1/listings/1/workflow").status_code == 403
    assert client.get("/api/admin/v1/listings/1/workflow", headers={"X-API-Key": "bad"}).status_code == 403
    assert client.get("/api/admin/v1/listings/1/workflow", headers={"X-API-Key": "tech"}).status_code == 403
    assert client.get("/api/admin/v1/listings/1/workflow?api_key=read").status_code == 403
    assert _get(client, 999).json()["error"]["code"] == "not_found"
    assert _get(client, "not-int").json()["error"]["code"] == "validation_error"


def test_workflow_state_derivation_and_actions(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    expected = {
        1: ("analysis_pending", "latest_analysis_missing"),
        2: ("needs_data", "missing_area_m2"),
        3: ("needs_data", "missing_price"),
        4: ("needs_review", "freshness_unknown"),
        5: ("needs_review", "latest_analysis_verdict_review"),
        6: ("ready_for_work", "latest_analysis_verdict_strong"),
        7: ("needs_review", "fallback_needs_review"),
        8: ("ready_for_work", "latest_analysis_verdict_strong"),
        9: ("watchlist", "human_review_watchlist"),
        10: ("rejected", "human_review_rejected"),
        11: ("closed", "human_review_closed"),
    }
    for listing_id, (state, reason) in expected.items():
        data = _get(client, listing_id).json()["data"]
        assert data["schema_version"] == "workflow-state-v1"
        assert data["workflow_state"] == state
        assert reason in data["state_reasons"]
        assert "report_ready" != state
        actions = {a["id"]: a for a in data["allowed_actions"] + data["blocked_actions"]}
        assert set(actions) == {"open_listing", "take_in_work", "request_data", "call_owner", "watchlist", "reject", "generate_memo", "generate_commercial_offer", "export_report", "close"}
        for action_id, action in actions.items():
            assert "execution_endpoint" not in action
            if action_id == "open_listing":
                assert action["implemented"] is True
                assert action["requires_write_endpoint"] is False
            else:
                assert action["implemented"] is False
                assert action["available_now"] is False
                assert action["requires_write_endpoint"] is True
        assert [a["id"] for a in data["allowed_actions"] if a["implemented"] and a["available_now"]] == ["open_listing"]

    unsafe = _get(client, 12).json()["data"]
    open_listing = {a["id"]: a for a in unsafe["allowed_actions"] + unsafe["blocked_actions"]}["open_listing"]
    assert open_listing["available_now"] is False
    assert open_listing["reason"] == "missing_listing_url"


def test_decision_source_workflow_and_no_side_effects(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    models = [Listing, ListingAnalysis, HumanReview, AlertSent, AgentTask, AdminAuditEvent]
    with Session() as s:
        before = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    ds = client.get("/api/admin/v1/listings/6/decision-source", headers={"X-API-Key": "read"}).json()["data"]
    workflow = _get(client, 6).json()["data"]
    assert ds["workflow"] == workflow
    assert ds["workflow"]["workflow_state"] == "ready_for_work"
    def walk_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from walk_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk_keys(child)

    keys = set(walk_keys(ds))
    for forbidden in ["decision_card", "primary_recommendation", "recommendation", "headline", "top_reasons", "top_risks", "next_steps", "missing_data", "readiness", "risk_severity", "risk_visual", "price_position", "facts_json", "result_json", "payload_json", "risks_json", "questions_json", "report_md"]:
        assert forbidden not in keys
    with Session() as s:
        after = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert after == before


def test_meta_exposes_workflow_read_contract(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    data = client.get("/api/admin/v1/meta", headers={"X-API-Key": "read"}).json()["data"]
    assert data["meta_contract_version"] == "v1"
    assert data["workflow_contract_version"] == "workflow-state-v1"
    assert "workflow_state" in data["enums"]
    assert "workflow_action" in data["enums"]
    assert data["capabilities"]["workflow_state_read"] is True
    assert data["capabilities"]["workflow_actions_execute"] is False
