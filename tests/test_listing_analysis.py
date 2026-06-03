import json
from argparse import Namespace

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.analysis.provider import ListingAnalysisResult
from app.analysis.service import ListingAnalysisService
from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_snapshot import ListingSnapshot
from app.repositories.alert_repository import AlertRepository
from app.repositories.listing_analysis_repository import ListingAnalysisRepository


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

    analysis = ListingAnalysisService(db_session, provider=FailingProvider()).analyze_listing("ext-1")

    assert analysis.status == "failed"
    assert analysis.error_type == "RuntimeError"
    assert analysis.error_message == "provider exploded"


def test_cli_analyze_listing_works(db_session, monkeypatch, capsys):
    _listing(db_session)
    _snapshot(db_session)
    db_session.commit()
    SessionLocal = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
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
    SessionLocal = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
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

    listings = ListingAnalysisRepository(db_session).list_alerted_listings_without_analysis(limit=20)

    assert [listing.external_id for listing in listings] == ["ext-1"]
