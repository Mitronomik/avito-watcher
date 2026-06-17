from __future__ import annotations

from sqlalchemy import func, select

from app.api.admin_v1.readiness_checklist import READINESS_GROUPS, READINESS_ITEM_IDS, READINESS_ITEM_STATUSES, READINESS_STATUSES
from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from tests.test_admin_api_v1_workflow import _client, _seed


def _readiness(client, listing_id, key="read"):
    return client.get(f"/api/admin/v1/listings/{listing_id}/readiness-checklist", headers={"X-API-Key": key})


def _item(data, id_):
    return {item["id"]: item for item in data["items"]}[id_]


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


def test_readiness_auth_errors_and_contract(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    assert client.get("/api/admin/v1/listings/1/readiness-checklist").status_code == 403
    assert _readiness(client, 1, "bad").status_code == 403
    assert _readiness(client, 1, "tech").status_code == 403
    assert client.get("/api/admin/v1/listings/1/readiness-checklist?api_key=read").status_code == 403
    assert _readiness(client, 999).json()["error"]["code"] == "not_found"
    assert _readiness(client, "not-int").json()["error"]["code"] == "validation_error"

    data = _readiness(client, 6).json()["data"]
    assert data["schema_version"] == "readiness-checklist-v1"
    assert data["readiness_model_version"] == "readiness-checklist-v1"
    assert data["readiness_policy_version"] == "readiness-policy-v1"
    assert data["readiness_label_version"] == "readiness-labels-v1"
    assert data["status"] in READINESS_STATUSES
    assert data["label"]["ru"] and data["label"]["en"]
    assert data["label_key"] == f"readiness_status.{data['status']}"
    for key in ["checked_count", "total_count", "critical_missing_count", "blocking_item_count"]:
        assert isinstance(data[key], int) and data[key] >= 0
    assert isinstance(data["source_refs"], dict)
    assert isinstance(data["input_hashes"], dict)
    assert isinstance(data["limitations"], list)
    assert [item["id"] for item in data["items"]] == list(READINESS_ITEM_IDS)
    for item in data["items"]:
        assert item["schema_version"] == "readiness-checklist-item-v1"
        assert item["id"] in READINESS_ITEM_IDS
        assert item["group"] in READINESS_GROUPS
        assert item["status"] in READINESS_ITEM_STATUSES
        assert isinstance(item["critical"], bool)
        assert item["label"]["ru"] and item["label"]["en"]
        assert item["label_key"] == f"readiness_item.{item['id']}"
        assert item["explanation"]["ru"] and item["explanation"]["en"]
        assert item["source_ref"] is not None or item["status"] == "not_applicable"
        assert isinstance(item["evidence_refs"], list)
        assert "execution_endpoint" not in item["recommended_action"]
        assert "method" not in item["recommended_action"]
        assert item["recommended_action"]["id"] not in {"generate_memo", "export_report"}
        assert isinstance(item["rank"], int)


def test_readiness_statuses_counters_and_defaults(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    no_analysis = _readiness(client, 1).json()["data"]
    assert no_analysis["status"] == "blocked"
    assert _item(no_analysis, "analysis_available")["status"] == "blocked"
    assert _item(no_analysis, "human_review_available")["status"] == "not_applicable"

    missing_area = _readiness(client, 2).json()["data"]
    assert missing_area["status"] == "blocked"
    assert _item(missing_area, "area_present")["status"] == "missing"

    missing_price = _readiness(client, 3).json()["data"]
    assert missing_price["status"] == "blocked"
    assert _item(missing_price, "price_present")["status"] == "missing"

    freshness = _readiness(client, 4).json()["data"]
    assert _item(freshness, "freshness_known")["status"] == "warning"
    assert _item(freshness, "freshness_known")["critical"] is False
    assert freshness["status"] == "partial"

    ready = _readiness(client, 6).json()["data"]
    assert _item(ready, "market_evidence_checked")["status"] == "not_applicable"
    assert _item(ready, "financial_assumptions_present")["status"] == "not_applicable"
    assert _item(ready, "object_quality_available")["status"] == "not_applicable"
    assert _item(ready, "report_inputs_ready")["status"] == "not_applicable"
    assert _item(ready, "human_review_available")["status"] == "warning"
    assert ready["status"] == "partial"

    for data in [no_analysis, missing_area, missing_price, freshness, ready]:
        assert data["total_count"] == sum(1 for item in data["items"] if item["status"] != "not_applicable")
        assert data["checked_count"] == sum(1 for item in data["items"] if item["status"] != "not_applicable")
        assert data["checked_count"] == data["total_count"]
        assert data["critical_missing_count"] == sum(1 for item in data["items"] if item["critical"] and item["status"] in {"missing", "blocked"})
        assert data["blocking_item_count"] == data["critical_missing_count"]


def test_readiness_decision_card_decision_source_meta_and_boundaries(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    models = [Listing, ListingAnalysis, HumanReview, AlertSent, AgentTask, AdminAuditEvent]
    with Session() as s:
        before = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}

    standalone = _readiness(client, 6).json()["data"]
    card = client.get("/api/admin/v1/listings/6/decision-card", headers={"X-API-Key": "read"}).json()["data"]
    assert card["readiness_checklist"] == standalone
    assert card["primary_recommendation"]["code"] == "take_in_work"
    assert card["workflow"]["workflow_state"] == "ready_for_work"
    source = client.get("/api/admin/v1/listings/6/decision-source", headers={"X-API-Key": "read"}).json()["data"]
    assert source["available_sections"]["readiness_checklist"] is True
    assert source["readiness_checklist_ref"] == {"route_name": "admin_api_v1_readiness_checklist", "listing_id": 6, "schema_version": "readiness-checklist-v1"}
    assert "readiness_checklist" not in source
    assert "decision_card_ref" in source and "risk_attention_ref" in source

    meta = client.get("/api/admin/v1/meta", headers={"X-API-Key": "read"}).json()["data"]
    assert meta["readiness_checklist_contract_version"] == "readiness-checklist-v1"
    assert meta["capabilities"]["readiness_checklist"] is True
    assert meta["capabilities"]["write_api"] is False
    assert [v["value"] for v in meta["enums"]["readiness_status"]["values"]] == sorted(READINESS_STATUSES)
    assert [v["value"] for v in meta["enums"]["readiness_item_status"]["values"]] == sorted(READINESS_ITEM_STATUSES)
    assert [v["value"] for v in meta["enums"]["readiness_group"]["values"]] == sorted(READINESS_GROUPS)
    assert [v["value"] for v in meta["enums"]["readiness_item_id"]["values"]] == sorted(READINESS_ITEM_IDS)

    keys = set(_walk_keys({"readiness": standalone, "card": card, "source": source}))
    for forbidden in {"facts_json", "result_json", "payload_json", "risks_json", "questions_json", "report_md", "execution_endpoint", "price_position", "scenario", "dcf", "irr", "npv", "loan", "tax", "confirmed_rent", "confirmed_price", "confirmed_area", "appraisal", "valuation_opinion", "investment_advice"}:
        assert forbidden not in keys
    visible = "\n".join(_walk_text(standalone)).lower()
    for unsafe in ["must buy", "must sell", "guaranteed yield", "guaranteed rent", "guaranteed market value", "legal advice", "tax advice", "external report ready", "investment ready"]:
        assert unsafe not in visible

    with Session() as s:
        after = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert after == before
