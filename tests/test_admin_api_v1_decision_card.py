from __future__ import annotations

from sqlalchemy import func, select

from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from tests.test_admin_api_v1_workflow import _client, _get as _workflow_get, _seed


def _card(client, listing_id, key="read"):
    return client.get(f"/api/admin/v1/listings/{listing_id}/decision-card", headers={"X-API-Key": key})


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _walk_text(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_text(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_text(child)


def test_decision_card_auth_errors_and_source_ref(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    assert client.get("/api/admin/v1/listings/1/decision-card").status_code == 403
    assert _card(client, 1, "bad").status_code == 403
    assert _card(client, 1, "tech").status_code == 403
    assert client.get("/api/admin/v1/listings/1/decision-card?api_key=read").status_code == 403
    assert _card(client, 999).json()["error"]["code"] == "not_found"
    assert _card(client, "not-int").json()["error"]["code"] == "validation_error"

    source = client.get("/api/admin/v1/listings/6/decision-source", headers={"X-API-Key": "read"}).json()["data"]
    assert source["available_sections"]["decision_card"] is True
    assert source["decision_card_ref"] == {"route_name": "admin_api_v1_decision_card", "listing_id": 6, "schema_version": "decision-card-v1"}


def test_decision_card_recommendations_derive_from_workflow(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    expected = {1: "analysis_pending", 2: "needs_data", 3: "needs_data", 4: "insufficient_evidence", 5: "insufficient_evidence", 6: "take_in_work", 7: "insufficient_evidence", 9: "watchlist", 10: "reject"}
    for listing_id, code in expected.items():
        card = _card(client, listing_id).json()["data"]
        workflow = _workflow_get(client, listing_id).json()["data"]
        assert card["workflow"] == workflow
        assert card["recommendation_scope"] == "internal_workflow"
        assert card["primary_recommendation"]["code"] == code
        assert card["primary_recommendation"]["confidence"] in {"high", "medium", "low", "unknown"}
    high_score_review = _card(client, 5).json()["data"]
    assert high_score_review["workflow"]["workflow_state"] == "needs_review"
    assert high_score_review["primary_recommendation"]["code"] != "take_in_work"


def test_decision_card_limits_boundaries_hashes_and_no_side_effects(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    models = [Listing, ListingAnalysis, HumanReview, AlertSent, AgentTask, AdminAuditEvent]
    with Session() as s:
        before = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    body1 = _card(client, 6).json()
    body2 = _card(client, 6).json()
    card = body1["data"]
    assert card["schema_version"] == "decision-card-v1"
    assert card["decision_card_model_version"] == "decision-card-v1"
    assert card["decision_card_template_version"] == "decision-card-templates-v1"
    assert card["decision_card_policy_version"] == "decision-card-policy-v1"
    assert len(card["top_reasons"]) <= 3
    assert len(card["top_risks"]) <= 3
    assert len(card["next_steps"]) <= 3
    assert len(card["missing_data"]) <= 5
    assert card["input_hashes"]["decision_card_input_hash"] == body2["data"]["input_hashes"]["decision_card_input_hash"]
    assert body1["meta"]["generated_at"] != ""
    keys = set(_walk_keys(card))
    for forbidden in {"facts_json", "result_json", "payload_json", "risks_json", "questions_json", "report_md", "before_json", "after_json", "execution_endpoint", "risk_severity", "visual_weight", "blocking", "readiness_checklist", "readiness", "price_position", "scenario", "dcf", "irr", "npv", "loan", "tax"}:
        assert forbidden not in keys
    assert all(step["executable_now"] == (step["action_id"] == "open_listing") for step in card["next_steps"])
    assert card["source_trace"]["market_evidence"] == {"present": None, "ref": None, "status": "not_checked_in_pr33"}
    visible = "\n".join(_walk_text(card)).lower()
    for unsafe in ["valuation report", "valuation opinion", "guaranteed yield", "guaranteed rent", "guaranteed market value", "must buy", "must sell", "legal advice", "tax advice"]:
        assert unsafe not in visible
    with Session() as s:
        after = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert after == before


def test_decision_card_meta_contract(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    data = client.get("/api/admin/v1/meta", headers={"X-API-Key": "read"}).json()["data"]
    assert data["meta_contract_version"] == "v1"
    assert data["decision_card_contract_version"] == "decision-card-v1"
    assert data["capabilities"]["decision_card"] is True
    assert data["capabilities"]["report_export"] is False
    assert data["capabilities"]["write_api"] is False
    assert data["capabilities"]["technical_api_actions"] is False
    assert data["capabilities"]["workflow_actions_execute"] is False
    assert "decision_recommendation" in data["enums"]
