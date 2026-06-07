import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.analysis.config import AnalysisConfig
from app.analysis.provider import (
    CommercialRentDeterministicAnalysisProvider,
    FlatRentDeterministicAnalysisProvider,
    FlatSaleDeterministicAnalysisProvider,
    ListingAnalysisResult,
    _flat_verdict,
    _verdict,
    get_analysis_provider,
)
from app.analysis.service import (
    ListingAnalysisService,
    calculate_input_hash,
    resolve_search_analysis_profile,
)
from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_snapshot import ListingSnapshot
from app.models.listing_search_match import ListingSearchMatch
from app.repositories.alert_repository import AlertRepository
from app.repositories.listing_analysis_repository import ListingAnalysisRepository
from app.repositories.listing_search_match_repository import ListingSearchMatchRepository
from app.repositories.search_repository import SearchRepository


def _listing(db_session, external_id: str = "ext-1", **kwargs) -> Listing:
    listing = Listing(
        external_id=external_id,
        url=f"https://www.avito.ru/item/{external_id}",
        title=kwargs.pop("title", "Тестовая квартира"),
        price=kwargs.pop("price", 10_000_000),
        address=kwargs.pop("address", "Санкт-Петербург"),
        area_m2=kwargs.pop("area_m2", 42.0),
        rooms=kwargs.pop("rooms", "1"),
        **kwargs,
    )
    db_session.add(listing)
    db_session.flush()
    return listing


def _snapshot(db_session, external_id: str = "ext-1", **kwargs) -> ListingSnapshot:
    snapshot = ListingSnapshot(
        external_id=external_id,
        title=kwargs.pop("title", "Тестовая квартира snapshot"),
        price=kwargs.pop("price", 10_000_000),
        payload_json=kwargs.pop("payload_json", {"source": "test"}),
        **kwargs,
    )
    db_session.add(snapshot)
    db_session.flush()
    return snapshot


def _alert(db_session, external_id: str = "ext-1") -> AlertSent:
    return AlertRepository(db_session).create(
        listing_external_id=external_id,
        dedupe_key=f"telegram:{external_id}",
    )


def test_analysis_config_defaults_and_filter_overrides_are_whitelisted():
    default = AnalysisConfig.from_search_filters(profile="commercial_rent")
    overridden = AnalysisConfig.from_search_filters(
        profile="commercial_rent",
        filters_json={
            "max_age_hours": 24,
            "estimated_monthly_rent": 180_000,
            "irrelevant_key": "ignored",
        },
    )
    ignored_only = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"irrelevant_key": "ignored"}
    )

    assert default.min_area_m2 == 40.0
    assert default.max_area_m2 == 150.0
    assert default.max_price == 200_000.0
    assert default.max_age_hours == 72.0
    assert default.max_price_per_m2 == 5_000.0
    assert default.suspicious_total_price == 5_000.0
    assert default.suspicious_low_price_per_m2 == 300.0
    assert overridden.max_age_hours == 24.0
    assert overridden.estimated_monthly_rent == 180_000.0
    assert ignored_only == default


def test_analysis_config_profile_defaults_for_flat_profiles():
    flat_sale = AnalysisConfig.from_search_filters(profile="flat_sale")
    flat_rent = AnalysisConfig.from_search_filters(profile="flat_rent")

    assert flat_sale.min_area_m2 == 25.0
    assert flat_sale.max_area_m2 == 90.0
    assert flat_sale.max_price == 15_000_000.0
    assert flat_sale.max_age_hours == 72.0
    assert flat_sale.suspicious_low_price_per_m2 == 100_000.0
    assert flat_sale.max_price_per_m2 == 350_000.0
    assert flat_rent.min_area_m2 == 20.0
    assert flat_rent.max_area_m2 == 90.0
    assert flat_rent.max_price == 100_000.0
    assert flat_rent.max_age_hours == 72.0
    assert flat_rent.suspicious_low_price_per_m2 == 600.0
    assert flat_rent.max_price_per_m2 == 3_000.0


def test_analysis_config_hash_stability_and_invalidation():
    base = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"irrelevant_key": "a"}
    )
    same = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"irrelevant_key": "b"}
    )
    changed_age = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"max_age_hours": 24}
    )
    changed_rent = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"estimated_monthly_rent": 1}
    )

    assert base.hash() == same.hash()
    assert base.hash() != changed_age.hash()
    assert base.hash() != changed_rent.hash()
    assert base.hash() == AnalysisConfig.from_search_filters(profile="commercial_rent").hash()


def test_analysis_config_hash_payload_omits_none_and_null_filters_are_missing():
    default = AnalysisConfig.from_search_filters(profile="commercial_rent")
    with_null = AnalysisConfig.from_search_filters(
        profile="commercial_rent",
        filters_json={"max_price": None, "estimated_monthly_rent": None},
    )
    payload = default.to_hash_payload()

    assert "min_price_per_m2" not in payload
    assert "estimated_monthly_rent" not in payload
    assert "opex_ratio" not in payload
    assert with_null == default
    assert with_null.hash() == default.hash()


def test_calculate_input_hash_uses_analysis_config_hash_only_for_filters(db_session):
    listing = _listing(db_session, external_id="hash-config-1")
    snapshot = _snapshot(db_session, external_id="hash-config-1")
    base = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"irrelevant_key": "a"}
    )
    same = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"irrelevant_key": "b"}
    )
    changed_age = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"max_age_hours": 24}
    )
    changed_rent = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"estimated_monthly_rent": 1}
    )

    kwargs = {
        "profile": "commercial_rent",
        "analysis_version": "commercial-rent-v0",
        "context_key": "search:1",
    }
    base_hash = calculate_input_hash(listing, snapshot, config=base, **kwargs)

    assert base_hash == calculate_input_hash(listing, snapshot, config=base, **kwargs)
    assert base_hash == calculate_input_hash(listing, snapshot, config=same, **kwargs)
    assert base_hash != calculate_input_hash(listing, snapshot, config=changed_age, **kwargs)
    assert base_hash != calculate_input_hash(listing, snapshot, config=changed_rent, **kwargs)


def test_search_analysis_uses_filters_json_config_and_dedupes_same_input_hash(db_session):
    search = _search(
        db_session,
        name="config-search",
        filters_json={
            "analysis_profile": "commercial_rent",
            "max_age_hours": 24,
            "irrelevant_key": "ignored",
        },
    )
    _listing(
        db_session,
        external_id="config-search-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
        published_at=datetime.now(UTC) - timedelta(hours=30),
    )
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "config-search-1")
    service = ListingAnalysisService(
        db_session, provider=get_analysis_provider(resolve_search_analysis_profile(search))
    )

    first = service.analyze_search_matches(search.id, limit=20)
    second = service.analyze_search_matches(search.id, limit=20)

    assert len(first) == 1
    assert second == []
    analysis = first[0]
    assert analysis.facts_json["analysis_config"]["max_age_hours"] == 24
    assert analysis.facts_json["freshness_status"] == "stale"
    assert "stale_publication_sanity_cap" in analysis.risks_json["flags"]
    rows = db_session.scalars(select(ListingAnalysis)).all()
    assert len(rows) == 1


def test_search_analysis_reanalyzes_when_config_hash_changes(db_session):
    search = _search(
        db_session,
        name="config-invalidation",
        filters_json={"analysis_profile": "commercial_rent", "max_age_hours": 72},
    )
    _listing(
        db_session,
        external_id="config-invalidation-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
        published_at=datetime.now(UTC) - timedelta(hours=30),
    )
    ListingSearchMatchRepository(db_session).upsert_match(
        search.id, "config-invalidation-1"
    )
    service = ListingAnalysisService(
        db_session, provider=get_analysis_provider(resolve_search_analysis_profile(search))
    )

    first = service.analyze_search_matches(search.id, limit=20)
    search.filters_json = {"analysis_profile": "commercial_rent", "max_age_hours": 24}
    db_session.flush()
    second = service.analyze_search_matches(search.id, limit=20)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].input_hash != second[0].input_hash
    assert first[0].status == "stale"
    assert second[0].status == "success"
    assert second[0].facts_json["analysis_config"]["max_age_hours"] == 24
    assert second[0].facts_json["freshness_status"] == "stale"
    rows = db_session.scalars(
        select(ListingAnalysis).order_by(ListingAnalysis.id.asc())
    ).all()
    assert len(rows) == 2


