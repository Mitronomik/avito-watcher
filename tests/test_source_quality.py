from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.analysis.market_comps import (
    SOURCE_QUALITY_CONFIG_VERSION,
    SOURCE_QUALITY_MODEL_VERSION,
    ComparableQualityAssessment,
    ComparableQualityResult,
    ComparableSelectionDecision,
    ComparableSelectionResult,
    ComparableTargetContext,
    EvidenceSetQualitySummary,
    MarketCompInput,
    ResolvedMarketEvidenceConfig,
    SelectedMarketEvidenceContext,
    adjust_comparable_rents,
    assess_source_quality,
    combine_confidence_caps,
    market_evidence_fingerprint_hash,
)

AS_OF = datetime(2026, 6, 16, tzinfo=UTC)


def _target(**kw):
    base = dict(
        target_listing_id=1,
        target_listing_external_id="t",
        profile="investment",
        estimate_purpose="rent_estimate",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc",
        area_m2=100.0,
    )
    base.update(kw)
    return ComparableTargetContext(**base)


def _comp(comp_id=1, **kw):
    id = kw.pop("id", comp_id)
    base = dict(
        id=id,
        listing_external_id=f"c{id}",
        content_hash=f"h{id}",
        confidence=0.9,
        checked_at=AS_OF - timedelta(days=1),
        expires_at=None,
        source_url_normalized=f"https://example.test/{id}?token=secret",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc",
        rent_per_m2_rub=1000.0,
        rent_rub_per_month=100_000.0,
        area_m2=100.0,
        rent_period="month",
        source_type="asking",
    )
    base.update(kw)
    return MarketCompInput(**base)


def _quality(ids, accepted=True):
    results = [ComparableQualityResult(i, 90, 90, "high", accepted, []) for i in ids]
    return ComparableQualityAssessment(
        "v0",
        AS_OF,
        results,
        EvidenceSetQualitySummary("v0", len(results), len(results), 0, len(results), 0, 0, 90, 90.0, "strong", None, [], False, []),
    )


def _selection(target, comps, rejected=()):
    decisions = [ComparableSelectionDecision(c.id, c.listing_external_id, "selected", "hard_gate") for c in comps]
    decisions += [ComparableSelectionDecision(i, f"r{i}", "rejected", "hard_gate", rejection_reason="policy_rejected") for i in rejected]
    return ComparableSelectionResult("v2", AS_OF, target, 50, 10, 10, decisions, list(comps), [], False)


def _assess(comps, *, quality=None, selection=None):
    target = _target()
    return assess_source_quality(
        target_context=target,
        selected_comps=comps,
        selection_result=selection or _selection(target, comps),
        quality_result=quality or _quality([c.id for c in comps]),
        as_of=AS_OF,
    )


def test_versions_and_facts_and_no_current_time_calls():
    result = _assess([_comp()])
    facts = result.facts()
    assert SOURCE_QUALITY_MODEL_VERSION == "v0"
    assert SOURCE_QUALITY_CONFIG_VERSION == "v0"
    assert facts["version"] == "v0"
    assert facts["config_version"] == "v0"
    source = Path("app/analysis/market_comps.py").read_text()
    helper_section = source.split("def assess_source_quality", 1)[1].split("def adjust_comparable_rents", 1)[0]
    assert "datetime.now" not in helper_section
    assert "datetime.utcnow" not in helper_section
    assert "date.today" not in helper_section


def test_rejects_naive_as_of():
    with pytest.raises(ValueError):
        assess_source_quality(
            target_context=_target(), selected_comps=[], selection_result=None, quality_result=None, as_of=datetime(2026, 6, 16)
        )


def test_trace_strength_and_verification_are_separate():
    comps = [
        _comp(1, source_url_normalized="https://e/1", content_hash="h"),
        _comp(2, source_url_normalized="https://e/2", content_hash=""),
        _comp(3, source_url_normalized="", content_hash="h3"),
        _comp(4, source_url_normalized="", content_hash="", listing_external_id="x"),
        _comp(5, source_url_normalized="", content_hash="", listing_external_id=None, id=None),
    ]
    result = _assess(comps)
    strengths = {i.evidence_id: i.trace_strength for i in result.items}
    assert strengths == {1: "strong", 2: "medium", 3: "medium", 4: "weak", None: "missing"}
    assert all(i.verification_status == "unknown" for i in result.items)
    assert next(i for i in result.items if i.evidence_id == 1).confidence_cap is None


def test_source_type_and_verification_status_explicit_only():
    result = _assess([
        _comp(1, source_type=None),
        _comp(2, source_type="avito_listing"),
        _comp(3, source_type="confirmed"),
        _comp(4, source_type="human_verified", human_verified=True),
        _comp(5, source_type="manual"),
    ])
    by_id = {i.evidence_id: i for i in result.items}
    assert by_id[1].source_type == "unknown"
    assert by_id[2].source_type == "unknown"
    assert "source_type_untrusted_value" in by_id[2].source_quality_reasons
    assert by_id[3].verification_status == "unknown"
    assert by_id[4].source_type == "unknown"
    assert by_id[4].verification_status == "human_verified"
    assert by_id[5].source_type == "manual"
    assert "source_type_unknown" in result.review_reasons



