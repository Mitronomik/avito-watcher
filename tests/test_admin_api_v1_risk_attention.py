from __future__ import annotations

from sqlalchemy import func, select

from app.api.admin_v1.risk_attention import RISK_CATEGORIES, RISK_SEVERITIES, build_risk_attention_from_card
from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from tests.test_admin_api_v1_workflow import _client, _seed


def _get(client, listing_id, key="read"):
    return client.get(f"/api/admin/v1/listings/{listing_id}/risk-attention", headers={"X-API-Key": key})


def _walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_keys(v)


def test_risk_attention_auth_errors_and_missing(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    assert client.get("/api/admin/v1/listings/1/risk-attention").status_code == 403
    assert _get(client, 1, "bad").status_code == 403
    assert _get(client, 1, "tech").status_code == 403
    assert client.get("/api/admin/v1/listings/1/risk-attention?api_key=read").status_code == 403
    assert _get(client, "bad-id").json()["error"]["code"] == "validation_error"
    assert _get(client, 999).json()["error"]["code"] == "not_found"


def test_risk_attention_contract_mapping_and_no_side_effects(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    models = [Listing, ListingAnalysis, HumanReview, AlertSent, AgentTask, AdminAuditEvent]
    with Session() as s:
        before = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    data = _get(client, 3).json()["data"]
    assert data["schema_version"] == "risk-attention-v1"
    assert data["risk_attention_model_version"] == "risk-attention-v1"
    assert data["risk_attention_policy_version"] == "risk-attention-policy-v1"
    assert data["risk_attention_label_version"] == "risk-attention-labels-v1"
    assert data["risk_count"] == len(data["risks"])
    assert data["blocking_risk_count"] == sum(1 for r in data["risks"] if r["blocking"])
    assert data["max_visual_weight"] == max(r["visual_weight"] for r in data["risks"])
    assert data["input_hashes"]["risk_attention_input_hash"]
    assert data["source_refs"]["listing_id"] == 3
    risk = data["risks"][0]
    assert risk["id"] == "missing_price"
    assert "risk_id" not in risk
    assert risk["schema_version"] == "risk-attention-item-v1"
    assert risk["category"] == "data_quality"
    assert risk["severity"] == "high"
    assert risk["severity_score"] == 0.85
    assert risk["visual_weight"] == risk["severity_score"]
    assert risk["blocking"] is True
    assert risk["blocking_scope"] == "visual_attention"
    assert set(risk["label"]) == {"ru", "en"}
    assert set(risk["explanation"]) == {"ru", "en"}
    assert risk["recommended_action"]["action_id"] == "request_data"
    for item in data["risks"]:
        assert item["category"] in RISK_CATEGORIES
        assert item["severity"] in RISK_SEVERITIES
        assert 0 <= item["severity_score"] <= 1
        assert 0 <= item["visual_weight"] <= 1
        assert item["visual_weight"] <= item["severity_score"]
    keys = set(_walk_keys(data))
    for forbidden in {"facts_json", "result_json", "payload_json", "risks_json", "questions_json", "report_md", "execution_endpoint", "readiness", "ready", "not_ready", "checked_count", "total_count", "critical_missing_count", "price_position", "scenario", "dcf", "irr", "npv", "loan", "tax", "appraisal", "valuation_opinion", "guaranteed_yield", "probability_of_loss", "expected_loss"}:
        assert forbidden not in keys
    with Session() as s:
        after = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert after == before


def test_decision_card_and_source_integration(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    card = client.get("/api/admin/v1/listings/2/decision-card", headers={"X-API-Key": "read"}).json()["data"]
    attention = _get(client, 2).json()["data"]
    assert card["risk_attention"]["risks"] == attention["risks"]
    for risk in card["top_risks"]:
        assert {"category", "severity", "severity_score", "visual_weight", "blocking", "blocking_scope", "explanation", "recommended_action"} <= set(risk)
    assert [r["rank"] for r in card["top_risks"]] == sorted(r["rank"] for r in card["top_risks"])
    source = client.get("/api/admin/v1/listings/2/decision-source", headers={"X-API-Key": "read"}).json()["data"]
    assert source["available_sections"]["risk_attention"] is True
    assert source["risk_attention_ref"] == {"route_name": "admin_api_v1_risk_attention", "listing_id": 2, "schema_version": "risk-attention-v1"}
    assert "risk_attention" not in source
    assert "execution_endpoint" not in str(source)
    assert "api_key" not in str(source).lower()


def test_unknown_risk_and_hash_determinism():
    card = {"listing_id": 1, "listing_external_id": "x", "top_risks": [{"id": "future", "label": {"ru": "x", "en": "x"}, "label_key": "decision_risk.future", "rank": 1, "evidence_ref": "listing:1"}], "workflow": {"source_refs": {}, "allowed_actions": [], "blocked_actions": []}, "input_hashes": {"decision_card_input_hash": "a", "workflow_source_hash": "b"}}
    first = build_risk_attention_from_card(card)
    second = build_risk_attention_from_card(card)
    risk = first["risks"][0]
    assert risk["id"] == "unknown_risk"
    assert risk["original_risk_id"] == "future"
    assert risk["category"] == "system"
    assert risk["severity"] == "info"
    assert risk["visual_weight"] == 0.10
    assert risk["blocking"] is False
    assert first["input_hashes"]["risk_attention_input_hash"] == second["input_hashes"]["risk_attention_input_hash"]
    card["input_hashes"]["decision_card_input_hash"] = "changed"
    assert first["input_hashes"]["risk_attention_input_hash"] != build_risk_attention_from_card(card)["input_hashes"]["risk_attention_input_hash"]


def test_meta_contract_risk_attention(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    data = client.get("/api/admin/v1/meta", headers={"X-API-Key": "read"}).json()["data"]
    assert data["risk_attention_contract_version"] == "risk-attention-v1"
    assert data["capabilities"]["risk_attention"] is True
    assert data["capabilities"]["write_api"] is False
    assert [v["value"] for v in data["enums"]["risk_category"]["values"]] == sorted(RISK_CATEGORIES)
    assert [v["value"] for v in data["enums"]["risk_severity"]["values"]] == sorted(RISK_SEVERITIES)
    assert "formula" not in str(data).lower()
