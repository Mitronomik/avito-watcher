from datetime import UTC, datetime, timedelta
from pathlib import Path
import json

import pytest

from app.analysis.market_comps import (
    CAP_RATE_EVIDENCE_MODEL_VERSION,
    MAX_SALE_EVIDENCE_FACT_ITEMS,
    SALE_EVIDENCE_MODEL_VERSION,
    ComparableQualityAssessment,
    ComparableQualityResult,
    ComparableSelectionDecision,
    ComparableSelectionResult,
    ComparableTargetContext,
    EvidenceSetQualitySummary,
    MarketCompInput,
    SaleEvidenceConfig,
    SourceQualityAssessment,
    SourceQualityItem,
    assess_sale_and_cap_rate_evidence,
    market_evidence_fingerprint_hash,
    ResolvedMarketEvidenceConfig,
    SelectedMarketEvidenceContext,
)

AS_OF = datetime(2026, 6, 16, tzinfo=UTC)


def _target():
    return ComparableTargetContext(1, "t", "investment", "rent_estimate", "commercial", "rent", "loc", 100.0)


def _comp(comp_id=1, **kw):
    base = dict(id=comp_id, listing_external_id=f"c{comp_id}", content_hash=f"h{comp_id}", confidence=0.9,
        checked_at=AS_OF - timedelta(days=1), expires_at=None, source_url_normalized=f"https://example.test/{comp_id}?token=secret",
        asset_type="commercial", deal_type="rent", location_key="loc", rent_per_m2_rub=1000.0,
        rent_rub_per_month=100_000.0, area_m2=100.0, source_type="asking", verification_status="unknown")
    base.update(kw)
    return MarketCompInput(**base)


def _quality(ids, accepted=True):
    results = [ComparableQualityResult(i, 90, 90, "high", accepted, []) for i in ids]
    return ComparableQualityAssessment("v0", AS_OF, results,
        EvidenceSetQualitySummary("v0", len(results), len(results) if accepted else 0, 0 if accepted else len(results), len(results) if accepted else 0, 0, 0, 90, 90.0, "strong", None, [], False, []))


def _selection(target, comps, rejected=()):
    decisions = [ComparableSelectionDecision(c.id, c.listing_external_id, "selected", "hard_gate") for c in comps]
    decisions += [ComparableSelectionDecision(i, f"r{i}", "rejected", "hard_gate", rejection_reason="policy_rejected") for i in rejected]
    return ComparableSelectionResult("v2", AS_OF, target, 50, 10, 10, decisions, list(comps), [], False)


def _source_quality(ids, cap=0.5):
    items = [SourceQualityItem(i, f"c{i}", "asking", "unknown", "strong", "fresh", None, "medium", cap, [], []) for i in ids]
    return SourceQualityAssessment("v0", "v0", AS_OF, _target(), len(items), 0, 0, 0, 0, cap, "medium", [], items)


def _assess(comps, **kw):
    target = _target()
    return assess_sale_and_cap_rate_evidence(target_context=target, selected_comps=comps,
        quality_result=kw.get("quality") or _quality([c.id for c in comps]),
        selection_result=kw.get("selection") or _selection(target, comps),
        source_quality_assessment=kw.get("source_quality"), as_of=kw.get("as_of", AS_OF),
        config=kw.get("config"))


def test_versions_facts_are_compact_safe_and_no_valuation_fields():
    comps = [_comp(i, asking_price_rub=10_000_000+i, currency="RUB", price_type="asking_sale") for i in range(20)]
    facts = _assess(comps).facts(max_items=MAX_SALE_EVIDENCE_FACT_ITEMS)
    assert SALE_EVIDENCE_MODEL_VERSION == "v0"
    assert CAP_RATE_EVIDENCE_MODEL_VERSION == "v0"
    assert facts["sale_evidence_model_version"] == "v0"
    assert facts["cap_rate_evidence_model_version"] == "v0"
    assert len(facts["items"]) == MAX_SALE_EVIDENCE_FACT_ITEMS
    assert facts["truncated_items"] is True
    rendered = json.dumps(facts, sort_keys=True)
    for forbidden in ("evidence_json", "token=secret", "target_fair_value", "estimated_value", "fair_value", "target_equivalent_sale_price"):
        assert forbidden not in rendered


def test_rejects_naive_as_of_and_no_current_time_calls():
    with pytest.raises(ValueError):
        _assess([], as_of=datetime(2026, 6, 16))
    section = Path("app/analysis/market_comps.py").read_text().split("def assess_sale_and_cap_rate_evidence", 1)[1].split("def source_quality_facts", 1)[0]
    assert "datetime.now" not in section
    assert "datetime.utcnow" not in section
    assert "date.today" not in section


def test_ordering_is_deterministic_and_fingerprint_changes_for_sale_fields():
    a = _comp(1, asking_price_rub=12_000_000, currency="RUB", price_type="asking_sale", cap_rate_pct=8.2)
    b = _comp(2, asking_price_rub=10_000_000, currency="RUB", price_type="asking_sale")
    assert _assess([a, b]).facts() == _assess([b, a]).facts()
    ctx1 = SelectedMarketEvidenceContext([a], {}, [], AS_OF, AS_OF.date(), ResolvedMarketEvidenceConfig(), "t", _selection(_target(), [a]))
    changed = _comp(1, asking_price_rub=13_000_000, currency="RUB", price_type="asking_sale", cap_rate_pct=8.2)
    ctx2 = SelectedMarketEvidenceContext([changed], {}, [], AS_OF, AS_OF.date(), ResolvedMarketEvidenceConfig(), "t", _selection(_target(), [changed]))
    assert market_evidence_fingerprint_hash(ctx1) != market_evidence_fingerprint_hash(ctx2)


