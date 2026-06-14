from datetime import UTC, datetime, timedelta

from app.analysis.config import AnalysisConfig
from app.analysis.market_comps import select_market_evidence, estimate_market_rent
from app.analysis.provider import InvestmentAnalysisProvider
from app.models.listing import Listing
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun

AS_OF = datetime(2026, 6, 14, tzinfo=UTC)


def _item(db, **kw):
    run = db.query(MarketResearchRun).first() or MarketResearchRun(
        agent_task_id=333,
        status="success",
        schema_version="research-agent-result-v1",
        checked_at=AS_OF.replace(tzinfo=None),
    )
    db.add(run)
    db.flush()
    base = dict(
        run_id=run.id,
        evidence_type="comparable_candidate",
        research_profile="default",
        listing_external_id="l1",
        asset_type="commercial",
        deal_type="rent",
        location_key="loc",
        source_url="https://e.test/1",
        source_url_normalized="https://e.test/1",
        confidence=0.8,
        is_reusable=True,
        checked_at=(AS_OF - timedelta(days=1)).replace(tzinfo=None),
        expires_at=(AS_OF + timedelta(days=1)).replace(tzinfo=None),
        content_hash=f"h{db.query(MarketEvidenceItem).count()}",
        rent_per_m2_rub=1000.0,
    )
    base.update(kw)
    obj = MarketEvidenceItem(**base)
    db.add(obj)
    db.flush()
    return obj


def test_market_evidence_only_uses_median_rent_per_m2(db_session):
    for v in (1000, 1200, 1400):
        _item(db_session, rent_per_m2_rub=v, rent_rub_per_month=None)
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "investment_purchase_price": 1_000_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    est = estimate_market_rent(context=ctx, area_m2=50)
    assert est.monthly_rent == 60_000
    listing = Listing(external_id="l1", title="x", price=1, area_m2=50)
    res = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=listing,
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert (
        res.facts_json["investment_metrics"]["rent_estimate_source"]
        == "market_evidence"
    )
    assert res.facts_json["llm_used"] is False
    assert res.facts_json["agent_used"] is False
    assert res.facts_json["live_external_research_used"] is False
    assert res.facts_json["investment_metrics"]["gross_yield_on_price"] == 0.72


def test_manual_rent_primary_and_mismatch_not_capped_by_weak_comps(db_session):
    _item(db_session, rent_per_m2_rub=None, rent_rub_per_month=50_000)
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "investment_purchase_price": 1_000_000,
            "estimated_monthly_rent": 100_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    listing = Listing(external_id="l1", title="x", price=1, area_m2=50)
    res = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=listing,
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert res.facts_json["investment_metrics"]["estimated_monthly_rent"] == 100_000
    assert "manual_rent_differs_from_market_evidence" in res.risks_json["flags"]
    assert "single_market_comp" not in res.risks_json["flags"]


def test_selection_scope_and_quality(db_session):
    good = _item(db_session, listing_external_id="target", location_key="a")
    _item(
        db_session, listing_external_id="other", location_key="a", content_hash="other"
    )
    _item(db_session, listing_external_id="target", location_key="b", content_hash="b")
    _item(
        db_session,
        listing_external_id="target",
        location_key="a",
        source_url=None,
        source_url_normalized=None,
        content_hash="nosrc",
    )
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {"use_market_evidence": True, "market_evidence_location_key": "a"},
    )
    candidates = (
        db_session.query(MarketEvidenceItem)
        .filter_by(listing_external_id="target")
        .all()
    )
    ctx = select_market_evidence(
        candidates=candidates,
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    assert [i.id for i in ctx.items] == [good.id]
    assert ctx.excluded_counts_by_reason["wrong_location_key"] == 1
    assert ctx.excluded_counts_by_reason["missing_source"] == 1


def test_missing_area_blocks_rent_per_m2_only(db_session):
    for v in (1000, 1100, 1200):
        _item(db_session, rent_per_m2_rub=v, rent_rub_per_month=None)
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment", {"use_market_evidence": True}
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    est = estimate_market_rent(context=ctx, area_m2=None)
    assert est.monthly_rent is None
    assert "missing_area_for_market_rent" in est.risk_flags


def test_evidence_only_report_and_facts_explain_market_rent_source(db_session):
    for v in (1000, 1200, 1400):
        _item(db_session, rent_per_m2_rub=v, rent_rub_per_month=None)
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "investment_purchase_price": 1_000_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    result = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=Listing(external_id="l1", title="x", area_m2=50),
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert "manual assumptions only" not in result.report_md
    assert "uses no comps" not in result.report_md
    assert (
        "stored SQL-backed market evidence was used as rent source" in result.report_md
    )
    assert result.facts_json["market_evidence_used_as_rent_source"] is True
    assert result.facts_json["market_evidence_used_for_comparison"] is False
    assert result.facts_json["market_comps_used"] is True


def test_manual_primary_report_questions_and_facts_show_comparison(db_session):
    for rent in (50_000, 55_000, 60_000):
        _item(db_session, rent_per_m2_rub=None, rent_rub_per_month=rent)
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "investment_purchase_price": 1_000_000,
            "estimated_monthly_rent": 100_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    result = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=Listing(external_id="l1", title="x", area_m2=50),
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert "manual rent remained primary" in result.report_md
    assert "evidence was used for comparison" in result.report_md
    assert result.facts_json["market_evidence_used_for_comparison"] is True
    assert result.facts_json["market_evidence_used_as_rent_source"] is False
    assert result.facts_json["market_comps_used"] is False
    assert not any(
        "расчет не использует рыночные comps" in question
        for question in result.questions_json["items"]
    )


def test_insufficient_comps_adds_human_review_question(db_session):
    _item(db_session, rent_per_m2_rub=None, rent_rub_per_month=50_000)
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "investment_purchase_price": 1_000_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )
    result = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=Listing(external_id="l1", title="x", area_m2=50),
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert "insufficient_market_comps" in result.risks_json["flags"]
    assert any(
        "ручную проверку рыночной аренды" in question
        for question in result.questions_json["items"]
    )


