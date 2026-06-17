from __future__ import annotations

from sqlalchemy import func, select

from app.api.admin_v1 import price_position as pp
from app.models.admin_audit_event import AdminAuditEvent
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.market_evidence import MarketEvidenceItem
from tests.test_admin_api_v1_workflow import _analysis, _client, _listing, _seed


def _get(client, listing_id, key="read"):
    return client.get(f"/api/admin/v1/listings/{listing_id}/price-position", headers={"X-API-Key": key})


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


def test_price_position_auth_errors_and_missing(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    assert client.get("/api/admin/v1/listings/1/price-position").status_code == 403
    assert _get(client, 1, "bad").status_code == 403
    assert _get(client, 1, "tech").status_code == 403
    assert client.get("/api/admin/v1/listings/1/price-position?api_key=read").status_code == 403
    assert _get(client, 999).json()["error"]["code"] == "not_found"
    assert _get(client, "not-int").json()["error"]["code"] == "validation_error"


def test_price_position_shape_not_applicable_and_no_side_effects(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    models = [Listing, ListingAnalysis, HumanReview, MarketEvidenceItem, AlertSent, AgentTask, AdminAuditEvent]
    with Session() as s:
        before = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    body = _get(client, 6).json()
    data = body["data"]
    assert body["ok"] is True
    assert data["schema_version"] == "price-position-v1"
    assert data["price_position_model_version"] == "price-position-v1"
    assert data["price_position_policy_version"] == "price-position-policy-v1"
    assert data["price_position_label_version"] == "price-position-labels-v1"
    assert data["listing_id"] == 6
    assert data["listing_external_id"] == "strong"
    assert data["metric"] in pp.PRICE_POSITION_METRICS
    assert data["area_unit"] == "m2"
    assert data["range_basis"] == "selected_adjusted_comparables"
    assert set(["subject_price_per_m2", "market_low", "market_median", "market_high"]).issubset(data)
    assert data["position"] == "not_applicable"
    assert data["confidence"] == "not_applicable"
    assert data["location_basis"] in pp.PRICE_POSITION_LOCATION_BASIS
    assert data["selected_comps_count"] == len(data["selected_evidence_ids"])
    assert isinstance(data["chart"]["visible"], bool)
    assert data["chart"]["reason"] in pp.PRICE_POSITION_CHART_REASONS
    assert data["labels"]["position"]["ru"] and data["labels"]["position"]["en"]
    assert "label_keys" in data and "source_refs" in data and "input_hashes" in data and "limitations" in data
    assert data["input_hashes"]["price_position_input_hash"]
    assert data["source_refs"]["selected_evidence_ids"] == data["selected_evidence_ids"]
    keys = set(_walk_keys(data))
    forbidden = {"facts_json", "result_json", "payload_json", "risks_json", "questions_json", "report_md", "before_json", "after_json", "execution_endpoint", "scenario", "dcf", "irr", "npv", "noi", "yield", "payback", "loan", "tax", "confirmed_rent", "confirmed_price", "confirmed_area", "valuation_opinion", "investment_advice", "fair_value", "market_value", "appraisal"}
    assert forbidden.isdisjoint(keys)
    visible = "\n".join(_walk_text(data)).lower()
    for unsafe in ["cheap", "expensive", "good deal", "bad deal", "undervalued", "overvalued", "buy", "sell", "investment opportunity", "guaranteed", "valuation opinion", "certified appraisal", "investment advice"]:
        assert unsafe not in visible
    with Session() as s:
        after = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert after == before


def test_price_position_commercial_rent_insufficient_subjects_and_comps(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    with Session() as s:
        analyses = [
            _analysis(20, "cr-ok", verdict="strong"),
            _analysis(21, "cr-no-price", verdict="strong"),
            _analysis(22, "cr-no-area", verdict="strong"),
        ]
        for analysis in analyses:
            analysis.profile = "commercial_rent"
        s.add_all([
            _listing(20, "cr-ok", price=90000, area_m2=30),
            _listing(21, "cr-no-price", price=None, area_m2=30),
            _listing(22, "cr-no-area", price=90000, area_m2=None),
            *analyses,
        ])
        s.commit()
    ok = _get(client, 20).json()["data"]
    assert ok["metric"] == "asking_rent_per_m2"
    assert ok["subject_price_per_m2"] == 3000
    assert ok["position"] == "insufficient_data"
    assert ok["chart"] == {"visible": False, "reason": "insufficient_selected_comps"}
    assert "selected_adjusted_comps_not_available_in_pr36" in ok["limitations"]
    no_price = _get(client, 21).json()["data"]
    assert no_price["position"] == "insufficient_data"
    assert no_price["chart"]["reason"] == "insufficient_subject_price"
    no_area = _get(client, 22).json()["data"]
    assert no_area["subject_price_per_m2"] is None
    assert no_area["chart"]["reason"] == "insufficient_subject_area"


def test_price_position_selected_adjusted_comps_and_median(monkeypatch):
    listing = _listing(30, "direct", price=1000, area_m2=1)
    analysis = _analysis(30, "direct", verdict="strong")
    analysis.profile = "commercial_rent"
    source = pp.SelectedAdjustedComparableSource(
        items=(
            pp.SelectedAdjustedComparable(3, 100),
            pp.SelectedAdjustedComparable(1, 90),
            pp.SelectedAdjustedComparable(2, 110),
            pp.SelectedAdjustedComparable(4, 130),
        ),
        location_basis="same_location_key",
        excluded_count=None,
        source_quality_confidence_cap=None,
    )
    data = pp.build_price_position(listing, analysis, comparable_source=source)
    assert data["selected_evidence_ids"] == [1, 2, 3, 4]
    assert data["selected_comps_count"] == 4
    assert data["excluded_comps_count"] == 0
    assert "excluded_comps_count_not_available_in_pr36" in data["limitations"]
    assert data["market_low"] == 90
    assert data["market_median"] == 105
    assert data["market_high"] == 130
    assert data["position"] == "above_market"
    assert data["confidence"] == "medium"
    assert "source_quality_confidence_cap_not_available_in_pr36" in data["limitations"]
    assert data["chart"] == {"visible": True, "reason": "selected_comps_available"}

    with_excluded_ids = pp.build_price_position(
        listing,
        analysis,
        comparable_source=pp.SelectedAdjustedComparableSource(
            items=source.items[:3],
            location_basis="same_location_key",
            excluded_count=None,
            excluded_evidence_ids=(4, 5, 6),
        ),
    )
    assert with_excluded_ids["excluded_comps_count"] == 3
    assert with_excluded_ids["source_refs"]["excluded_evidence_ids"] == [4, 5, 6]
    assert "excluded_comps_count_not_available_in_pr36" not in with_excluded_ids["limitations"]

    with_explicit_excluded_count = pp.build_price_position(
        listing,
        analysis,
        comparable_source=pp.SelectedAdjustedComparableSource(
            items=source.items[:3],
            location_basis="same_location_key",
            excluded_count=2,
            excluded_evidence_ids=(4, 5, 6),
        ),
    )
    assert with_explicit_excluded_count["excluded_comps_count"] == 2
    assert with_explicit_excluded_count["source_refs"]["excluded_evidence_ids"] == [4, 5, 6]

    below = pp.build_price_position(_listing(31, "below", price=80, area_m2=1), analysis, comparable_source=source)
    assert below["position"] == "below_market"
    near = pp.build_price_position(_listing(32, "near", price=94.5, area_m2=1), analysis, comparable_source=source)
    assert near["position"] == "near_market"

    hidden = pp.build_price_position(listing, analysis, comparable_source=pp.SelectedAdjustedComparableSource(items=source.items[:2], location_basis="same_listing_context"))
    assert hidden["chart"]["reason"] == "insufficient_selected_comps"
    loc_hidden = pp.build_price_position(listing, analysis, comparable_source=pp.SelectedAdjustedComparableSource(items=source.items[:3], location_basis="insufficient_location"))
    assert loc_hidden["chart"]["reason"] == "insufficient_location"


def test_price_position_decision_card_decision_source_and_meta(monkeypatch):
    client, Session = _client(monkeypatch)
    _seed(Session)
    standalone = _get(client, 6).json()["data"]
    card = client.get("/api/admin/v1/listings/6/decision-card", headers={"X-API-Key": "read"}).json()["data"]
    assert card["price_position"] == standalone
    assert card["primary_recommendation"]["code"] == "take_in_work"
    source = client.get("/api/admin/v1/listings/6/decision-source", headers={"X-API-Key": "read"}).json()["data"]
    assert source["available_sections"]["price_position"] is True
    assert source["price_position_ref"] == {"route_name": "admin_api_v1_price_position", "listing_id": 6, "schema_version": "price-position-v1"}
    assert "url" not in source["price_position_ref"] and "endpoint" not in source["price_position_ref"]
    assert "price_position" not in source
    meta = client.get("/api/admin/v1/meta", headers={"X-API-Key": "read"}).json()["data"]
    assert meta["price_position_contract_version"] == "price-position-v1"
    assert meta["capabilities"]["price_position"] is True
    for key in ["price_position_code", "price_position_confidence", "price_position_location_basis", "price_position_chart_reason", "price_position_metric", "price_position_range_basis"]:
        assert key in meta["enums"]
    assert {v["value"] for v in meta["enums"]["price_position_code"]["values"]} == set(pp.PRICE_POSITION_CODES)
