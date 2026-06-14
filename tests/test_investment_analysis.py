from app.analysis.config import AnalysisConfig
from app.analysis.investment import calculate_investment_metrics
from app.analysis.provider import InvestmentAnalysisProvider, get_analysis_provider
from app.analysis.service import ListingAnalysisService
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_search_match import ListingSearchMatch
from app.models.search_job import SearchJob


def _listing(db_session, external_id="inv-1", price=9_500_000):
    listing = Listing(
        external_id=external_id,
        url=f"https://www.avito.ru/item/{external_id}",
        title="Инвестиционный объект",
        price=price,
        address="Санкт-Петербург",
        area_m2=50,
    )
    db_session.add(listing)
    db_session.flush()
    return listing


def _filters(**overrides):
    data = {
        "analysis_profile": "commercial_sale_investment",
        "asset_type": "commercial",
        "deal_type": "sale",
        "investment_purchase_price": 9_500_000,
        "estimated_monthly_rent": 120_000,
        "opex_ratio": 0.25,
        "vacancy_rate": 0.08,
        "capex_initial": 500_000,
        "min_gross_yield": 0.12,
        "min_noi_yield": 0.08,
        "max_payback_years": 12,
    }
    data.update(overrides)
    return data


def test_investment_config_whitelist_and_hash_changes():
    base = AnalysisConfig.from_search_filters("commercial_sale_investment", _filters(irrelevant="ignored"))
    same = AnalysisConfig.from_search_filters("commercial_sale_investment", _filters(irrelevant="changed"))
    changed = AnalysisConfig.from_search_filters("commercial_sale_investment", _filters(investment_purchase_price=9_600_000))
    assert base.asset_type == "commercial"
    assert base.deal_type == "sale"
    assert base.investment_purchase_price == 9_500_000
    assert base.hash() == same.hash()
    assert base.hash() != changed.hash()
    assert AnalysisConfig.from_search_filters("flat_sale_investment", {"investment_allow_listing_price_as_purchase_price": True}).investment_allow_listing_price_as_purchase_price is True


def test_investment_bool_hash_payload_absent_true_and_false():
    absent = AnalysisConfig.from_search_filters("commercial_sale_investment", {})
    explicit_true = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {"investment_allow_listing_price_as_purchase_price": True},
    )
    explicit_false = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {"investment_allow_listing_price_as_purchase_price": False},
    )
    assert "investment_allow_listing_price_as_purchase_price" not in absent.to_hash_payload()
    assert (
        explicit_true.to_hash_payload()[
            "investment_allow_listing_price_as_purchase_price"
        ]
        is True
    )
    assert (
        explicit_false.to_hash_payload()[
            "investment_allow_listing_price_as_purchase_price"
        ]
        is False
    )
    assert absent.hash() != explicit_true.hash()
    assert explicit_true.hash() != explicit_false.hash()


def test_existing_profiles_do_not_receive_investment_hash_payload_or_facts(db_session):
    expected = {
        "default": ("mock", "deterministic-local"),
        "commercial_rent": ("deterministic", "commercial-rent-rules-v0"),
        "flat_sale": ("deterministic", "flat-sale-rules-v0"),
        "flat_rent": ("deterministic", "flat-rent-rules-v0"),
    }
    investment_only_keys = {
        "investment_purchase_price",
        "investment_price_basis",
        "investment_allow_listing_price_as_purchase_price",
        "estimated_monthly_rent",
        "opex_ratio",
        "opex_monthly",
        "vacancy_rate",
        "capex_initial",
        "min_gross_yield",
        "min_noi_yield",
        "max_payback_years",
        "asset_type",
        "deal_type",
    }
    for profile, (model_provider, model_name) in expected.items():
        config = AnalysisConfig.from_search_filters(profile, {})
        assert investment_only_keys.isdisjoint(config.to_hash_payload())
        listing = _listing(db_session, external_id=f"existing-{profile}")
        provider = get_analysis_provider(profile)
        result = provider.analyze(
            listing=listing,
            snapshot=None,
            input_hash="x",
            config=config,
        )
        assert provider.profile == profile
        assert provider.model_provider == model_provider
        assert provider.model_name == model_name
        assert result.model_provider == model_provider
        assert result.model_name == model_name
        assert "investment_metrics" not in result.facts_json


def test_metric_calculation_complete_and_opex_ratio():
    metrics = calculate_investment_metrics(
        purchase_price=9_500_000,
        purchase_price_source="filters_json.investment_purchase_price",
        estimated_monthly_rent=120_000,
        opex_ratio=0.25,
        vacancy_rate=0.08,
        capex_initial=500_000,
    )
    assert metrics.annual_gross_income == 1_440_000
    assert metrics.vacancy_loss_annual == 115_200
    assert metrics.effective_gross_income == 1_324_800
    assert metrics.opex_annual == 331_200
    assert metrics.noi_annual == 993_600
    assert metrics.total_initial_outlay == 10_000_000
    assert metrics.gross_yield_on_price == 0.1516
    assert metrics.gross_yield_on_total_outlay == 0.144
    assert metrics.noi_yield_on_price == 0.1046
    assert metrics.noi_yield_on_total_outlay == 0.0994
    assert metrics.payback_years == 10.06