def test_sale_price_and_cap_rate_handling_boundaries():
    asking = _comp(1, asking_price_rub=12_000_000, currency="RUB", price_type="asking_sale", cap_rate_pct=8.2, cap_rate_unit="pct")
    confirmed = _comp(2, sale_price_rub=9_000_000, currency="RUB", price_type="confirmed_sale", source_type="confirmed", verification_status="verified", area_m2=90)
    unknown = _comp(3, asking_price_rub=7_000_000, currency="RUB")
    result = _assess([asking, confirmed, unknown])
    by_id = {i.evidence_id: i for i in result.items}
    assert by_id[1].price_type == "asking_sale"
    assert by_id[1].explicit_cap_rate_pct == 8.2
    assert by_id[2].price_type == "confirmed_sale"
    assert by_id[2].price_per_m2_rub == 100_000
    assert 3 not in by_id


def test_sale_and_asking_price_families_do_not_fallback_across_price_type():
    confirmed_with_only_asking = _comp(
        1,
        asking_price_rub=12_000_000,
        currency="RUB",
        price_type="confirmed_sale",
        source_type="confirmed",
        verification_status="verified",
    )
    asking_with_only_sale = _comp(
        2,
        sale_price_rub=11_000_000,
        currency="RUB",
        price_type="asking_sale",
    )
    result = _assess([confirmed_with_only_asking, asking_with_only_sale])
    assert result.items == []
    assert "missing_sale_price_for_confirmed_sale" in result.review_reasons
    assert "missing_asking_price_for_asking_sale" in result.review_reasons


def test_correct_price_family_fields_work_for_confirmed_manual_and_asking_sale():
    confirmed = _comp(
        1,
        sale_price_rub=12_000_000,
        currency="RUB",
        price_type="confirmed_sale",
        source_type="confirmed",
        verification_status="verified",
        area_m2=120,
    )
    manual = _comp(
        2,
        sale_price_per_m2_rub=110_000,
        currency="RUB",
        price_type="manual_sale",
    )
    asking = _comp(
        3,
        asking_price_rub=10_000_000,
        currency="RUB",
        price_type="asking_sale",
        area_m2=100,
    )
    by_id = {i.evidence_id: i for i in _assess([confirmed, manual, asking]).items}
    assert by_id[1].price_type == "confirmed_sale"
    assert by_id[1].price_per_m2_rub == 100_000
    assert by_id[2].price_type == "manual_sale"
    assert by_id[2].price_per_m2_rub == 110_000
    assert by_id[3].price_type == "asking_sale"
    assert by_id[3].price_per_m2_rub == 100_000


def test_currency_invalid_values_area_and_unsupported_price_type_are_reviewed_or_excluded():
    comps = [
        _comp(1, asking_price_rub=1, price_type="asking_sale"),
        _comp(2, asking_price_rub=1, currency="USD", price_type="asking_sale"),
        _comp(3, asking_price_rub=0, currency="RUB", price_type="asking_sale"),
        _comp(4, asking_price_rub=1, currency="RUB", price_type="human_verified_sale"),
        _comp(5, asking_price_rub=1, currency="RUB", price_type="asking_sale", area_m2=0),
    ]
    facts = _assess(comps).facts()
    rendered = json.dumps(facts)
    for reason in ("missing_currency", "unsupported_currency", "invalid_price", "unsupported_price_type", "invalid_area"):
        assert reason in rendered


def test_only_selected_quality_accepted_comps_and_no_gross_yield_by_default():
    target = _target()
    selected = _comp(1, asking_price_rub=1_000_000, currency="RUB", price_type="asking_sale")
    rejected = _comp(2, asking_price_rub=2_000_000, currency="RUB", price_type="asking_sale")
    selection = ComparableSelectionResult("v2", AS_OF, target, 50, 10, 10,
        [ComparableSelectionDecision(1, "c1", "selected", "hard_gate"), ComparableSelectionDecision(2, "c2", "rejected", "hard_gate")], [selected], [], False)
    quality = ComparableQualityAssessment("v0", AS_OF, [ComparableQualityResult(1, 90, 90, "high", True, []), ComparableQualityResult(2, 0, 0, "rejected", False, [], "x")],
        EvidenceSetQualitySummary("v0", 2, 1, 1, 1, 0, 0, 90, 90.0, "strong", None, [], False, []))
    result = _assess([selected, rejected], selection=selection, quality=quality, source_quality=_source_quality([1], cap=0.35), config=SaleEvidenceConfig(gross_yield_enabled=False))
    assert [i.evidence_id for i in result.items] == [1]
    assert result.gross_yield_evidence_count == 0
    assert result.items[0].derived_gross_yield_pct is None
    assert result.sale_evidence_confidence_cap == 0.35
