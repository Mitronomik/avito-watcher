from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.analysis.market_comps import (
    COMPARABLE_QUALITY_MODEL_VERSION,
    MarketCompInput,
    ResolvedMarketEvidenceConfig,
    SelectedMarketEvidenceContext,
    assess_comparable_quality,
    comparable_quality_facts,
    estimate_market_rent,
)

AS_OF = datetime(2026, 6, 14, tzinfo=UTC)


def _comp(**kw):
    base = dict(
        id=1,
        listing_external_id="l1",
        content_hash="h1",
        confidence=0.9,
        checked_at=AS_OF - timedelta(days=1),
        expires_at=None,
        source_url_normalized="https://example.test/1",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc",
        rent_per_m2_rub=1000.0,
        rent_rub_per_month=None,
        area_m2=50.0,
    )
    base.update(kw)
    return MarketCompInput(**base)


def _ctx(items):
    return SelectedMarketEvidenceContext(
        items=items,
        excluded_counts_by_reason={},
        limitations=[],
        retrieval_as_of_datetime=AS_OF,
        retrieval_as_of_date=AS_OF.date(),
        config=ResolvedMarketEvidenceConfig(location_key="loc", min_comps=3),
        target_listing_external_id="l1",
    )


def _assess(items):
    return assess_comparable_quality(
        context=_ctx(items),
        expected_asset_type="commercial",
        target_area_m2=50.0,
        target_location_key="loc",
        as_of=AS_OF,
    )


def test_good_comparable_gets_high_quality_and_versioned_output():
    assessment = _assess([_comp()])
    result = assessment.results[0]
    assert assessment.comparable_quality_model_version == COMPARABLE_QUALITY_MODEL_VERSION == "v0"
    assert result.quality_bucket == "high"
    assert result.quality_score >= 80
    assert result.accepted is True
    assert {"fresh", "area_similar", "location_match"} <= set(result.quality_flags)
    facts = comparable_quality_facts(assessment)
    assert facts["comparable_quality_model_version"] == "v0"
    assert facts["comparables"][0]["quality_bucket"] == "high"
    assert "raw" not in str(facts).lower()
    assert "webhook" not in str(facts).lower()


def test_stable_rejection_reasons_for_critical_mismatches():
    cases = [
        (_comp(asset_type="flat"), "asset_type_mismatch"),
        (_comp(deal_type="sale"), "deal_type_mismatch"),
        (_comp(rent_per_m2_rub=None, rent_rub_per_month=None), "missing_rent_metric"),
        (_comp(checked_at=AS_OF - timedelta(days=91)), "stale_evidence"),
        (_comp(area_m2=120.0), "area_band_mismatch"),
    ]
    for comp, reason in cases:
        result = _assess([comp]).results[0]
        assert result.accepted is False
        assert result.quality_bucket == "rejected"
        assert result.rejection_reason == reason


def test_missing_source_stale_and_location_mismatch_downgrade():
    results = _assess([
        _comp(id=1, source_url_normalized=""),
        _comp(id=2, checked_at=AS_OF - timedelta(days=45)),
        _comp(id=3, location_key="other"),
    ]).results
    assert [r.accepted for r in results] == [True, True, True]
    assert [r.quality_bucket for r in results] == ["high", "medium", "high"]
    assert "missing_source_url" in results[0].quality_flags
    assert "stale_evidence" in results[1].quality_flags
    assert "location_mismatch" in results[2].quality_flags


def test_unknown_optional_fields_are_soft_flags_not_fake_mismatches():
    result = _assess([_comp(area_m2=None, location_key=None)]).results[0]
    assert result.accepted is True
    assert "area_unknown" in result.quality_flags
    assert "location_unknown" in result.quality_flags
    assert result.rejection_reason is None


def test_evidence_set_summary_prevents_single_or_low_quality_strong_result():
    single = _assess([_comp(source_url_normalized="", checked_at=AS_OF - timedelta(days=45), location_key="other")])
    assert single.summary.accepted_count == 1
    assert single.summary.force_review is True
    assert single.summary.evidence_confidence_cap == 0.5
    assert "single_comp_cannot_support_strong_estimate" in single.summary.review_reasons

    rejected = _assess([_comp(asset_type="flat")])
    assert rejected.summary.accepted_count == 0
    assert rejected.summary.evidence_quality_bucket == "none"
    assert rejected.summary.force_review is True


def test_rejected_comps_are_excluded_from_market_estimate():
    items = [_comp(id=1), _comp(id=2, deal_type="sale", rent_per_m2_rub=9999)]
    assessment = _assess(items)
    estimate = estimate_market_rent(context=_ctx(items), area_m2=50, quality_assessment=assessment)
    assert estimate.item_ids == [1]
    assert estimate.monthly_rent == 50_000


def test_deterministic_as_of_controls_freshness_only_and_no_adjustment_flags():
    item = _comp(checked_at=AS_OF - timedelta(days=20))
    first = _assess([item])
    second = _assess([item])
    later = assess_comparable_quality(
        context=_ctx([item]),
        expected_asset_type="commercial",
        target_area_m2=50,
        target_location_key="loc",
        as_of=AS_OF + timedelta(days=20),
    )
    assert comparable_quality_facts(first) == comparable_quality_facts(second)
    assert first.results[0].quality_bucket == "high"
    assert later.results[0].quality_bucket == "medium"
    text = str(comparable_quality_facts(first))
    assert "comp_adjustment_flags" not in text
    assert "adjusted" not in text


def test_quality_helper_has_no_current_time_or_external_calls():
    source = Path("app/analysis/market_comps.py").read_text()
    helper_source = source[source.index("def assess_comparable_quality") : source.index("def market_evidence_fingerprint_hash")]
    forbidden = ["datetime.now", "datetime.utcnow", "date.today", "requests.", "httpx.", "openai", "agent"]
    assert not any(token in helper_source for token in forbidden)
