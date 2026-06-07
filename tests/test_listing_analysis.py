import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.analysis.provider import (
    CommercialRentDeterministicAnalysisProvider,
    ListingAnalysisResult,
    _verdict,
    get_analysis_provider,
)
from app.analysis.service import ListingAnalysisService, resolve_search_analysis_profile
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


def test_get_analysis_provider_supports_default_and_commercial_rent():
    default_provider = get_analysis_provider("default")
    commercial_provider = get_analysis_provider("commercial_rent")

    assert default_provider.profile == "default"
    assert commercial_provider.profile == "commercial_rent"
    assert commercial_provider.analysis_version == "commercial-rent-v0"
    assert commercial_provider.model_provider == "deterministic"
    assert commercial_provider.model_name == "commercial-rent-rules-v0"


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