def test_matching_policy_defaults_to_none_and_effective_same_listing(db_session):
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment", {"use_market_evidence": True}
    )
    assert cfg.market_evidence_matching_policy is None
    good = _item(db_session, listing_external_id="target", content_hash="target")
    _item(db_session, listing_external_id="other", content_hash="other")
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="target",
    )
    assert [i.id for i in ctx.items] == [good.id]
    assert ctx.config.matching_policy == "same_listing"


def test_same_location_key_selects_cross_listing_and_excludes_bad_candidates(
    db_session,
):
    good1 = _item(
        db_session,
        listing_external_id="other1",
        location_key="loc-x",
        content_hash="g1",
    )
    good2 = _item(
        db_session,
        listing_external_id="other2",
        location_key="loc-x",
        content_hash="g2",
        source_url="https://e.test/2",
        source_url_normalized="https://e.test/2",
    )
    good3 = _item(
        db_session,
        listing_external_id="target",
        location_key="loc-x",
        content_hash="g3",
        source_url="https://e.test/3",
        source_url_normalized="https://e.test/3",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="wrong",
        content_hash="wrongloc",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key=None,
        content_hash="missingloc",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        asset_type="flat",
        content_hash="flat",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        deal_type="sale",
        content_hash="sale",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        expires_at=(AS_OF - timedelta(days=1)).replace(tzinfo=None),
        content_hash="expired",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        checked_at=(AS_OF - timedelta(days=31)).replace(tzinfo=None),
        content_hash="old",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        confidence=0.1,
        content_hash="low",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        source_url=None,
        source_url_normalized=None,
        content_hash="nosrc",
    )
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc-x",
        rent_per_m2_rub=None,
        rent_rub_per_month=None,
        content_hash="norent",
    )
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "market_evidence_matching_policy": "same_location_key",
            "market_evidence_location_key": "loc-x",
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="target",
    )
    assert {i.id for i in ctx.items} == {good1.id, good2.id, good3.id}
    assert ctx.excluded_counts_by_reason["wrong_location_key"] == 2
    assert ctx.excluded_counts_by_reason["wrong_asset_type"] == 1
    assert ctx.excluded_counts_by_reason["wrong_deal_type"] == 1
    assert ctx.excluded_counts_by_reason["expired"] == 1
    assert ctx.excluded_counts_by_reason["too_old"] == 1
    assert ctx.excluded_counts_by_reason["low_confidence"] == 1
    assert ctx.excluded_counts_by_reason["missing_source"] == 1
    assert ctx.excluded_counts_by_reason["missing_rent_metric"] == 1


def test_cross_listing_rent_source_is_capped_and_facted(db_session):
    for n, rent in enumerate((50_000, 60_000, 70_000), start=1):
        _item(
            db_session,
            listing_external_id=f"other{n}",
            location_key="loc-x",
            rent_per_m2_rub=None,
            rent_rub_per_month=rent,
            source_url=f"https://e.test/{n}",
            source_url_normalized=f"https://e.test/{n}",
        )
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "market_evidence_matching_policy": "same_location_key",
            "market_evidence_location_key": "loc-x",
            "investment_purchase_price": 1_000_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
            "vacancy_rate": 0,
            "capex_initial": 0,
            "min_gross_yield": 0.01,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="target",
    )
    result = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=Listing(external_id="target", title="x", area_m2=50),
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert result.verdict != "strong"
    assert result.verdict == "medium"
    assert "cross_listing_evidence_requires_human_review" in result.risks_json["flags"]
    assert "cross_listing_evidence_without_quality_score" in result.risks_json["flags"]
    facts = result.facts_json["investment_metrics"]["market_evidence"]
    assert facts["matching_policy"] == "same_location_key"
    assert facts["cross_listing_reuse_enabled"] is True
    assert facts["comp_quality_scoring_used"] is False
    assert facts["cross_listing_verdict_cap_applied"] is True
    assert facts["selected_external_listing_count"] == 3
    assert any("cross-listing" in q for q in result.questions_json["items"])


def test_same_location_key_missing_location_key_review_without_manual_rent(db_session):
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "market_evidence_matching_policy": "same_location_key",
            "investment_purchase_price": 1_000_000,
        },
    )
    ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="target",
    )
    result = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=Listing(external_id="target", title="x", area_m2=50),
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert "market_evidence_location_key_missing" in result.risks_json["flags"]
    assert result.verdict == "review"


def test_manual_primary_not_capped_by_missing_cross_listing_evidence(db_session):
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "market_evidence_matching_policy": "same_location_key",
            "market_evidence_location_key": "loc-x",
            "investment_purchase_price": 1_000_000,
            "estimated_monthly_rent": 100_000,
            "asset_type": "commercial",
            "deal_type": "sale",
            "opex_ratio": 0.1,
            "vacancy_rate": 0,
            "capex_initial": 0,
            "min_gross_yield": 0.01,
            "min_noi_yield": 0.01,
            "max_payback_years": 10,
        },
    )
    ctx = select_market_evidence(
        candidates=[],
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="target",
    )
    result = InvestmentAnalysisProvider("commercial_sale_investment").analyze(
        listing=Listing(external_id="target", title="x", area_m2=50),
        snapshot=None,
        input_hash="h",
        config=cfg,
        market_evidence_context=ctx,
    )
    assert result.facts_json["investment_metrics"]["rent_estimate_source"] == "manual"
    assert result.verdict == "strong"
