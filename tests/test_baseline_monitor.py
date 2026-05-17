import asyncio
from datetime import UTC, datetime, timedelta

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
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


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


def card(
    external_id: str,
    price: float = 100.0,
    area_m2: float | None = None,
    title: str | None = None,
    address: str = "",
    raw: dict | None = None,
) -> ListingCard:
    payload = {"external_id": external_id, "price": price}
    if raw:
        payload.update(raw)
    return ListingCard(
        external_id=external_id,
        url=f"https://www.avito.ru/item_{external_id}",
        title=title or f"Listing {external_id}",
        price=price,
        address=address,
        area_m2=area_m2,
        raw=payload,
    )


def make_search(
    db_session,
    name: str = "test",
    source_url: str = "https://www.avito.ru/test",
    poll_interval_sec: int = 180,
    filters_json: dict | None = None,
):
    repo = SearchRepository(db_session)
    search = repo.create(
        name=name,
        source_url=source_url,
        filters_json=filters_json,
        poll_interval_sec=poll_interval_sec,
    )
    db_session.commit()
    return search


def patch_session_local(monkeypatch, db_session):
    import app.services.monitor_service as monitor_module

    class FakeSessionLocal:
        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(monitor_module, "SessionLocal", lambda: FakeSessionLocal())


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
    assert result["filtered"] == 0
    assert result["scored"] == 0
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


def test_max_price_filters_out_expensive_new_listing(db_session):
    search = make_search(db_session, filters_json={"max_price": 100.0})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [[card("1", price=50.0)], [card("1", price=50.0), card("2", price=150.0)]]
        ),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["created"] == 1
    assert result["filtered"] == 1
    assert result["scored"] == 0
    assert result["alerted"] == 0
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scorer.cards == []
    assert notifier.messages == []


def test_min_area_filters_out_too_small_new_listing(db_session):
    search = make_search(db_session, filters_json={"min_area": 40.0})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1", area_m2=45.0)],
                [card("1", area_m2=45.0), card("2", area_m2=30.0)],
            ]
        ),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["created"] == 1
    assert result["filtered"] == 1
    assert result["scored"] == 0
    assert result["alerted"] == 0
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scorer.cards == []
    assert notifier.messages == []


def test_exclude_keywords_filters_by_title_or_text(db_session):
    search = make_search(db_session, filters_json={"exclude_keywords": ["auction"]})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1")],
                [
                    card("1"),
                    card(
                        "2", title="Commercial lot", raw={"description": "auction sale"}
                    ),
                ],
            ]
        ),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["created"] == 1
    assert result["filtered"] == 1
    assert result["scored"] == 0
    assert result["alerted"] == 0
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scorer.cards == []
    assert notifier.messages == []


def test_include_keywords_allows_matching_new_listing(db_session):
    search = make_search(db_session, filters_json={"include_keywords": ["warehouse"]})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1")],
                [card("1"), card("2", title="Warm warehouse near metro")],
            ]
        ),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["created"] == 1
    assert result["filtered"] == 0
    assert result["scored"] == 1
    assert result["alerted"] == 1
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scorer.cards == ["2"]
    assert len(notifier.messages) == 1


def test_baseline_ignores_filters_alerting_and_scoring_but_saves_listings(db_session):
    search = make_search(
        db_session, filters_json={"max_price": 100.0, "include_keywords": ["office"]}
    )
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser([[card("1", price=999.0, title="Expensive apartment")]]),
        scorer=scorer,
        notifier=notifier,
    )

    result = run(service, db_session, search)

    assert result["baseline_run"] is True
    assert result["created"] == 1
    assert result["filtered"] == 0
    assert result["scored"] == 0
    assert result["alerted"] == 0
    assert scalar_count(db_session, Listing) == 1
    assert scalar_count(db_session, ListingSnapshot) == 1
    assert scalar_count(db_session, AlertSent) == 0
    assert scorer.cards == []
    assert notifier.messages == []


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

    result = MonitorService(
        parser=FakeParser([]), scorer=FakeScorer(), notifier=FakeNotifier()
    ).run_all_searches()

    assert result == []
    assert scalar_count(db_session, Listing) == 0


class FailingScorer:
    async def score(self, card: ListingCard):
        raise RuntimeError("scoring failed")