def test_commercial_rent_default_freshness_window_remains_72h():
    result = CommercialRentDeterministicAnalysisProvider().analyze(
        listing=Listing(
            external_id="fresh-default",
            url="https://www.avito.ru/item/fresh-default",
            title="Офис 60 м²",
            price=120_000,
            address="Санкт-Петербург",
            area_m2=60,
            published_at=datetime.now(UTC) - timedelta(hours=30),
        ),
        snapshot=None,
        input_hash="hash",
    )

    assert result.facts_json["freshness_status"] == "recent"
    assert "stale_publication_sanity_cap" not in result.risks_json["flags"]


def test_commercial_rent_config_max_age_hours_marks_30h_listing_stale():
    result = CommercialRentDeterministicAnalysisProvider().analyze(
        listing=Listing(
            external_id="fresh-override",
            url="https://www.avito.ru/item/fresh-override",
            title="Офис 60 м²",
            price=120_000,
            address="Санкт-Петербург",
            area_m2=60,
            published_at=datetime.now(UTC) - timedelta(hours=30),
        ),
        snapshot=None,
        input_hash="hash",
        config=AnalysisConfig.from_search_filters(
            profile="commercial_rent", filters_json={"max_age_hours": 24}
        ),
    )

    assert result.facts_json["freshness_status"] == "stale"
    assert "stale_publication_sanity_cap" in result.risks_json["flags"]


def test_commercial_rent_config_max_age_hours_below_24_caps_freshness():
    provider = CommercialRentDeterministicAnalysisProvider()
    config = AnalysisConfig.from_search_filters(
        profile="commercial_rent", filters_json={"max_age_hours": 12}
    )

    stale = provider.analyze(
        listing=Listing(
            external_id="fresh-12-stale",
            url="https://www.avito.ru/item/fresh-12-stale",
            title="Офис 60 м²",
            price=120_000,
            address="Санкт-Петербург",
            area_m2=60,
            published_at=datetime.now(UTC) - timedelta(hours=18),
        ),
        snapshot=None,
        input_hash="hash",
        config=config,
    )
    fresh = provider.analyze(
        listing=Listing(
            external_id="fresh-12-fresh",
            url="https://www.avito.ru/item/fresh-12-fresh",
            title="Офис 60 м²",
            price=120_000,
            address="Санкт-Петербург",
            area_m2=60,
            published_at=datetime.now(UTC) - timedelta(hours=6),
        ),
        snapshot=None,
        input_hash="hash",
        config=config,
    )

    assert stale.facts_json["freshness_status"] == "stale"
    assert fresh.facts_json["freshness_status"] == "fresh"


def test_commercial_rent_provider_config_does_not_leak_between_analyses():
    provider = CommercialRentDeterministicAnalysisProvider()
    narrow_config = AnalysisConfig.from_search_filters(
        profile="commercial_rent",
        filters_json={"max_age_hours": 12, "max_price": 1},
    )

    provider.analyze(
        listing=Listing(
            external_id="leak-first",
            url="https://www.avito.ru/item/leak-first",
            title="Офис 60 м²",
            price=120_000,
            address="Санкт-Петербург",
            area_m2=60,
            published_at=datetime.now(UTC) - timedelta(hours=18),
        ),
        snapshot=None,
        input_hash="hash",
        config=narrow_config,
    )
    default_result = provider.analyze(
        listing=Listing(
            external_id="leak-second",
            url="https://www.avito.ru/item/leak-second",
            title="Офис 60 м²",
            price=120_000,
            address="Санкт-Петербург",
            area_m2=60,
            published_at=datetime.now(UTC) - timedelta(hours=30),
        ),
        snapshot=None,
        input_hash="hash",
    )

    assert provider.target_max_price == 200_000.0
    assert provider.target_freshness_hours == 72.0
    assert default_result.facts_json["freshness_status"] == "recent"
    assert "over_budget" not in default_result.risks_json["flags"]


def test_creates_analysis_for_alerted_listing(db_session):
    _listing(db_session)
    snapshot = _snapshot(db_session)
    _alert(db_session)

    analyses = ListingAnalysisService(db_session).analyze_alerted_listings(limit=20)

    assert len(analyses) == 1
    analysis = analyses[0]
    assert analysis.listing_external_id == "ext-1"
    assert analysis.snapshot_id == snapshot.id
    assert analysis.status == "success"
    assert analysis.model_provider == "mock"
    assert analysis.facts_json["has_snapshot"] is True
    assert "Listing analysis: ext-1" in analysis.report_md


def test_analyzes_listing_without_snapshot(db_session):
    _listing(db_session, external_id="no-snapshot")

    analysis = ListingAnalysisService(db_session).analyze_listing("no-snapshot")

    assert analysis.status == "success"
    assert analysis.snapshot_id is None
    assert analysis.facts_json["has_snapshot"] is False
    assert "missing_snapshot" in analysis.risks_json["flags"]


def test_list_alerted_listings_without_analysis_honors_limit(db_session):
    for idx in range(3):
        external_id = f"ext-{idx}"
        _listing(db_session, external_id=external_id)
        _alert(db_session, external_id=external_id)
    repo = ListingAnalysisRepository(db_session)
    repo.create_or_update_analysis(
        listing_external_id="ext-0",
        snapshot_id=None,
        profile="default",
        status="success",
        analysis_version="mock-v1",
        input_hash="hash-0",
    )

    listings = repo.list_alerted_listings_without_analysis(limit=1)

    assert [listing.external_id for listing in listings] == ["ext-1"]


def test_idempotent_rerun_with_same_input_hash(db_session):
    _listing(db_session)
    _snapshot(db_session)

    service = ListingAnalysisService(db_session)
    first = service.analyze_listing("ext-1")
    first_id = first.id
    second = service.analyze_listing("ext-1")

    assert second.id == first_id
    rows = db_session.scalars(select(ListingAnalysis)).all()
    assert len(rows) == 1
    assert rows[0].status == "success"


class FailingProvider:
    profile = "default"
    analysis_version = "mock-v1"
    model_provider = "mock"
    model_name = "failing-test"

    def analyze(self, *, listing, snapshot, input_hash):
        raise RuntimeError("provider exploded")


class CustomSuccessProvider:
    profile = "default"
    analysis_version = "mock-v1"
    model_provider = "mock"
    model_name = "custom-test"

    def analyze(self, *, listing, snapshot, input_hash):
        return ListingAnalysisResult(
            score=0.5,
            verdict="custom",
            facts_json={"external_id": listing.external_id},
            risks_json={},
            questions_json={},
            report_md="custom report",
            model_provider=self.model_provider,
            model_name=self.model_name,
        )


def test_failed_analysis_records_error(db_session):
    _listing(db_session)

    analysis = ListingAnalysisService(
        db_session, provider=FailingProvider()
    ).analyze_listing("ext-1")

    assert analysis.status == "failed"
    assert analysis.error_type == "RuntimeError"
    assert analysis.error_message == "provider exploded"


def test_cli_analyze_listing_works(db_session, monkeypatch, capsys):
    _listing(db_session)
    _snapshot(db_session)
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_analyze_listing(Namespace(external_id="ext-1"))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["analysis"]["listing_external_id"] == "ext-1"
    assert output["analysis"]["status"] == "success"


def test_cli_analyze_alerted_listings_works_with_limit(db_session, monkeypatch, capsys):
    for idx in range(2):
        external_id = f"cli-ext-{idx}"
        _listing(db_session, external_id=external_id)
        _alert(db_session, external_id=external_id)
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_analyze_alerted_listings(Namespace(limit=1))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["limit"] == 1
    assert output["count"] == 1
    assert output["analyses"][0]["listing_external_id"] == "cli-ext-0"


def test_alerted_listing_query_does_not_require_alert_created_at(db_session):
    assert not hasattr(AlertSent, "created_at")
    _listing(db_session)
    _alert(db_session)

    listings = ListingAnalysisRepository(
        db_session
    ).list_alerted_listings_without_analysis(limit=20)

    assert [listing.external_id for listing in listings] == ["ext-1"]


