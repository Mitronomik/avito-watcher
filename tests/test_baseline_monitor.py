import asyncio

from sqlalchemy import func, select

from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot
from app.parsers.schemas import ListingCard
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService


class FakeParser:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    async def fetch_search_cards(self, search_url: str):
        self.calls += 1
        assert search_url
        return self.batches.pop(0)


class FakeScorer:
    def __init__(self):
        self.cards = []

    async def score(self, card: ListingCard):
        self.cards.append(card.external_id)
        return {"score": 100, "summary": f"score for {card.external_id}", "tags": []}


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send_listing_alert(self, text: str) -> None:
        self.messages.append(text)


def card(external_id: str, price: float = 100.0) -> ListingCard:
    return ListingCard(
        external_id=external_id,
        url=f"https://www.avito.ru/item_{external_id}",
        title=f"Listing {external_id}",
        price=price,
        raw={"external_id": external_id, "price": price},
    )


def make_search(db_session):
    repo = SearchRepository(db_session)
    search = repo.create(name="test", source_url="https://www.avito.ru/test")
    db_session.commit()
    return search


def scalar_count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


def run(service: MonitorService, db_session, search):
    return asyncio.run(service.process_search(db_session, search))


def test_first_baseline_run_saves_listings_but_sends_zero_alerts(db_session):
    search = make_search(db_session)
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser([[card("1"), card("2")]]),
        scorer=scorer,
        notifier=notifier,
    )

    result = run(service, db_session, search)

    assert result["baseline_run"] is True
    assert result["created"] == 2
    assert result["alerted"] == 0
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scalar_count(db_session, AlertSent) == 0
    assert notifier.messages == []
    assert scorer.cards == []
    db_session.refresh(search)
    assert search.baseline_initialized is True
    assert search.baseline_initialized_at is not None
    assert search.last_checked_at is not None
    assert search.last_success_at is not None
    assert search.last_error == ""
    assert search.fail_count == 0


def test_second_run_with_same_listings_sends_zero_alerts(db_session):
    search = make_search(db_session)
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser([[card("1")], [card("1")]]),
        scorer=scorer,
        notifier=notifier,
    )

    first = run(service, db_session, search)
    second = run(service, db_session, search)

    assert first["alerted"] == 0
    assert second["baseline_run"] is False
    assert second["created"] == 0
    assert second["alerted"] == 0
    assert scalar_count(db_session, Listing) == 1
    assert scalar_count(db_session, AlertSent) == 0
    assert notifier.messages == []
    assert scorer.cards == []


def test_second_run_with_one_new_listing_sends_one_alert(db_session):
    search = make_search(db_session)
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser([[card("1")], [card("1"), card("2")]]),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["baseline_run"] is False
    assert result["created"] == 1
    assert result["alerted"] == 1
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scalar_count(db_session, AlertSent) == 1
    assert scorer.cards == ["2"]
    assert len(notifier.messages) == 1


def test_existing_listing_price_change_creates_new_snapshot(db_session):
    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser([[card("1", price=100.0)], [card("1", price=150.0)]]),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    listing = db_session.scalar(select(Listing).where(Listing.external_id == "1"))
    assert result["price_changed"] == 1
    assert listing.price == 150.0
    assert scalar_count(db_session, Listing) == 1
    assert scalar_count(db_session, ListingSnapshot) == 2


def test_run_all_searches_does_not_create_default_search(monkeypatch, db_session):
    import app.services.monitor_service as monitor_module

    class FakeSessionLocal:
        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(monitor_module, "SessionLocal", lambda: FakeSessionLocal())

    result = MonitorService(parser=FakeParser([]), scorer=FakeScorer(), notifier=FakeNotifier()).run_all_searches()

    assert result == []
    assert scalar_count(db_session, Listing) == 0

class FailingScorer:
    async def score(self, card: ListingCard):
        raise RuntimeError("scoring failed")


def test_scoring_failure_records_failed_check_without_partial_commit(db_session):
    search = make_search(db_session)
    baseline_service = MonitorService(
        parser=FakeParser([[card("1")]]),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )
    run(baseline_service, db_session, search)

    failing_service = MonitorService(
        parser=FakeParser([[card("1"), card("2")]]),
        scorer=FailingScorer(),
        notifier=FakeNotifier(),
    )

    try:
        run(failing_service, db_session, search)
    except RuntimeError as exc:
        assert str(exc) == "scoring failed"
    else:
        raise AssertionError("expected scoring failure")

    db_session.refresh(search)
    assert search.fail_count == 1
    assert search.last_error == "scoring failed"
    assert search.last_checked_at is not None
    assert scalar_count(db_session, Listing) == 1
    assert scalar_count(db_session, ListingSnapshot) == 1
    assert scalar_count(db_session, AlertSent) == 0