def test_scoring_failure_still_sends_alert_with_fallback_summary(db_session):
    search = make_search(db_session)
    baseline_service = MonitorService(
        parser=FakeParser([[card("1")]]),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )
    run(baseline_service, db_session, search)

    notifier = FakeNotifier()
    failing_service = MonitorService(
        parser=FakeParser([[card("1"), card("2")]]),
        scorer=FailingScorer(),
        notifier=notifier,
    )

    result = run(failing_service, db_session, search)

    db_session.refresh(search)
    assert result["created"] == 1
    assert result["alerted"] == 1
    assert search.fail_count == 0
    assert search.last_error == ""
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 2
    assert scalar_count(db_session, AlertSent) == 1
    assert len(notifier.messages) == 1
    assert "LLM scoring unavailable: scoring failed" in notifier.messages[0]


def test_run_all_searches_records_one_failure_and_continues_next_search(
    monkeypatch, db_session
):
    import app.services.monitor_service as monitor_module

    first = make_search(
        db_session, name="first", source_url="https://www.avito.ru/first"
    )
    second = make_search(
        db_session, name="second", source_url="https://www.avito.ru/second"
    )
    parser = FakeParser([RuntimeError("parser failed"), [card("2")]])

    class FakeSessionLocal:
        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(monitor_module, "SessionLocal", lambda: FakeSessionLocal())

    result = MonitorService(
        parser=parser, scorer=FakeScorer(), notifier=FakeNotifier()
    ).run_all_searches()

    db_session.refresh(first)
    db_session.refresh(second)
    assert parser.calls == 2
    assert result[0] == {"search": "first", "error": "parser failed"}
    assert result[1]["search"] == "second"
    assert result[1]["baseline_run"] is True
    assert result[1]["created"] == 1
    assert first.fail_count == 1
    assert first.last_error == "parser failed"
    assert second.baseline_initialized is True
    assert second.fail_count == 0


def test_run_all_searches_skips_inactive_search(monkeypatch, db_session):
    search = make_search(db_session)
    SearchRepository(db_session).deactivate(search)
    db_session.commit()
    patch_session_local(monkeypatch, db_session)
    parser = FakeParser([[card("1")]])

    result = MonitorService(
        parser=parser, scorer=FakeScorer(), notifier=FakeNotifier()
    ).run_all_searches()

    assert result == []
    assert parser.calls == 0
    assert scalar_count(db_session, Listing) == 0


def test_run_all_searches_skips_active_search_scheduled_in_future(
    monkeypatch, db_session
):
    search = make_search(db_session)
    search.next_run_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
    db_session.commit()
    patch_session_local(monkeypatch, db_session)
    parser = FakeParser([[card("1")]])

    result = MonitorService(
        parser=parser, scorer=FakeScorer(), notifier=FakeNotifier()
    ).run_all_searches()

    assert result == []
    assert parser.calls == 0
    assert scalar_count(db_session, Listing) == 0


def test_run_all_searches_processes_active_search_with_null_next_run(
    monkeypatch, db_session
):
    search = make_search(db_session)
    assert search.next_run_at is None
    patch_session_local(monkeypatch, db_session)
    parser = FakeParser([[card("1")]])

    result = MonitorService(
        parser=parser, scorer=FakeScorer(), notifier=FakeNotifier()
    ).run_all_searches()

    assert parser.calls == 1
    assert result[0]["search"] == search.name
    assert result[0]["baseline_run"] is True
    assert scalar_count(db_session, Listing) == 1


def test_run_all_searches_processes_active_search_scheduled_in_past(
    monkeypatch, db_session
):
    search = make_search(db_session)
    search.next_run_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    db_session.commit()
    patch_session_local(monkeypatch, db_session)
    parser = FakeParser([[card("1")]])

    result = MonitorService(
        parser=parser, scorer=FakeScorer(), notifier=FakeNotifier()
    ).run_all_searches()

    assert parser.calls == 1
    assert result[0]["search"] == search.name
    assert result[0]["baseline_run"] is True
    assert scalar_count(db_session, Listing) == 1


def test_successful_run_updates_next_run_at(monkeypatch, db_session):
    search = make_search(db_session, poll_interval_sec=45)
    patch_session_local(monkeypatch, db_session)

    result = MonitorService(
        parser=FakeParser([[card("1")]]),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    ).run_all_searches()

    db_session.refresh(search)
    assert result[0]["search"] == search.name
    assert search.last_success_at is not None
    assert search.next_run_at == search.last_success_at + timedelta(seconds=45)