def _commercial_result(**kwargs):
    now = datetime.now(UTC)
    description = kwargs.pop("description", "")
    listing = Listing(
        external_id=kwargs.pop("external_id", "comm-1"),
        url=kwargs.pop("url", "https://www.avito.ru/item/comm-1"),
        title=kwargs.pop("title", "Офис свободного назначения"),
        price=kwargs.pop("price", 120_000),
        address=kwargs.pop("address", "Санкт-Петербург, Невский проспект"),
        area_m2=kwargs.pop("area_m2", 60.0),
        published_label=kwargs.pop("published_label", "сегодня"),
        published_at=kwargs.pop("published_at", now - timedelta(hours=2)),
        **kwargs,
    )
    snapshot = ListingSnapshot(
        id=1,
        external_id=listing.external_id,
        title=listing.title,
        price=listing.price,
        published_label=listing.published_label,
        published_at=listing.published_at,
        payload_json={"description": description},
    )
    return CommercialRentDeterministicAnalysisProvider().analyze(
        listing=listing, snapshot=snapshot, input_hash="hash"
    )


def test_get_analysis_provider_supports_default_commercial_rent_and_flat_sale():
    default_provider = get_analysis_provider("default")
    commercial_provider = get_analysis_provider("commercial_rent")
    flat_provider = get_analysis_provider("flat_sale")

    assert default_provider.profile == "default"
    assert commercial_provider.profile == "commercial_rent"
    assert commercial_provider.analysis_version == "commercial-rent-v0"
    assert commercial_provider.model_provider == "deterministic"
    assert commercial_provider.model_name == "commercial-rent-rules-v0"
    assert flat_provider.profile == "flat_sale"
    assert flat_provider.analysis_version == "flat-sale-v0"


def test_get_analysis_provider_unknown_profile_fails_safely():
    try:
        get_analysis_provider("unknown")
    except ValueError as exc:
        assert "unsupported analysis profile" in str(exc)
    else:
        raise AssertionError("unknown analysis profile must fail")


def test_commercial_rent_calculates_price_per_m2():
    result = _commercial_result(price=120_000, area_m2=60)

    assert result.facts_json["price_per_m2"] == 2000


def test_commercial_rent_freshness_statuses():
    now = datetime.now(UTC)

    assert (
        _commercial_result(published_at=now - timedelta(hours=1)).facts_json[
            "freshness_status"
        ]
        == "fresh"
    )
    assert (
        _commercial_result(published_at=now - timedelta(hours=48)).facts_json[
            "freshness_status"
        ]
        == "recent"
    )
    assert (
        _commercial_result(published_at=now - timedelta(hours=96)).facts_json[
            "freshness_status"
        ]
        == "stale"
    )
    assert (
        _commercial_result(published_at=None).facts_json["freshness_status"]
        == "unknown"
    )


def test_commercial_rent_target_fit_logic():
    good = _commercial_result(price=120_000, area_m2=60)
    too_large = _commercial_result(price=250_000, area_m2=200)

    assert good.facts_json["target_fit"]["area_fit"] is True
    assert good.facts_json["target_fit"]["price_fit"] is True
    assert good.facts_json["target_fit"]["freshness_fit"] is True
    assert good.facts_json["target_fit"]["overall"] == "good"
    assert too_large.facts_json["target_fit"]["area_fit"] is False
    assert too_large.facts_json["target_fit"]["price_fit"] is False
    assert too_large.facts_json["target_fit"]["overall"] == "partial"


def test_commercial_rent_parking_storage_garage_risk_flags():
    result = _commercial_result(title="Гараж и машиноместо рядом с офисом")

    assert "parking_storage_garage_keyword" in result.risks_json["flags"]


def test_commercial_rent_sublease_partial_area_risk_flags():
    result = _commercial_result(
        description="Субаренда, часть помещения, отдельное рабочее место"
    )

    assert "sublease_or_partial_area_ambiguity" in result.risks_json["flags"]


def test_commercial_rent_normal_place_phrase_does_not_trigger_partial_area_risk():
    result = _commercial_result(description="Проходимое место рядом с метро")

    assert "sublease_or_partial_area_ambiguity" not in result.risks_json["flags"]


def test_commercial_rent_warehouse_or_production_risk_for_service_use():
    warehouse = _commercial_result(title="Склад 60 м²")
    production = _commercial_result(title="Производство 60 м²")

    assert "warehouse_or_production_for_service_use" in warehouse.risks_json["flags"]
    assert "warehouse_or_production_for_service_use" in production.risks_json["flags"]


def test_commercial_rent_sanity_caps_suspicious_low_total_and_per_m2():
    result = _commercial_result(
        title="Свободного назначения, 100 м²",
        price=4_000,
        area_m2=100,
    )

    assert "suspicious_total_price" in result.risks_json["flags"]
    assert "suspicious_low_price_per_m2" in result.risks_json["flags"]
    assert result.score <= 70
    assert result.verdict == "review"
    assert result.facts_json["sanity"]["score_cap"] == 70


def test_commercial_rent_sanity_downgrades_storage_with_low_price():
    result = _commercial_result(title="Склад, 71 м²", price=500, area_m2=71)

    assert "suspicious_total_price" in result.risks_json["flags"]
    assert "suspicious_low_price_per_m2" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_commercial_rent_missing_area_sanity_cap_keeps_existing_missing_area():
    result = _commercial_result(title="Офис у метро", price=120_000, area_m2=None)

    assert "missing_area" in result.risks_json["flags"]
    assert "missing_area_sanity_cap" in result.risks_json["flags"]
    assert result.score <= 70
    assert result.verdict == "review"


def test_commercial_rent_stale_publication_sanity_cap_blocks_strong():
    result = _commercial_result(
        title="Офис 65 м²",
        price=120_000,
        area_m2=65,
        published_at=datetime.now(UTC) - timedelta(days=5),
    )

    assert "stale_publication" in result.risks_json["flags"]
    assert "stale_publication_sanity_cap" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_commercial_rent_title_area_mismatch_forces_review():
    result = _commercial_result(title="Идеальный офис 528 м2", area_m2=30)

    assert "area_parser_mismatch" in result.risks_json["flags"]
    assert result.facts_json["sanity"]["title_area_m2"] == 528.0
    assert result.score <= 65
    assert result.verdict == "review"


def test_commercial_rent_area_distance_does_not_create_mismatch():
    result = _commercial_result(title="Офис 300 метров от метро, 65 м²", area_m2=65)

    assert result.facts_json["sanity"]["title_area_m2"] == 65.0
    assert "area_parser_mismatch" not in result.risks_json["flags"]


def test_commercial_rent_storage_object_cannot_be_strong():
    result = _commercial_result(title="Кладовка 5 м²", price=20_000, area_m2=5)

    assert "storage_parking_garage_object" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_commercial_rent_parking_object_is_storage_object():
    for title in ("Место в паркинге 14 м²", "Парковочное место 14 м²"):
        result = _commercial_result(title=title, price=20_000, area_m2=14)

        assert "storage_parking_garage_object" in result.risks_json["flags"]


def test_commercial_rent_parking_amenity_is_not_storage_object():
    for title in (
        "Офис 65 м², есть парковка рядом",
        "Офис 65 м², есть паркинг",
        "Офис 65 м², подземный паркинг в БЦ",
        "Офис 65 м², паркинг рядом",
    ):
        result = _commercial_result(title=title)

        assert "storage_parking_garage_object" not in result.risks_json["flags"]


def test_flat_sale_parking_amenity_is_not_storage_object():
    for title in (
        "1-к квартира 42 м² 8/15 эт., есть паркинг",
        "1-к квартира 42 м² 8/15 эт., подземный паркинг в доме",
        "1-к квартира 42 м² 8/15 эт., паркинг рядом",
    ):
        result = _flat_result(title=title)

        assert "storage_parking_garage_object" not in result.risks_json["flags"]


