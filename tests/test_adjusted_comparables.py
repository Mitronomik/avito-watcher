from datetime import UTC, datetime, timedelta

from app.analysis.market_comps import (
    ADJUSTED_COMPARABLE_CONFIG_VERSION,
    ADJUSTED_COMPARABLE_MODEL_VERSION,
    REASON_ASKING_TO_EFFECTIVE_DISCOUNT,
    ComparableQualityAssessment,
    ComparableQualityResult,
    ComparableSelectionDecision,
    ComparableSelectionResult,
    ComparableTargetContext,
    EvidenceSetQualitySummary,
    MarketCompInput,
    adjust_comparable_rents,
    market_evidence_fingerprint,
    ResolvedMarketEvidenceConfig,
    SelectedMarketEvidenceContext,
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


def _comp(id=1, **kw):
    base = dict(
        id=id,
        listing_external_id=f"c{id}",
        content_hash=f"h{id}",
        confidence=0.9,
        checked_at=AS_OF - timedelta(days=1),
        expires_at=None,
        source_url_normalized=f"https://e.test/{id}",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc",
        rent_per_m2_rub=1000.0,
        rent_rub_per_month=80_000.0,
        area_m2=80.0,
        rent_period="month",
        source_type="asking",
    )
    base.update(kw)
    return MarketCompInput(**base)


def _quality(ids, bucket="high"):
    results = [ComparableQualityResult(i, 90, 90, bucket, True, ["fresh"]) for i in ids]
    summary = EvidenceSetQualitySummary(
        "v0",
        len(results),
        len(results),
        0,
        len(results) if bucket == "high" else 0,
        len(results) if bucket == "medium" else 0,
        len(results) if bucket == "low" else 0,
        90,
        90.0,
        "strong",
        None,
        [],
        False,
        [],
    )
    return ComparableQualityAssessment("v0", AS_OF, results, summary)


def _selection(target, comps, rejected=()):
    decisions = [
        ComparableSelectionDecision(
            c.id,
            c.listing_external_id,
            "selected",
            "hard_gate",
            selection_reason="same_location_key_reuse",
        )
        for c in comps
    ]
    decisions += [
        ComparableSelectionDecision(
            i, f"r{i}", "rejected", "hard_gate", rejection_reason="area_band_mismatch"
        )
        for i in rejected
    ]
    return ComparableSelectionResult(
        "v2", AS_OF, target, 50, 10, 10, decisions, list(comps), [], False
    )


def test_adjusted_comp_has_raw_adjusted_rent_reasons_and_medians():
    target = _target(first_line=False, condition="average", floor_access="standard")
    comps = [
        _comp(i, first_line=True, condition="good", floor_access="ground")
        for i in (1, 2, 3)
    ]
    result = adjust_comparable_rents(
        target_context=target,
        selected_comps=comps,
        quality_result=_quality([1, 2, 3]),
        selection_result=_selection(target, comps),
        as_of=AS_OF,
    )
    assert result.version == ADJUSTED_COMPARABLE_MODEL_VERSION
    assert result.config_version == ADJUSTED_COMPARABLE_CONFIG_VERSION
    assert result.adjusted_count == 3
    item = result.items[0]
    assert item.raw_rent == 80_000.0
    assert item.raw_rent_per_m2 == 1000.0
    assert item.adjusted_rent_per_m2 < item.raw_rent_per_m2
    assert item.adjusted_rent == item.adjusted_rent_per_m2 * 100
    assert REASON_ASKING_TO_EFFECTIVE_DISCOUNT in item.adjustment_reasons
    facts = result.facts()
    assert (
        facts["summary"]["adjusted_median_rent_per_m2"]
        == result.adjusted_median_rent_per_m2
    )
    assert facts["summary"]["adjusted_median_used"] is True


def test_direction_unknown_confirmed_freshness_and_missing_target_area_do_not_change_value():
    target = _target(area_m2=None, first_line=None, condition=None, floor_access=None)
    comp = _comp(
        source_type="confirmed",
        first_line=True,
        condition="good",
        floor_access="ground",
    )
    result = adjust_comparable_rents(
        target_context=target,
        selected_comps=[comp],
        quality_result=_quality([1]),
        selection_result=_selection(target, [comp]),
        as_of=AS_OF,
    )
    item = result.items[0]
    assert item.adjusted_rent is None
    assert item.adjusted_rent_per_m2 == item.raw_rent_per_m2
    assert "source_type_confirmed_no_discount" in item.adjustment_flags
    assert "target_area_unknown" in result.review_reasons
    assert "first_line_unknown" in item.adjustment_flags


def test_total_rent_derives_rent_per_m2_missing_metric_and_unsupported_period_excluded():
    target = _target()
    good = _comp(
        1,
        rent_per_m2_rub=None,
        rent_rub_per_month=90_000,
        area_m2=90,
        source_type="confirmed",
    )
    missing = _comp(2, rent_per_m2_rub=None, rent_rub_per_month=None, area_m2=90)
    yearly = _comp(3, rent_period="year")
    result = adjust_comparable_rents(
        target_context=target,
        selected_comps=[good, missing, yearly],
        quality_result=_quality([1, 2, 3]),
        selection_result=_selection(target, [good, missing, yearly]),
        as_of=AS_OF,
    )
    assert result.adjusted_count == 1
    assert result.items[0].raw_rent_per_m2 == 1000.0
    assert result.excluded_from_adjusted_count == 2
    assert "missing_rent_metric" in result.review_reasons
    assert "insufficient_rent_metric" in result.review_reasons


def test_rejected_selection_and_quality_are_not_adjusted_and_order_is_stable():
    target = _target()
    comps = [_comp(2), _comp(1)]
    q = ComparableQualityAssessment(
        "v0",
        AS_OF,
        [
            ComparableQualityResult(
                1, 0, 0, "rejected", False, [], "insufficient_data"
            ),
            ComparableQualityResult(2, 90, 90, "high", True, []),
        ],
        EvidenceSetQualitySummary(
            "v0", 2, 1, 1, 1, 0, 0, 90, 90, "medium", None, [], False, []
        ),
    )
    sel = ComparableSelectionResult(
        "v2",
        AS_OF,
        target,
        50,
        10,
        10,
        [
            ComparableSelectionDecision(1, "c1", "selected", "hard_gate"),
            ComparableSelectionDecision(2, "c2", "selected", "hard_gate"),
        ],
        comps,
        [],
        False,
    )
    a = adjust_comparable_rents(
        target_context=target,
        selected_comps=comps,
        quality_result=q,
        selection_result=sel,
        as_of=AS_OF,
    )
    b = adjust_comparable_rents(
        target_context=target,
        selected_comps=list(reversed(comps)),
        quality_result=q,
        selection_result=sel,
        as_of=AS_OF,
    )
    assert [i.evidence_id for i in a.items] == [2]
    assert a.facts() == b.facts()


def test_caps_stale_low_quality_and_manual_primary():
    target = _target(first_line=True, condition="excellent", floor_access="street")
    comps = [
        _comp(
            i,
            area_m2=10,
            first_line=False,
            condition="poor",
            floor_access="basement",
            checked_at=AS_OF - timedelta(days=60),
        )
        for i in (1, 2, 3)
    ]
    result = adjust_comparable_rents(
        target_context=target,
        selected_comps=comps,
        quality_result=_quality([1, 2, 3], bucket="low"),
        selection_result=_selection(target, comps),
        as_of=AS_OF,
        manual_rent=123,
    )
    assert result.adjusted_median_used is False
    assert result.adjusted_median_not_used_reason == "manual_rent_primary"
    assert any(i.adjustment_cap_applied for i in result.items)
    assert "stale_comp" in result.review_reasons
    assert "low_quality_comps_in_adjusted_set" in result.review_reasons


def test_fingerprint_includes_adjusted_versions_and_structured_inputs_order_stable():
    ctx = SelectedMarketEvidenceContext(
        items=[_comp(2, source_type="confirmed"), _comp(1, first_line=True)],
        excluded_counts_by_reason={},
        limitations=[],
        retrieval_as_of_datetime=AS_OF,
        retrieval_as_of_date=AS_OF.date(),
        config=ResolvedMarketEvidenceConfig(),
        target_listing_external_id="t",
    )
    payload = market_evidence_fingerprint(ctx)
    assert payload["adjusted_comparable_model_version"] == "v0"
    assert payload["adjusted_comparable_config_version"] == "v0"
    assert payload["items"][0]["first_line"] is True
    ctx2 = SelectedMarketEvidenceContext(
        items=list(reversed(ctx.items)),
        excluded_counts_by_reason={},
        limitations=[],
        retrieval_as_of_datetime=AS_OF,
        retrieval_as_of_date=AS_OF.date(),
        config=ResolvedMarketEvidenceConfig(),
        target_listing_external_id="t",
    )
    assert market_evidence_fingerprint(ctx) == market_evidence_fingerprint(ctx2)