def test_published_at_is_primary_for_source_freshness_and_does_not_change_adjusted_rent():
    comp = _comp(
        1,
        checked_at=AS_OF - timedelta(days=1),
        published_at=AS_OF - timedelta(days=60),
        source_type="confirmed",
        source_url_normalized="https://e/1",
        content_hash="h1",
    )
    result = _assess([comp])
    item = result.items[0]
    assert item.freshness_bucket == "stale"
    assert "stale_source" in item.source_quality_reasons
    assert "stale_source" in result.review_reasons
    assert result.evidence_confidence_cap == 0.5

    target = _target()
    adjusted_before = adjust_comparable_rents(
        target_context=target,
        selected_comps=[comp],
        quality_result=_quality([1]),
        selection_result=_selection(target, [comp]),
        as_of=AS_OF,
    )
    _assess([comp])
    adjusted_after = adjust_comparable_rents(
        target_context=target,
        selected_comps=[comp],
        quality_result=_quality([1]),
        selection_result=_selection(target, [comp]),
        as_of=AS_OF,
    )
    assert adjusted_after.items[0].adjusted_rent_per_m2 == adjusted_before.items[0].adjusted_rent_per_m2
    assert adjusted_after.adjusted_median_rent_per_m2 == adjusted_before.adjusted_median_rent_per_m2


def test_checked_at_fallback_is_fresh_when_published_at_missing():
    comp = _comp(
        1,
        checked_at=AS_OF - timedelta(days=1),
        published_at=None,
        source_type="confirmed",
    )
    result = _assess([comp])
    assert result.items[0].freshness_bucket == "fresh"
    assert "stale_source" not in result.review_reasons

def test_freshness_uses_as_of_and_caps_without_changing_adjusted_rent():
    stale = _comp(1, checked_at=AS_OF - timedelta(days=45), source_type="confirmed")
    expired = _comp(2, expires_at=AS_OF - timedelta(days=1), source_type="effective")
    result = _assess([stale, expired])
    assert result.stale_or_expired_count == 2
    assert "stale_source" in result.review_reasons
    assert "expired_source" in result.review_reasons
    target = _target()
    adjusted_before = adjust_comparable_rents(target_context=target, selected_comps=[stale], quality_result=_quality([1]), selection_result=_selection(target, [stale]), as_of=AS_OF)
    _assess([stale])
    adjusted_after = adjust_comparable_rents(target_context=target, selected_comps=[stale], quality_result=_quality([1]), selection_result=_selection(target, [stale]), as_of=AS_OF)
    assert adjusted_after.adjusted_median_rent_per_m2 == adjusted_before.adjusted_median_rent_per_m2
    assert adjusted_after.adjusted_median_rent == adjusted_before.adjusted_median_rent


def test_caps_only_lower_and_combine_conservatively_and_facts_are_compact_safe():
    comps = [_comp(i, id=None, source_type=None, source_url_normalized="", content_hash="", listing_external_id=None) for i in range(1, 13)]
    result = _assess(comps)
    assert result.evidence_confidence_cap == 0.35
    assert combine_confidence_caps(0.5, result.evidence_confidence_cap) == 0.35
    facts = result.facts(max_items=2)
    assert facts["summary"]["evidence_confidence_cap"] == 0.35
    assert len(facts["items"]) == 2
    assert facts["truncated_items"] is True
    rendered = str(facts).lower()
    assert "evidence_json" not in rendered
    assert "token=secret" not in rendered
    assert "authorization" not in rendered
    assert "script.google.com/macros" not in rendered


def test_selection_and_quality_rejections_are_excluded():
    comps = [_comp(1), _comp(2)]
    target = _target()
    quality = ComparableQualityAssessment(
        "v0", AS_OF,
        [ComparableQualityResult(1, 90, 90, "high", True, []), ComparableQualityResult(2, 0, 0, "rejected", False, [], "bad")],
        EvidenceSetQualitySummary("v0", 2, 1, 1, 1, 0, 0, 90, 90, "strong", None, [], False, []),
    )
    selection = ComparableSelectionResult("v2", AS_OF, target, 50, 10, 10, [ComparableSelectionDecision(1, "c1", "selected", "hard_gate"), ComparableSelectionDecision(2, "c2", "rejected", "hard_gate")], [comps[0]], [], False)
    result = assess_source_quality(target_context=target, selected_comps=comps, selection_result=selection, quality_result=quality, as_of=AS_OF)
    assert [i.evidence_id for i in result.items] == [1]


def _ctx(items):
    return SelectedMarketEvidenceContext(
        items=list(items), excluded_counts_by_reason={}, limitations=[], retrieval_as_of_datetime=AS_OF, retrieval_as_of_date=AS_OF.date(),
        config=ResolvedMarketEvidenceConfig(), target_listing_external_id="t", selection_result=_selection(_target(), items)
    )


def test_fingerprint_includes_source_quality_inputs_is_order_stable_and_hashes_urls():
    a = [_comp(1, source_type="asking", verification_status="unverified"), _comp(2, source_type="confirmed")]
    h1 = market_evidence_fingerprint_hash(_ctx(a))
    h2 = market_evidence_fingerprint_hash(_ctx(list(reversed(a))))
    assert h1 == h2
    assert h1 != market_evidence_fingerprint_hash(_ctx([_comp(1, source_type="confirmed", verification_status="unverified"), _comp(2, source_type="confirmed")]))
    assert h1 != market_evidence_fingerprint_hash(_ctx([_comp(1, source_type="asking", verification_status="verified"), _comp(2, source_type="confirmed")]))
    assert h1 != market_evidence_fingerprint_hash(_ctx([_comp(1, source_type="asking", verification_status="unverified", content_hash="changed"), _comp(2, source_type="confirmed")]))
    assert h1 != market_evidence_fingerprint_hash(_ctx([_comp(1, source_type="asking", verification_status="unverified", checked_at=AS_OF - timedelta(days=2)), _comp(2, source_type="confirmed")]))
    fingerprint = str(_ctx(a))
    assert "token=secret" in fingerprint  # context may contain raw comp URL, unlike the fingerprint payload below.
    from app.analysis.market_comps import market_evidence_fingerprint
    rendered_payload = str(market_evidence_fingerprint(_ctx(a)))
    assert "token=secret" not in rendered_payload