def test_flat_rent_parking_amenity_is_not_storage_object():
    result = _flat_rent_result(
        title="1-к квартира 40 м² 8/15 эт. есть паркинг рядом залог без комиссии КУ мебель"
    )

    assert "storage_parking_garage_object" not in result.risks_json["flags"]


def test_flat_sale_missing_area_sanity_cap_blocks_strong():
    result = _flat_result(title="1-к квартира 42 м² 8/15 эт.", area_m2=None)

    assert "missing_area_sanity_cap" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_flat_sale_stale_high_score_sanity_cap_blocks_strong():
    result = _flat_result(published_at=datetime.now(UTC) - timedelta(days=5))

    assert "stale_publication_sanity_cap" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_flat_sale_title_area_mismatch_forces_non_strong_review():
    result = _flat_result(title="1-к квартира 104 м² 8/15 эт.", area_m2=42)

    assert "area_parser_mismatch" in result.risks_json["flags"]
    assert result.verdict == "review"


def test_flat_rent_missing_area_sanity_cap_blocks_strong():
    result = _flat_rent_result(title="1-к квартира 40 м² 8/15 эт.", area_m2=None)

    assert "missing_area_sanity_cap" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_flat_rent_stale_high_score_sanity_cap_blocks_strong():
    result = _flat_rent_result(published_at=datetime.now(UTC) - timedelta(days=5))

    assert "stale_publication_sanity_cap" in result.risks_json["flags"]
    assert result.verdict != "strong"


def test_flat_rent_title_area_mismatch_forces_non_strong_review():
    result = _flat_rent_result(title="1-к квартира 104 м² 8/15 эт.", area_m2=40)

    assert "area_parser_mismatch" in result.risks_json["flags"]
    assert result.verdict == "review"


def test_commercial_rent_score_is_clamped_to_0_and_100():
    low = _commercial_result(
        title="Склад гараж субаренда часть помещения",
        price=1_000_000,
        area_m2=500,
        published_at=datetime.now(UTC) - timedelta(days=30),
    )

    assert low.score == 0
    assert 0 <= _commercial_result().score <= 100


def test_commercial_rent_verdict_thresholds():
    assert _verdict(score=75, flags=[]) == "strong"
    assert _verdict(score=55, flags=[]) == "medium"
    assert _verdict(score=35, flags=[]) == "weak"
    assert _verdict(score=34, flags=[]) == "review"
    assert _verdict(score=80, flags=["missing_price"]) == "review"


def test_commercial_rent_report_contains_russian_sections():
    report = _commercial_result().report_md

    assert "## Вердикт" in report
    assert "## Факты" in report
    assert "## Риски" in report
    assert "## Что уточнить перед звонком" in report


def test_cli_analyze_listing_works_with_commercial_rent_profile(
    db_session, monkeypatch, capsys
):
    _listing(
        db_session,
        external_id="comm-cli-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
    )
    _snapshot(db_session, external_id="comm-cli-1", title="Офис 60 м²", price=120_000)
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_analyze_listing(
        Namespace(external_id="comm-cli-1", profile="commercial_rent")
    )

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["analysis"]["profile"] == "commercial_rent"
    assert output["analysis"]["analysis_version"] == "commercial-rent-v0"


def test_cli_analyze_alerted_listings_works_with_commercial_rent_profile(
    db_session, monkeypatch, capsys
):
    _listing(
        db_session,
        external_id="comm-alert-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
    )
    _alert(db_session, external_id="comm-alert-1")
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_analyze_alerted_listings(Namespace(limit=20, profile="commercial_rent"))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["count"] == 1
    assert output["analyses"][0]["profile"] == "commercial_rent"




def _flat_result(snapshot=True, **kwargs):
    now = datetime.now(UTC)
    listing = Listing(
        external_id=kwargs.pop("external_id", "flat-1"),
        url=kwargs.pop("url", "https://www.avito.ru/item/flat-1"),
        title=kwargs.pop("title", "1-к квартира 42 м² 8/15 эт."),
        price=kwargs.pop("price", 10_500_000),
        address=kwargs.pop("address", "Санкт-Петербург, Приморский район"),
        area_m2=kwargs.pop("area_m2", 42.0),
        published_label=kwargs.pop("published_label", "сегодня"),
        published_at=kwargs.pop("published_at", now - timedelta(hours=2)),
        **kwargs,
    )
    listing_snapshot = None
    if snapshot:
        listing_snapshot = ListingSnapshot(
            id=2,
            external_id=listing.external_id,
            title=listing.title,
            price=listing.price,
            published_label=listing.published_label,
            published_at=listing.published_at,
            payload_json={"source": "test"},
        )
    return FlatSaleDeterministicAnalysisProvider().analyze(
        listing=listing, snapshot=listing_snapshot, input_hash="hash"
    )


def test_flat_sale_provider_metadata():
    provider = FlatSaleDeterministicAnalysisProvider()

    assert provider.profile == "flat_sale"
    assert provider.analysis_version == "flat-sale-v0"
    assert provider.model_provider == "deterministic"
    assert provider.model_name == "flat-sale-rules-v0"


def test_flat_sale_calculates_price_per_m2():
    result = _flat_result(price=9_000_000, area_m2=45)

    assert result.facts_json["price_per_m2"] == 200_000


def test_flat_sale_detects_flat_type_variants():
    assert (
        _flat_result(title="Квартира-студия 28 м² 5/12 эт.").facts_json[
            "detected_flat_type"
        ]
        == "studio"
    )
    assert (
        _flat_result(title="1-комн. квартира 36 м² 5/12 эт.").facts_json[
            "detected_flat_type"
        ]
        == "one_room"
    )
    assert (
        _flat_result(title="2-к квартира 55 м² 5/12 эт.").facts_json[
            "detected_flat_type"
        ]
        == "two_room"
    )
    assert (
        _flat_result(title="3-комн квартира 75 м² 5/12 эт.").facts_json[
            "detected_flat_type"
        ]
        == "three_room"
    )
    assert (
        _flat_result(title="Квартира свободной планировки 40 м² 5/12 эт.").facts_json[
            "detected_flat_type"
        ]
        == "unknown"
    )


def test_flat_sale_detects_room_markers_without_using_floor_patterns():
    cases = [
        ("2-к. квартира, 60,8 м², 2/12 эт.", "two_room"),
        ("1-к. квартира, 32 м², 12/16 эт.", "one_room"),
        ("3-к. квартира, 78 м², 1/12 эт.", "three_room"),
        ("Квартира-студия, 24 м², 5/12 эт.", "studio"),
        ("Квартира, 60,8 м², 2/12 эт.", "unknown"),
    ]

    for title, flat_type in cases:
        assert _flat_result(title=title).facts_json["detected_flat_type"] == flat_type


def test_flat_sale_parses_floor_info():
    first = _flat_result(title="1-к квартира 36 м² 1/11 эт.").facts_json["floor_info"]
    last = _flat_result(title="2-к квартира 55 м² 6/6 эт.").facts_json["floor_info"]
    middle = _flat_result(title="3-к квартира 75 м² 8/15 эт.").facts_json["floor_info"]
    unknown = _flat_result(title="Квартира без этажа").facts_json["floor_info"]

    assert first == {"floor": 1, "total_floors": 11, "is_first_floor": True, "is_last_floor": False}
    assert last == {"floor": 6, "total_floors": 6, "is_first_floor": False, "is_last_floor": True}
    assert middle == {"floor": 8, "total_floors": 15, "is_first_floor": False, "is_last_floor": False}
    assert unknown == {"floor": None, "total_floors": None, "is_first_floor": None, "is_last_floor": None}


def test_flat_sale_freshness_statuses():
    now = datetime.now(UTC)

    assert _flat_result(published_at=now - timedelta(hours=1)).facts_json["freshness_status"] == "fresh"
    assert _flat_result(published_at=now - timedelta(hours=48)).facts_json["freshness_status"] == "recent"
    assert _flat_result(published_at=now - timedelta(hours=96)).facts_json["freshness_status"] == "stale"
    assert _flat_result(published_at=None).facts_json["freshness_status"] == "unknown"


