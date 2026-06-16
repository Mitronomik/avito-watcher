from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.analysis.market_comps import (
    COMPARABLE_SELECTION_POLICY_VERSION,
    ComparableTargetContext,
    MarketCompInput,
    comparable_selection_facts,
    select_comparable_candidates,
)

AS_OF = datetime(2026, 6, 16, tzinfo=UTC)


def _target(**kw):
    base = dict(
        target_listing_id=10,
        target_listing_external_id="target",
        profile="commercial_sale_investment",
        estimate_purpose="rent_estimate",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc-a",
        area_m2=100.0,
    )
    base.update(kw)
    return ComparableTargetContext(**base)


def _comp(**kw):
    base = dict(
        id=1,
        listing_external_id="other",
        content_hash="hash-1",
        confidence=0.9,
        checked_at=AS_OF - timedelta(days=1),
        expires_at=None,
        source_url_normalized="",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc-a",
        rent_per_m2_rub=1000.0,
        rent_rub_per_month=None,
        area_m2=105.0,
    )
    base.update(kw)
    return MarketCompInput(**base)


def _select(items, **kw):
    return select_comparable_candidates(_target(), items, as_of=AS_OF, candidate_limit=kw.get("candidate_limit", 50), selected_limit=kw.get("selected_limit", 5))


def test_same_listing_direct_evidence_is_selected_with_stable_policy_version():
    result = _select([_comp(listing_external_id="target", location_key=None, area_m2=None)])
    assert result.policy_version == COMPARABLE_SELECTION_POLICY_VERSION == "v2"
    assert [d.selection_reason for d in result.decisions] == ["same_listing_direct_evidence"]
    assert result.selected_items[0].id == 1


def test_cross_listing_same_location_asset_deal_area_is_selected():
    result = _select([_comp(id=2)])
    decision = result.decisions[0]
    assert decision.selection_status == "selected"
    assert decision.selection_reason == "same_location_key_reuse"
    assert decision.matched_on == ["asset_type", "deal_type", "location_key", "area_band"]


def test_hard_gate_rejection_reasons_are_stable():
    cases = [
        (_comp(id=11, asset_type="flat"), "asset_type_mismatch"),
        (_comp(id=12, deal_type="sale"), "deal_type_mismatch"),
        (_comp(id=13, location_key="loc-b"), "location_key_mismatch"),
        (_comp(id=14, area_m2=150.0), "area_band_mismatch"),
        (_comp(id=15, checked_at=AS_OF - timedelta(days=31)), "stale_evidence"),
        (_comp(id=16, rent_per_m2_rub=None, rent_rub_per_month=None), "missing_rent_metric"),
    ]
    for item, reason in cases:
        result = _select([item])
        assert result.decisions[0].rejection_reason == reason


def test_source_trace_can_be_content_hash_without_url_and_missing_match_data_rejects():
    selected = _select([_comp(id=21, source_url_normalized="", content_hash="hash-only")])
    assert selected.decisions[0].selection_status == "selected"
    rejected = select_comparable_candidates(
        _target(location_key=None), [_comp(id=22)], as_of=AS_OF, candidate_limit=50, selected_limit=5
    )
    assert rejected.decisions[0].rejection_reason == "insufficient_match_data"


def test_candidate_limits_ordering_and_no_scope_widening_are_deterministic():
    items = [_comp(id=i, confidence=0.5 + i / 100, area_m2=100.0) for i in range(1, 6)]
    result1 = _select(items, candidate_limit=3, selected_limit=2)
    result2 = _select(list(reversed(items)), candidate_limit=3, selected_limit=2)
    assert [d.evidence_id for d in result1.decisions] == [5, 4, 3]
    assert [d.evidence_id for d in result1.decisions] == [d.evidence_id for d in result2.decisions]
    assert [i.id for i in result1.selected_items] == [5, 4]
    assert result1.truncated_candidates is True
    assert "insufficient_selected_comparable_evidence" not in result1.review_reasons
    few = _select([_comp(id=99)], selected_limit=3)
    assert few.review_reasons == ["insufficient_selected_comparable_evidence"]


def test_as_of_is_deterministic_and_no_current_time_call_in_helper():
    fresh = select_comparable_candidates(_target(), [_comp(checked_at=AS_OF - timedelta(days=30))], as_of=AS_OF)
    stale = select_comparable_candidates(_target(), [_comp(checked_at=AS_OF - timedelta(days=30))], as_of=AS_OF + timedelta(days=1))
    assert fresh.decisions[0].selection_status == "selected"
    assert stale.decisions[0].rejection_reason == "stale_evidence"
    source = Path("app/analysis/market_comps.py").read_text()
    helper = source[source.index("def select_comparable_candidates") : source.index("def resolve_market_evidence_config")]
    assert "datetime.now" not in helper
    assert "datetime.utcnow" not in helper
    assert "date.today" not in helper


def test_facts_are_compact_per_analysis_and_have_no_adjusted_values():
    result = _select([_comp(id=1), _comp(id=2, location_key="wrong")], selected_limit=1)
    facts = comparable_selection_facts(result)
    assert facts["version"] == "v2"
    assert facts["candidate_count_considered"] == 2
    assert facts["selected_count"] == 1
    assert facts["rejected_count"] == 1
    assert facts["rejected"][0]["rejection_reason"] == "location_key_mismatch"
    rendered = str(facts)
    assert "adjusted_rent" not in rendered
    assert "adjusted_price" not in rendered
    assert "adjusted_median" not in rendered
    assert "comp_adjustment_flags" not in rendered
