from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db import session as db_session_module
from app.main import create_app
from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.market_evidence import MarketEvidenceItem


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


def _seed(Session):
    now = datetime(2026, 1, 1, 12, 0, 0)
    with Session() as s:
        l1 = Listing(id=1, external_id="e1", url="https://www.avito.ru/item?token=secret", title="A", price=None, area_m2=None, address="Adr", published_at=None, first_seen_at=now, last_seen_at=now)
        l2 = Listing(id=2, external_id="e2", url="https://www.avito.ru/item2", title="B", price=100, area_m2=50, address="Adr2", published_at=now, first_seen_at=now, last_seen_at=now)
        l3 = Listing(id=3, external_id="e3", url="https://www.avito.ru/item3", title="C", price=200, area_m2=60, address="Adr3", published_at=now + timedelta(days=1), first_seen_at=now, last_seen_at=now)
        s.add_all([l1, l2, l3])
        s.add_all([
            ListingAnalysis(id=1, listing_external_id="e2", status="failed", profile="p", input_hash="f", score=99, verdict="strong", created_at=now + timedelta(days=5)),
            ListingAnalysis(id=2, listing_external_id="e2", status="success", profile="p", input_hash="a", score=10, verdict="reject", created_at=now),
            ListingAnalysis(id=3, listing_external_id="e2", status="success", profile="p", input_hash="b", score=80, verdict="review", created_at=now + timedelta(days=1)),
            ListingAnalysis(id=4, listing_external_id="e3", status="success", profile="p", input_hash="c", score=70, verdict="review", created_at=now + timedelta(days=1)),
        ])
        s.add(HumanReview(listing_id=2, listing_external_id="e2", listing_analysis_id=3, review_context_key="ctx", review_status="needs_review", human_verdict="interesting", reviewer="operator", reviewed_at=now))
        s.commit()


def _get(client, path):
    return client.get(path, headers={"X-API-Key": "read"})


def test_listings_auth_pagination_ordering_redaction_and_latest_analysis(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    for response in [client.get("/api/admin/v1/listings"), client.get("/api/admin/v1/listings?api_key=read"), client.get("/api/admin/v1/listings", headers={"X-API-Key": "tech"})]:
        assert response.status_code == 403
        assert response.json()["ok"] is False

    body = _get(client, "/api/admin/v1/listings?limit=2&offset=0&order_by=published_at&order_dir=asc").json()
    assert body["ok"] is True
    assert body["meta"]["api_version"] == "admin-v1"
    assert body["meta"]["pagination"] == {"limit": 2, "offset": 0, "has_more": True}
    items = body["data"]["items"]
    assert [i["id"] for i in items] == [2, 3]  # nulls last, direction-aware id tiebreak among non-nulls
    desc_tie = _get(client, "/api/admin/v1/listings?limit=3&order_by=first_seen_at&order_dir=desc").json()["data"]["items"]
    assert [i["id"] for i in desc_tie] == [3, 2, 1]
    assert items[0]["schema_version"] == "listing-summary-v1"
    assert items[0]["latest_analysis"]["id"] == 3
    text = str(body)
    for forbidden in ["facts_json", "result_json", "payload_json", "risks_json", "questions_json", "report_md", "secret"]:
        assert forbidden not in text

    assert _get(client, "/api/admin/v1/listings?order_by=title").status_code == 422
    assert _get(client, "/api/admin/v1/listings?order_dir=sideways").status_code == 422
    assert _get(client, "/api/admin/v1/listings?unknown=1").status_code == 422
    assert _get(client, "/api/admin/v1/listings?limit=101").json()["error"]["code"] == "pagination_limit_exceeded"


def test_listing_detail_and_decision_source_safe_boundaries(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    detail = _get(client, "/api/admin/v1/listings/2").json()["data"]
    assert detail["schema_version"] == "listing-detail-v1"
    assert detail["latest_analysis"]["id"] == 3
    assert detail["latest_human_review"] == {"id": 1, "status": "needs_review", "human_verdict": "interesting", "reviewed_at": "2026-01-01T12:00:00", "reviewed_by_label": "operator"}
    assert _get(client, "/api/admin/v1/listings/999").json()["error"]["code"] == "not_found"
    assert _get(client, "/api/admin/v1/listings/not-int").json()["error"]["code"] == "validation_error"

    ds = _get(client, "/api/admin/v1/listings/2/decision-source").json()["data"]
    assert ds["schema_version"] == "decision-source-v1"
    assert "decision_card_not_implemented_in_pr31" in ds["limitations"]
    assert "workflow_state_not_implemented_in_pr31" in ds["limitations"]
    for forbidden_key in ["decision_card", "primary_recommendation", "headline", "top_reasons", "top_risks", "next_steps", "missing_data", "readiness_checklist", "workflow_state", "allowed_actions", "facts_json", "result_json", "risks_json", "questions_json", "report_md"]:
        assert forbidden_key not in ds
        assert forbidden_key not in ds.get("listing", {})


def test_review_queue_and_no_side_effects(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    with Session() as s:
        before = {m: s.scalar(func.count(m.id)) for m in [Listing, ListingAnalysis, HumanReview, AlertSent, AgentTask, AdminAuditEvent, MarketEvidenceItem]}
    body = _get(client, "/api/admin/v1/review-queue?limit=1&offset=0&profile=p").json()
    assert body["ok"] is True
    assert body["meta"]["pagination"]["limit"] == 1
    assert body["data"]["items"][0]["schema_version"] == "review-queue-item-v1"
    assert body["data"]["items"][0]["review"]["queue_status"] == "needs_review"
    text = str(body)
    for forbidden in ["workflow_state", "allowed_actions", "risk_severity", "facts_json", "payload_json", "risks_json", "report_md"]:
        assert forbidden not in text
    strong = _get(client, "/api/admin/v1/review-queue?limit=1&offset=0&profile=p&verdict=strong").json()
    assert [item["listing"]["id"] for item in strong["data"]["items"]] == [2]
    assert strong["meta"]["pagination"]["has_more"] is False

    score_asc = _get(client, "/api/admin/v1/review-queue?limit=1&offset=0&profile=p&order_by=score&order_dir=asc").json()
    assert [item["listing"]["id"] for item in score_asc["data"]["items"]] == [3]
    assert score_asc["meta"]["pagination"]["has_more"] is True
    score_desc = _get(client, "/api/admin/v1/review-queue?limit=1&offset=0&profile=p&order_by=score&order_dir=desc").json()
    assert [item["listing"]["id"] for item in score_desc["data"]["items"]] == [2]

    assert _get(client, "/api/admin/v1/review-queue?order_by=bad").status_code == 422
    assert _get(client, "/api/admin/v1/review-queue?unknown=1").status_code == 422
    with Session() as s:
        after = {m: s.scalar(func.count(m.id)) for m in before}
    assert after == before