def test_flat_sale_target_fit_logic():
    good = _flat_result(price=10_000_000, area_m2=45)
    poor = _flat_result(price=18_000_000, area_m2=100)
    unknown = _flat_result(price=None, area_m2=None, published_at=None)

    assert good.facts_json["target_fit"]["area_fit"] is True
    assert good.facts_json["target_fit"]["price_fit"] is True
    assert good.facts_json["target_fit"]["freshness_fit"] is True
    assert good.facts_json["target_fit"]["overall"] == "good"
    assert poor.facts_json["target_fit"]["area_fit"] is False
    assert poor.facts_json["target_fit"]["price_fit"] is False
    assert poor.facts_json["target_fit"]["overall"] == "partial"
    assert unknown.facts_json["target_fit"]["overall"] == "unknown"


def test_flat_sale_first_and_last_floor_risk_flags():
    first = _flat_result(title="1-к квартира 36 м² 1/11 эт.")
    last = _flat_result(title="2-к квартира 55 м² 6/6 эт.")

    assert "first_floor" in first.risks_json["flags"]
    assert "last_floor" in last.risks_json["flags"]
    assert any("Для первого этажа" in item for item in first.questions_json["items"])
    assert any("Для последнего этажа" in item for item in last.questions_json["items"])


def test_flat_sale_over_budget_and_area_risks():
    small = _flat_result(price=18_000_000, area_m2=20)
    large = _flat_result(area_m2=120)

    assert "over_budget" in small.risks_json["flags"]
    assert "area_too_small" in small.risks_json["flags"]
    assert "area_too_large" in large.risks_json["flags"]


def test_flat_sale_price_per_m2_risk_flags():
    low = _flat_result(price=2_000_000, area_m2=40)
    expensive = _flat_result(price=20_000_000, area_m2=40)

    assert "suspicious_low_price" in low.risks_json["flags"]
    assert "expensive_price_per_m2" in expensive.risks_json["flags"]


def test_flat_sale_score_is_clamped_to_0_and_100():
    low = _flat_result(
        title="Квартира без данных",
        price=50_000_000,
        area_m2=500,
        address="",
        published_at=datetime.now(UTC) - timedelta(days=30),
        snapshot=False,
    )

    assert low.score == 0
    assert 0 <= _flat_result().score <= 100


def test_flat_sale_verdict_thresholds():
    assert _flat_verdict(score=75) == "strong"
    assert _flat_verdict(score=55) == "medium"
    assert _flat_verdict(score=35) == "weak"
    assert _flat_verdict(score=34) == "review"


def test_flat_sale_score_75_is_strong_with_missing_published_at_risk():
    result = _flat_result(published_at=None)

    assert result.score == 75
    assert "missing_published_at" in result.risks_json["flags"]
    assert result.verdict == "strong"
    assert "review, score 75/100" not in result.report_md
    assert "strong, score 75/100" in result.report_md


def test_flat_sale_report_contains_russian_sections():
    report = _flat_result().report_md

    assert "## Вердикт" in report
    assert "## Факты" in report
    assert "## Риски" in report
    assert "## Что уточнить перед звонком" in report


def test_get_analysis_provider_supports_flat_sale():
    provider = get_analysis_provider("flat_sale")

    assert isinstance(provider, FlatSaleDeterministicAnalysisProvider)
    assert provider.profile == "flat_sale"


def _search(db_session, name="search-1", filters_json=None):
    search = SearchRepository(db_session).create(
        name=name,
        source_url=f"https://www.avito.ru/{name}",
        filters_json=filters_json or {},
    )
    db_session.flush()
    return search


def test_listing_search_match_upsert_creates_row(db_session):
    search = _search(db_session)
    seen_at = datetime(2026, 6, 1, 12, 0, 0)

    match = ListingSearchMatchRepository(db_session).upsert_match(
        search_job_id=search.id,
        listing_external_id="match-1",
        snapshot_id=10,
        seen_at=seen_at,
    )

    assert match.id is not None
    assert match.search_job_id == search.id
    assert match.listing_external_id == "match-1"
    assert match.first_seen_at == seen_at
    assert match.last_seen_at == seen_at
    assert match.last_snapshot_id == 10


def test_listing_search_match_repeated_upsert_updates_without_duplicates(db_session):
    search = _search(db_session)
    repo = ListingSearchMatchRepository(db_session)
    first_seen = datetime(2026, 6, 1, 12, 0, 0)
    second_seen = datetime(2026, 6, 1, 13, 0, 0)

    first = repo.upsert_match(search.id, "match-1", snapshot_id=10, seen_at=first_seen)
    second = repo.upsert_match(search.id, "match-1", snapshot_id=11, seen_at=second_seen)

    assert second.id == first.id
    assert second.first_seen_at == first_seen
    assert second.last_seen_at == second_seen
    assert second.last_snapshot_id == 11
    rows = db_session.scalars(select(ListingSearchMatch)).all()
    assert len(rows) == 1


def test_search_job_analysis_profile_resolution_uses_filters_json():
    search = type("Search", (), {"filters_json": {"analysis_profile": "commercial_rent"}})()

    assert resolve_search_analysis_profile(search) == "commercial_rent"


def test_search_job_analysis_profile_resolution_falls_back_to_default():
    search = type("Search", (), {"filters_json": {}})()

    assert resolve_search_analysis_profile(search) == "default"


def test_analyze_search_matches_analyzes_only_requested_search_id(db_session):
    commercial_search = _search(
        db_session,
        name="commercial",
        filters_json={"analysis_profile": "commercial_rent"},
    )
    apartment_search = _search(db_session, name="apartments")
    _listing(
        db_session,
        external_id="office-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
    )
    _listing(
        db_session,
        external_id="apt-1",
        title="Квартира 40 м²",
        price=8_000_000,
        area_m2=40,
    )
    match_repo = ListingSearchMatchRepository(db_session)
    match_repo.upsert_match(commercial_search.id, "office-1")
    match_repo.upsert_match(apartment_search.id, "apt-1")

    service = ListingAnalysisService(
        db_session,
        provider=get_analysis_provider(resolve_search_analysis_profile(commercial_search)),
    )
    analyses = service.analyze_search_matches(commercial_search.id, limit=20)

    assert len(analyses) == 1
    assert analyses[0].listing_external_id == "office-1"
    assert analyses[0].profile == "commercial_rent"
    assert analyses[0].context_key == f"search:{commercial_search.id}"
    assert analyses[0].search_job_id == commercial_search.id
    assert db_session.scalar(
        select(ListingAnalysis).where(ListingAnalysis.listing_external_id == "apt-1")
    ) is None


def test_analyze_search_matches_uses_flat_sale_profile(db_session):
    search = _search(
        db_session,
        name="flat-sale",
        filters_json={"analysis_profile": "flat_sale"},
    )
    _listing(
        db_session,
        external_id="flat-sale-1",
        title="1-к квартира 42 м² 8/15 эт.",
        price=10_500_000,
        area_m2=42,
    )
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "flat-sale-1")

    service = ListingAnalysisService(
        db_session,
        provider=get_analysis_provider(resolve_search_analysis_profile(search)),
    )
    analyses = service.analyze_search_matches(search.id, limit=20)

    assert len(analyses) == 1
    assert analyses[0].profile == "flat_sale"
    assert analyses[0].analysis_version == "flat-sale-v0"
    assert analyses[0].facts_json["detected_flat_type"] == "one_room"


def test_cli_analyze_search_matches_uses_search_profile(db_session, monkeypatch, capsys):
    search = _search(
        db_session,
        name="comm-cli-search",
        filters_json={"analysis_profile": "commercial_rent"},
    )
    _listing(
        db_session,
        external_id="comm-search-cli-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
    )
    ListingSearchMatchRepository(db_session).upsert_match(
        search.id, "comm-search-cli-1"
    )
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_analyze_search_matches(Namespace(search_id=search.id, limit=20))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["search_id"] == search.id
    assert output["profile"] == "commercial_rent"
    assert output["count"] == 1
    assert output["analyses"][0]["context_key"] == f"search:{search.id}"