def test_metric_missing_and_invalid_assumptions_fail_safely():
    metrics = calculate_investment_metrics(
        purchase_price=0,
        purchase_price_source=None,
        estimated_monthly_rent=-1,
        opex_monthly=-1,
        opex_ratio=2,
        vacancy_rate=None,
        capex_initial=-5,
    )
    assert "invalid_purchase_price" in metrics.flags
    assert "invalid_estimated_monthly_rent" in metrics.flags
    assert "invalid_opex_monthly" in metrics.flags
    assert "invalid_opex_ratio" in metrics.flags
    assert "vacancy_rate_missing_assumed_zero" in metrics.flags
    assert "invalid_capex_initial" in metrics.flags
    assert metrics.gross_yield_on_price is None


def test_opex_monthly_takes_precedence():
    metrics = calculate_investment_metrics(
        purchase_price=1_000_000,
        purchase_price_source="manual",
        estimated_monthly_rent=10_000,
        opex_monthly=1_000,
        opex_ratio=0.9,
        vacancy_rate=0,
        capex_initial=0,
    )
    assert metrics.opex_annual == 12_000
    assert metrics.opex_monthly_used == 1_000
    assert metrics.opex_ratio_used is None


def test_provider_metadata_facts_thresholds_and_fallback_policy(db_session):
    listing = _listing(db_session)
    provider = get_analysis_provider("commercial_sale_investment")
    assert isinstance(provider, InvestmentAnalysisProvider)
    result = provider.analyze(listing=listing, snapshot=None, input_hash="x", config=AnalysisConfig.from_search_filters("commercial_sale_investment", _filters()))
    assert result.model_provider == "deterministic"
    assert result.model_name == "commercial-sale-investment-rules-v0"
    assert result.verdict == "strong"
    assert result.facts_json["manual_assumptions_only"] is True
    assert result.facts_json["market_comps_used"] is False
    assert result.facts_json["external_research_used"] is False
    assert result.facts_json["llm_used"] is False
    assert result.facts_json["rag_used"] is False
    assert result.facts_json["agent_used"] is False
    missing = provider.analyze(listing=listing, snapshot=None, input_hash="x", config=AnalysisConfig.from_search_filters("commercial_sale_investment", _filters(investment_purchase_price=None)))
    assert missing.verdict == "review"
    assert "missing_investment_purchase_price" in missing.risks_json["flags"]
    fallback = provider.analyze(listing=listing, snapshot=None, input_hash="x", config=AnalysisConfig.from_search_filters("commercial_sale_investment", _filters(investment_purchase_price=None, investment_allow_listing_price_as_purchase_price=True, investment_price_basis="listing_price_as_purchase_price")))
    assert fallback.facts_json["purchase_price_source"] == "listing.price"
    assert "purchase_price_source_requires_human_confirmation" in fallback.risks_json["flags"]


def test_flat_profile_rent_and_asset_mismatch_force_review(db_session):
    listing = _listing(db_session, "inv-flat", 10_500_000)
    provider = get_analysis_provider("flat_sale_investment")
    result = provider.analyze(listing=listing, snapshot=None, input_hash="x", config=AnalysisConfig.from_search_filters("flat_sale_investment", _filters(analysis_profile="flat_sale_investment", asset_type="commercial", deal_type="rent", investment_purchase_price=None)))
    assert result.verdict == "review"
    assert "deal_type_rent_not_sale" in result.risks_json["flags"]
    assert "asset_type_profile_mismatch" in result.risks_json["flags"]
    assert result.facts_json["investment_metrics"]["purchase_price"] is None


def test_search_analysis_idempotency_and_hash_invalidation_no_side_effects(db_session):
    listing = _listing(db_session, "inv-search")
    search = SearchJob(name="inv", source_url="https://example.test", filters_json=_filters())
    db_session.add(search)
    db_session.flush()
    db_session.add(ListingSearchMatch(search_job_id=search.id, listing_external_id=listing.external_id, first_seen_at=listing.first_seen_at, last_seen_at=listing.last_seen_at))
    db_session.flush()
    service = ListingAnalysisService(db_session, provider=get_analysis_provider("commercial_sale_investment"))
    first = service.analyze_search_matches(search.id, 10)
    second = service.analyze_search_matches(search.id, 10)
    assert len(first) == 1
    assert second == []
    original_filters = dict(search.filters_json)
    search.filters_json = _filters(estimated_monthly_rent=130_000)
    db_session.flush()
    third = service.analyze_search_matches(search.id, 10)
    assert len(third) == 1
    assert db_session.query(ListingAnalysis).count() == 2
    assert original_filters["estimated_monthly_rent"] == 120_000
    assert db_session.query(SearchJob).filter(SearchJob.filters_json == search.filters_json).count() == 1