def test_cli_analyze_search_matches_uses_flat_sale_search_profile(
    db_session, monkeypatch, capsys
):
    search = _search(
        db_session,
        name="flat-cli-search",
        filters_json={"analysis_profile": "flat_sale"},
    )
    _listing(
        db_session,
        external_id="flat-search-cli-1",
        title="1-к квартира 42 м² 8/15 эт.",
        price=10_500_000,
        area_m2=42,
    )
    ListingSearchMatchRepository(db_session).upsert_match(
        search.id, "flat-search-cli-1"
    )
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_analyze_search_matches(Namespace(search_id=search.id, limit=20))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["profile"] == "flat_sale"
    assert output["count"] == 1
    assert output["analyses"][0]["profile"] == "flat_sale"


def test_global_and_search_context_idempotency_are_separate(db_session):
    search = _search(db_session)
    _listing(db_session, external_id="ctx-1")
    _alert(db_session, external_id="ctx-1")
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "ctx-1")

    service = ListingAnalysisService(db_session)
    global_analysis = service.analyze_alerted_listings(limit=20)[0]
    search_analysis = service.analyze_search_matches(search.id, limit=20)[0]
    rerun = service.analyze_search_matches(search.id, limit=20)

    assert global_analysis.context_key == "global"
    assert search_analysis.context_key == f"search:{search.id}"
    assert global_analysis.id != search_analysis.id
    assert rerun == []
    rows = db_session.scalars(select(ListingAnalysis)).all()
    assert len(rows) == 2


def test_listing_search_match_upsert_duplicate_race_does_not_raise(db_session, monkeypatch):
    search = _search(db_session)
    repo = ListingSearchMatchRepository(db_session)
    first_seen = datetime(2026, 6, 1, 12, 0, 0)
    second_seen = datetime(2026, 6, 1, 13, 0, 0)
    original_get_latest_match = repo.get_latest_match
    repo.upsert_match(search.id, "race-match", snapshot_id=10, seen_at=first_seen)
    calls = {"count": 0}

    def stale_read_once(search_job_id, listing_external_id):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_get_latest_match(search_job_id, listing_external_id)

    monkeypatch.setattr(repo, "get_latest_match", stale_read_once)

    match = repo.upsert_match(
        search.id, "race-match", snapshot_id=11, seen_at=second_seen
    )

    assert match.first_seen_at == first_seen
    assert match.last_seen_at == second_seen
    assert match.last_snapshot_id == 11
    assert len(db_session.scalars(select(ListingSearchMatch)).all()) == 1


class CommercialRentV1Provider(CustomSuccessProvider):
    profile = "commercial_rent"
    analysis_version = "commercial-rent-v1"
    model_provider = "deterministic"
    model_name = "commercial-rent-rules-v1"


class CommercialRentV0Provider(CustomSuccessProvider):
    profile = "commercial_rent"
    analysis_version = "commercial-rent-v0"
    model_provider = "deterministic"
    model_name = "commercial-rent-rules-v0"


def test_analyze_search_matches_only_skips_same_profile_version_context(db_session):
    search = _search(
        db_session,
        name="versioned-commercial",
        filters_json={"analysis_profile": "commercial_rent"},
    )
    _listing(
        db_session,
        external_id="versioned-office-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
    )
    ListingSearchMatchRepository(db_session).upsert_match(
        search.id, "versioned-office-1"
    )
    ListingAnalysisRepository(db_session).create_or_update_analysis(
        listing_external_id="versioned-office-1",
        snapshot_id=None,
        profile="commercial_rent",
        status="success",
        analysis_version="commercial-rent-v0",
        input_hash="old-hash",
        search_job_id=search.id,
        context_key=f"search:{search.id}",
    )

    analyses = ListingAnalysisService(
        db_session, provider=CommercialRentV1Provider()
    ).analyze_search_matches(search.id, limit=20)

    assert len(analyses) == 1
    assert analyses[0].listing_external_id == "versioned-office-1"
    assert analyses[0].analysis_version == "commercial-rent-v1"
    assert analyses[0].context_key == f"search:{search.id}"


def test_global_alerted_selection_is_analysis_version_aware(db_session):
    _listing(db_session, external_id="versioned-global-1")
    _alert(db_session, external_id="versioned-global-1")
    ListingAnalysisRepository(db_session).create_or_update_analysis(
        listing_external_id="versioned-global-1",
        snapshot_id=None,
        profile="commercial_rent",
        status="success",
        analysis_version="commercial-rent-v0",
        input_hash="old-global-hash",
        context_key="global",
    )

    analyses = ListingAnalysisService(
        db_session, provider=CommercialRentV1Provider()
    ).analyze_alerted_listings(limit=20)

    assert len(analyses) == 1
    assert analyses[0].listing_external_id == "versioned-global-1"
    assert analyses[0].analysis_version == "commercial-rent-v1"
    assert analyses[0].context_key == "global"


def _flat_rent_result(**kwargs):
    now = datetime.now(UTC)
    description = kwargs.pop("description", "")
    snapshot_enabled = kwargs.pop("snapshot", True)
    listing = Listing(
        external_id=kwargs.pop("external_id", "rent-1"),
        url=kwargs.pop("url", "https://www.avito.ru/item/rent-1"),
        title=kwargs.pop(
            "title",
            "1-к квартира 40 м² 8/15 эт. залог без комиссии КУ мебель техника",
        ),
        price=kwargs.pop("price", 60_000),
        address=kwargs.pop("address", "Санкт-Петербург, Невский проспект"),
        area_m2=kwargs.pop("area_m2", 40.0),
        published_label=kwargs.pop("published_label", "сегодня"),
        published_at=kwargs.pop("published_at", now - timedelta(hours=2)),
        **kwargs,
    )
    snapshot = None
    if snapshot_enabled:
        snapshot = ListingSnapshot(
            id=1,
            external_id=listing.external_id,
            title=listing.title,
            price=listing.price,
            published_label=listing.published_label,
            published_at=listing.published_at,
            payload_json={"description": description},
        )
    return FlatRentDeterministicAnalysisProvider().analyze(
        listing=listing, snapshot=snapshot, input_hash="hash"
    )


def test_flat_rent_provider_metadata():
    result = _flat_rent_result()

    assert result.model_provider == "deterministic"
    assert result.model_name == "flat-rent-rules-v0"
    provider = FlatRentDeterministicAnalysisProvider()
    assert provider.profile == "flat_rent"
    assert provider.analysis_version == "flat-rent-v0"


def test_flat_rent_rent_per_m2_calculation():
    result = _flat_rent_result(price=75_000, area_m2=30)

    assert result.facts_json["rent_per_m2"] == 2500


def test_flat_rent_detects_flat_type_markers():
    cases = [
        ("Квартира-студия 25 м² 3/12 эт.", "studio"),
        ("1 к квартира 35 м² 4/9 эт.", "one_room"),
        ("2-комн. квартира 55 м² 5/10 эт.", "two_room"),
        ("трёхкомнатная квартира 70 м² 6/12 эт.", "three_room"),
        ("Квартира 2/12 эт. без комнатного маркера", "unknown"),
    ]

    for title, expected in cases:
        assert _flat_rent_result(title=title).facts_json["detected_flat_type"] == expected


def test_flat_rent_floor_parsing_variants():
    first = _flat_rent_result(title="1-к квартира 30 м² 1/11 эт.").facts_json[
        "floor_info"
    ]
    last = _flat_rent_result(title="1-к квартира 30 м² 6/6 эт.").facts_json[
        "floor_info"
    ]
    middle = _flat_rent_result(title="1-к квартира 30 м² 8/15 эт.").facts_json[
        "floor_info"
    ]
    unknown = _flat_rent_result(title="1-к квартира 30 м² этаж не указан").facts_json[
        "floor_info"
    ]

    assert first == {
        "floor": 1,
        "total_floors": 11,
        "is_first_floor": True,
        "is_last_floor": False,
    }
    assert last["is_last_floor"] is True
    assert middle["floor"] == 8
    assert middle["is_first_floor"] is False
    assert middle["is_last_floor"] is False
    assert unknown["floor"] is None


def test_flat_rent_freshness_statuses():
    now = datetime.now(UTC)

    assert _flat_rent_result(published_at=now - timedelta(hours=23)).facts_json[
        "freshness_status"
    ] == "fresh"
    assert _flat_rent_result(published_at=now - timedelta(hours=48)).facts_json[
        "freshness_status"
    ] == "recent"
    assert _flat_rent_result(published_at=now - timedelta(hours=73)).facts_json[
        "freshness_status"
    ] == "stale"
    assert _flat_rent_result(published_at=None).facts_json["freshness_status"] == "unknown"


def test_flat_rent_target_fit():
    good = _flat_rent_result(price=80_000, area_m2=45).facts_json["target_fit"]
    partial = _flat_rent_result(price=120_000, area_m2=45).facts_json["target_fit"]
    unknown = _flat_rent_result(price=None, area_m2=None, published_at=None).facts_json[
        "target_fit"
    ]

    assert good["area_fit"] is True
    assert good["price_fit"] is True
    assert good["freshness_fit"] is True
    assert good["overall"] == "good"
    assert partial["price_fit"] is False
    assert partial["overall"] == "partial"
    assert unknown["overall"] == "unknown"


def test_flat_rent_rental_term_hints():
    result = _flat_rent_result(
        description=(
            "залог комиссия коммунальные счетчики мебель диван техника холодильник "
            "стиральная можно с кошкой с детьми длительный срок"
        ),
    )
    hints = result.facts_json["rental_terms_hints"]
    no_commission = _flat_rent_result(description="без комиссии").facts_json[
        "rental_terms_hints"
    ]
    short_term = _flat_rent_result(description="посуточно на сутки").facts_json[
        "rental_terms_hints"
    ]

    assert hints["has_deposit_hint"] is True
    assert hints["has_commission_hint"] is True
    assert no_commission["has_no_commission_hint"] is True
    assert hints["has_utilities_hint"] is True
    assert hints["has_furniture_hint"] is True
    assert hints["has_appliances_hint"] is True
    assert hints["has_pets_hint"] is True
    assert hints["has_children_hint"] is True
    assert hints["has_long_term_hint"] is True
    assert short_term["has_short_term_hint"] is True


def test_flat_rent_utilities_hint_uses_specific_keywords():
    utilities = _flat_rent_result(
        description="Коммунальные платежи и счётчики оплачиваются отдельно"
    )
    false_positive = _flat_rent_result(
        description="Светлая квартира, кухня, парковку обсудим"
    )

    assert utilities.facts_json["rental_terms_hints"]["has_utilities_hint"] is True
    assert "utilities_unknown" not in utilities.risks_json["flags"]
    assert false_positive.facts_json["rental_terms_hints"]["has_utilities_hint"] is False
    assert "utilities_unknown" in false_positive.risks_json["flags"]


def test_flat_rent_risk_flags():
    flags = _flat_rent_result(
        title="1-к квартира 20 м² 1/10 эт. посуточно",
        price=120_000,
        area_m2=20,
        published_at=None,
        description="посуточно",
    ).risks_json["flags"]
    expensive_flags = _flat_rent_result(price=120_000, area_m2=20).risks_json["flags"]
    last_flags = _flat_rent_result(title="1-к квартира 40 м² 6/6 эт.").risks_json[
        "flags"
    ]

    assert "over_budget" in flags
    assert "first_floor" in flags
    assert "short_term_rent" in flags
    assert "missing_published_at" in flags
    assert "expensive_rent_per_m2" in expensive_flags
    assert "last_floor" in last_flags


def test_flat_rent_score_is_clamped_to_0_and_100():
    low = _flat_rent_result(
        title="Квартира без данных посуточно",
        price=500_000,
        area_m2=5,
        address="",
        published_at=datetime.now(UTC) - timedelta(days=30),
        snapshot=False,
    )

    assert 0 <= low.score <= 100
    assert 0 <= _flat_rent_result().score <= 100


def test_flat_rent_verdict_thresholds():
    assert _flat_verdict(score=75) == "strong"
    assert _flat_verdict(score=55) == "medium"
    assert _flat_verdict(score=35) == "weak"
    assert _flat_verdict(score=34) == "review"


def test_flat_rent_report_contains_russian_sections():
    report = _flat_rent_result().report_md

    assert "## Вердикт" in report
    assert "## Факты" in report
    assert "## Риски" in report
    assert "## Что уточнить перед звонком" in report


def test_flat_rent_report_renders_missing_deposit_hint_as_unknown():
    report = _flat_rent_result(title="1-к квартира 40 м² 8/15 эт.").report_md

    assert "залог=нет" not in report
    assert "залог=не найдено" in report


def test_flat_rent_report_renders_no_commission_and_utilities_hints():
    report = _flat_rent_result(
        title="1-к квартира 40 м² 8/15 эт. без комиссии",
        description="Коммунальные платежи и счётчики оплачиваются отдельно",
    ).report_md

    assert "комиссия=без комиссии указано" in report
    assert "КУ=найдено" in report


def test_flat_rent_deposit_question_is_not_duplicated():
    result = _flat_rent_result(title="1-к квартира 40 м² 8/15 эт.")
    deposit_question = "Уточнить размер залога, условия удержания и возврата."

    assert result.questions_json["items"].count(deposit_question) == 1
    assert result.report_md.count(deposit_question) == 1


def test_get_analysis_provider_supports_flat_rent():
    provider = get_analysis_provider("flat_rent")

    assert isinstance(provider, FlatRentDeterministicAnalysisProvider)
    assert provider.profile == "flat_rent"


def test_analyze_search_matches_uses_flat_rent_profile(db_session):
    search = _search(
        db_session,
        name="flat-rent",
        filters_json={"analysis_profile": "flat_rent"},
    )
    _listing(
        db_session,
        external_id="flat-rent-1",
        title="1-к квартира 42 м² 8/15 эт.",
        price=65_000,
        area_m2=42,
    )
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "flat-rent-1")

    service = ListingAnalysisService(
        db_session,
        provider=get_analysis_provider(resolve_search_analysis_profile(search)),
    )
    analyses = service.analyze_search_matches(search.id, limit=20)

    assert len(analyses) == 1
    assert analyses[0].profile == "flat_rent"
    assert analyses[0].analysis_version == "flat-rent-v0"
    assert analyses[0].facts_json["detected_flat_type"] == "one_room"


def test_cli_analyze_all_active_searches_uses_filters_json_config(
    db_session, monkeypatch, capsys
):
    search = _search(
        db_session,
        name="all-active-config",
        filters_json={"analysis_profile": "commercial_rent", "max_age_hours": 24},
    )
    _listing(
        db_session,
        external_id="all-active-config-1",
        title="Офис 60 м²",
        price=120_000,
        area_m2=60,
        published_at=datetime.now(UTC) - timedelta(hours=30),
    )
    ListingSearchMatchRepository(db_session).upsert_match(
        search.id, "all-active-config-1"
    )
    _prepare_cli_db(monkeypatch, db_session)

    cli.cmd_analyze_all_active_searches(
        Namespace(
            limit_per_search=20, include_inactive=False, profile="", dry_run=False
        )
    )

    output = json.loads(capsys.readouterr().out)
    analysis = db_session.scalar(select(ListingAnalysis))
    assert output["ok"] is True
    assert output["analyses_created_total"] == 1
    assert analysis.facts_json["analysis_config"]["max_age_hours"] == 24
    assert analysis.facts_json["freshness_status"] == "stale"


def _prepare_cli_db(monkeypatch, db_session):
    db_session.commit()
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)


def _run_analyze_all(capsys, **kwargs):
    args = Namespace(
        limit_per_search=kwargs.get("limit_per_search", 5),
        include_inactive=kwargs.get("include_inactive", False),
        profile=kwargs.get("profile", ""),
        dry_run=kwargs.get("dry_run", False),
    )
    cli.cmd_analyze_all_active_searches(args)
    return json.loads(capsys.readouterr().out)


def _search_result(output, search_id):
    return next(item for item in output["results"] if item["search_id"] == search_id)


def test_cli_analyze_all_active_searches_processes_only_active_by_default(
    db_session, monkeypatch, capsys
):
    active = _search(db_session, name="active-all", filters_json={"analysis_profile": "flat_sale"})
    inactive = _search(
        db_session, name="inactive-all", filters_json={"analysis_profile": "flat_sale"}
    )
    inactive.is_active = False
    _listing(db_session, external_id="active-all-1", title="1-к квартира 42 м²")
    _listing(db_session, external_id="inactive-all-1", title="1-к квартира 39 м²")
    match_repo = ListingSearchMatchRepository(db_session)
    match_repo.upsert_match(active.id, "active-all-1")
    match_repo.upsert_match(inactive.id, "inactive-all-1")
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys)

    assert output["ok"] is True
    assert output["searches_total"] == 2
    assert output["searches_considered"] == 1
    assert output["searches_processed"] == 1
    assert output["analyses_created_total"] == 1
    assert _search_result(output, active.id)["status"] == "processed"
    inactive_result = _search_result(output, inactive.id)
    assert inactive_result["status"] == "skipped"
    assert inactive_result["skip_reason"] == "inactive"


def test_cli_analyze_all_active_searches_include_inactive_processes_inactive(
    db_session, monkeypatch, capsys
):
    inactive = _search(
        db_session,
        name="inactive-included",
        filters_json={"analysis_profile": "commercial_rent"},
    )
    inactive.is_active = False
    _listing(db_session, external_id="inactive-included-1", title="Офис 60 м²")
    ListingSearchMatchRepository(db_session).upsert_match(inactive.id, "inactive-included-1")
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys, include_inactive=True)

    result = _search_result(output, inactive.id)
    assert result["status"] == "processed"
    assert result["count"] == 1
    assert output["include_inactive"] is True


def test_cli_analyze_all_active_searches_skips_missing_unknown_and_empty_pending(
    db_session, monkeypatch, capsys
):
    missing = _search(db_session, name="missing-all", filters_json={})
    unknown = _search(db_session, name="unknown-all", filters_json={"analysis_profile": "villa_sale"})
    empty = _search(db_session, name="empty-all", filters_json={"analysis_profile": "flat_sale"})
    _listing(db_session, external_id="unknown-all-1")
    ListingSearchMatchRepository(db_session).upsert_match(unknown.id, "unknown-all-1")
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys)

    assert output["ok"] is True
    assert output["searches_total"] == 3
    assert output["searches_considered"] == 3
    assert _search_result(output, missing.id)["skip_reason"] == "missing_analysis_profile"
    assert _search_result(output, unknown.id)["skip_reason"] == "unknown_analysis_profile"
    assert _search_result(output, empty.id)["skip_reason"] == "no_pending_matches"


def test_cli_analyze_all_active_searches_profile_filter(db_session, monkeypatch, capsys):
    flat = _search(db_session, name="profile-flat", filters_json={"analysis_profile": "flat_sale"})
    rent = _search(db_session, name="profile-rent", filters_json={"analysis_profile": "flat_rent"})
    _listing(db_session, external_id="profile-flat-1", title="1-к квартира 42 м²")
    _listing(db_session, external_id="profile-rent-1", title="1-к квартира 42 м²")
    match_repo = ListingSearchMatchRepository(db_session)
    match_repo.upsert_match(flat.id, "profile-flat-1")
    match_repo.upsert_match(rent.id, "profile-rent-1")
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys, profile="flat_rent")

    assert output["profile_filter"] == "flat_rent"
    assert output["searches_total"] == 2
    assert output["searches_considered"] == 1
    assert _search_result(output, flat.id)["skip_reason"] == "profile_filter_mismatch"
    assert _search_result(output, rent.id)["status"] == "processed"


def test_cli_analyze_all_active_searches_dry_run_does_not_create_analyses(
    db_session, monkeypatch, capsys
):
    search = _search(db_session, name="dry-all", filters_json={"analysis_profile": "flat_sale"})
    _listing(db_session, external_id="dry-all-1", title="1-к квартира 42 м²")
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "dry-all-1")
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys, dry_run=True)

    result = _search_result(output, search.id)
    assert output["dry_run"] is True
    assert output["ok"] is True
    assert result["status"] == "dry_run"
    assert result["count"] == 1
    assert output["analyses_created_total"] == 0
    assert db_session.scalar(select(ListingAnalysis)) is None


def test_cli_analyze_all_active_searches_zero_limit_validation(
    db_session, monkeypatch, capsys
):
    search = _search(
        db_session, name="zero-limit", filters_json={"analysis_profile": "flat_sale"}
    )
    _listing(db_session, external_id="zero-limit-1", title="1-к квартира 42 м²")
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "zero-limit-1")
    _prepare_cli_db(monkeypatch, db_session)
    monkeypatch.setattr(
        cli,
        "init_db",
        lambda: (_ for _ in ()).throw(AssertionError("init_db used")),
    )

    output = _run_analyze_all(capsys, limit_per_search=0)

    assert output == {
        "ok": False,
        "error_type": "ValidationError",
        "error": "limit_per_search must be a positive integer",
        "limit_per_search": 0,
    }
    assert db_session.scalar(select(ListingAnalysis)) is None


def test_cli_analyze_all_active_searches_negative_limit_validation(
    db_session, monkeypatch, capsys
):
    search = _search(
        db_session, name="negative-limit", filters_json={"analysis_profile": "flat_sale"}
    )
    _listing(db_session, external_id="negative-limit-1", title="1-к квартира 42 м²")
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "negative-limit-1")
    _prepare_cli_db(monkeypatch, db_session)
    monkeypatch.setattr(
        cli,
        "init_db",
        lambda: (_ for _ in ()).throw(AssertionError("init_db used")),
    )

    output = _run_analyze_all(capsys, limit_per_search=-1)

    assert output == {
        "ok": False,
        "error_type": "ValidationError",
        "error": "limit_per_search must be a positive integer",
        "limit_per_search": -1,
    }
    assert db_session.scalar(select(ListingAnalysis)) is None


def test_cli_analyze_all_active_searches_limit_per_search(db_session, monkeypatch, capsys):
    search_one = _search(db_session, name="limit-one", filters_json={"analysis_profile": "flat_sale"})
    search_two = _search(db_session, name="limit-two", filters_json={"analysis_profile": "flat_sale"})
    match_repo = ListingSearchMatchRepository(db_session)
    for search in (search_one, search_two):
        for idx in range(3):
            external_id = f"{search.name}-{idx}"
            _listing(db_session, external_id=external_id, title="1-к квартира 42 м²")
            match_repo.upsert_match(search.id, external_id)
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys, limit_per_search=2)

    assert output["limit_per_search"] == 2
    assert _search_result(output, search_one.id)["count"] == 2
    assert _search_result(output, search_two.id)["count"] == 2
    assert output["analyses_created_total"] == 4


def test_cli_analyze_all_active_searches_summary_shape(db_session, monkeypatch, capsys):
    search = _search(db_session, name="shape-all", filters_json={"analysis_profile": "flat_sale"})
    _listing(db_session, external_id="shape-all-1", title="1-к квартира 42 м²")
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "shape-all-1")
    _prepare_cli_db(monkeypatch, db_session)

    output = _run_analyze_all(capsys)

    assert set(output) == {
        "ok",
        "limit_per_search",
        "dry_run",
        "include_inactive",
        "profile_filter",
        "searches_total",
        "searches_considered",
        "searches_processed",
        "searches_skipped",
        "searches_failed",
        "analyses_created_total",
        "results",
    }
    result = _search_result(output, search.id)
    assert result["status"] == "processed"
    assert result["analyses"][0]["context_key"] == f"search:{search.id}"


def test_cli_analyze_all_active_searches_does_not_use_monitor_parser_or_notifiers(
    db_session, monkeypatch, capsys
):
    search = _search(db_session, name="safe-all", filters_json={"analysis_profile": "flat_sale"})
    _listing(db_session, external_id="safe-all-1", title="1-к квартира 42 м²")
    ListingSearchMatchRepository(db_session).upsert_match(search.id, "safe-all-1")
    _prepare_cli_db(monkeypatch, db_session)
    monkeypatch.setattr(cli, "MonitorService", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("monitor used")))
    monkeypatch.setattr(cli, "_build_parser", lambda: (_ for _ in ()).throw(AssertionError("parser used")))

    output = _run_analyze_all(capsys)

    assert output["ok"] is True
    assert _search_result(output, search.id)["status"] == "processed"
