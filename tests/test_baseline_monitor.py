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

    async def begin_cycle(self):
        return None

    async def end_cycle(self):
        return None

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
    channel_name = "telegram"

    def __init__(self):
        self.messages = []
        self.payloads = []
        self.channels = [self]

    async def send_listing_alert(self, message: str, payload: dict | None = None):
        self.messages.append(message)
        self.payloads.append(payload)
        return [self.channel_name]


def card(
    external_id: str,
    price: float = 100.0,
    area_m2: float | None = None,
    title: str | None = None,
    address: str = "",
    raw: dict | None = None,
    published_label: str = "",
    published_at: datetime | None = None,
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
        published_label=published_label,
        published_at=published_at,
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
    assert scalar_count(db_session, ListingSnapshot) == 0
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
    assert scalar_count(db_session, ListingSnapshot) == 1
    assert scalar_count(db_session, AlertSent) == 1
    assert scorer.cards == ["2"]
    assert len(notifier.messages) == 1
    assert notifier.payloads[0]["search_name"] == search.name


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
    assert scalar_count(db_session, ListingSnapshot) == 0
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
    assert scalar_count(db_session, ListingSnapshot) == 0
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
    assert scalar_count(db_session, ListingSnapshot) == 0
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
    assert scalar_count(db_session, ListingSnapshot) == 1
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
    assert scalar_count(db_session, ListingSnapshot) == 0
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
    assert scalar_count(db_session, ListingSnapshot) == 1


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
    assert scalar_count(db_session, ListingSnapshot) == 1
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
    min_next_run_at = search.last_success_at + timedelta(seconds=30)
    max_next_run_at = search.last_success_at + timedelta(seconds=60)
    assert min_next_run_at <= search.next_run_at <= max_next_run_at


def test_min_area_filter_uses_area_parsed_from_card_text(db_session):
    from app.parsers.avito_parser import AvitoParser

    parsed_area = AvitoParser._extract_area_m2("Студия, 32,5 м², 4/12 эт.")
    search = make_search(db_session, filters_json={"min_area": 40.0})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1", area_m2=45.0)],
                [card("1", area_m2=45.0), card("2", area_m2=parsed_area)],
            ]
        ),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert parsed_area == 32.5
    assert result["filtered"] == 1
    assert result["scored"] == 0
    assert result["alerted"] == 0


def test_max_area_filter_uses_area_parsed_from_card_text(db_session):
    from app.parsers.avito_parser import AvitoParser

    parsed_area = AvitoParser._extract_area_m2("3-к. квартира, 74 кв. м, 10/16 эт.")
    search = make_search(db_session, filters_json={"max_area": 60.0})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1", area_m2=45.0)],
                [card("1", area_m2=45.0), card("2", area_m2=parsed_area)],
            ]
        ),
        scorer=scorer,
        notifier=notifier,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert parsed_area == 74.0
    assert result["filtered"] == 1
    assert result["scored"] == 0
    assert result["alerted"] == 0



def test_max_age_hours_allows_fresh_listing(db_session):
    now = datetime(2026, 5, 17, 12, 0, 0)
    search = make_search(db_session, filters_json={"max_age_hours": 2})
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1", published_at=now - timedelta(hours=1))],
                [
                    card("1", published_at=now - timedelta(hours=1)),
                    card("2", published_at=now - timedelta(minutes=30)),
                ],
            ]
        ),
        scorer=scorer,
        notifier=notifier,
        now_func=lambda: now,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["filtered_by_publication_date"] == 0
    assert result["scored"] == 1
    assert result["alerted"] == 1


def test_max_age_hours_filters_old_listing(db_session):
    now = datetime(2026, 5, 17, 12, 0, 0)
    search = make_search(db_session, filters_json={"max_age_hours": 2})
    service = MonitorService(
        parser=FakeParser(
            [[card("1")], [card("1"), card("2", published_at=now - timedelta(hours=3))]]
        ),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
        now_func=lambda: now,
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["filtered"] == 1
    assert result["filtered_by_rules"] == 0
    assert result["filtered_by_publication_date"] == 1
    assert result["scored"] == 0
    assert scalar_count(db_session, Listing) == 2
    assert scalar_count(db_session, ListingSnapshot) == 0


def test_published_on_date_allows_matching_moscow_date(db_session):
    search = make_search(db_session, filters_json={"published_on_date": "2026-05-17"})
    service = MonitorService(
        parser=FakeParser(
            [[card("1")], [card("1"), card("2", published_at=datetime(2026, 5, 16, 21, 30, 0))]]
        ),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
        now_func=lambda: datetime(2026, 5, 17, 12, 0, 0),
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["filtered_by_publication_date"] == 0
    assert result["alerted"] == 1


def test_published_on_date_filters_non_matching_moscow_date(db_session):
    search = make_search(db_session, filters_json={"published_on_date": "2026-05-17"})
    service = MonitorService(
        parser=FakeParser(
            [[card("1")], [card("1"), card("2", published_at=datetime(2026, 5, 15, 21, 30, 0))]]
        ),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
        now_func=lambda: datetime(2026, 5, 17, 12, 0, 0),
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["filtered_by_publication_date"] == 1
    assert result["alerted"] == 0


def test_require_published_at_filters_unknown_published_at(db_session):
    search = make_search(db_session, filters_json={"require_published_at": True})
    service = MonitorService(
        parser=FakeParser([[card("1")], [card("1"), card("2")]]),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["filtered_by_publication_date"] == 1
    assert result["scored"] == 0
    assert result["alerted"] == 0


def test_baseline_ignores_publication_filters_but_saves_records(db_session):
    search = make_search(
        db_session,
        filters_json={"require_published_at": True, "max_age_hours": 1},
    )
    scorer = FakeScorer()
    notifier = FakeNotifier()
    service = MonitorService(
        parser=FakeParser([[card("1")]]),
        scorer=scorer,
        notifier=notifier,
    )

    result = run(service, db_session, search)

    assert result["baseline_run"] is True
    assert result["created"] == 1
    assert result["filtered_by_publication_date"] == 0
    assert result["scored"] == 0
    assert result["alerted"] == 0
    assert scalar_count(db_session, Listing) == 1
    assert scalar_count(db_session, ListingSnapshot) == 0
    assert scorer.cards == []
    assert notifier.messages == []


def test_listing_and_snapshot_store_publication_fields(db_session):
    published_at = datetime(2026, 5, 17, 9, 34, 0)
    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1")],
                [card("1"), card("2", published_label="Сегодня 12:34", published_at=published_at)],
            ]
        ),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )

    run(service, db_session, search)
    run(service, db_session, search)

    listing = db_session.scalar(select(Listing).where(Listing.external_id == "2"))
    snapshot = db_session.scalar(
        select(ListingSnapshot).where(ListingSnapshot.external_id == "2")
    )
    assert listing.published_label == "Сегодня 12:34"
    assert listing.published_at == published_at
    assert snapshot.published_label == "Сегодня 12:34"
    assert snapshot.published_at == published_at
    assert snapshot.payload_json["llm_score"]["summary"] == "score for 2"


def test_existing_listing_updates_publication_fields_when_seen_again(db_session):
    first_published_at = datetime(2026, 5, 17, 9, 34, 0)
    second_published_at = datetime(2026, 5, 17, 10, 10, 0)
    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser(
            [
                [card("1", published_label="Сегодня 12:34", published_at=first_published_at)],
                [card("1", published_label="Сегодня 13:10", published_at=second_published_at)],
            ]
        ),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )

    run(service, db_session, search)
    run(service, db_session, search)

    listing = db_session.scalar(select(Listing).where(Listing.external_id == "1"))
    assert listing.published_label == "Сегодня 13:10"
    assert listing.published_at == second_published_at


def test_channel_specific_dedupe_keys_recorded(db_session):
    class Channel:
        def __init__(self, name: str):
            self.channel_name = name

    class MultiNotifier:
        def __init__(self):
            self.channels = [Channel("email"), Channel("jsonl")]

        async def send_listing_alert(self, message: str, payload: dict):
            return ["email", "jsonl"]

    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser([[card("1")], [card("1"), card("2")]]),
        scorer=FakeScorer(),
        notifier=MultiNotifier(),
    )

    run(service, db_session, search)
    run(service, db_session, search)

    rows = db_session.scalars(select(AlertSent)).all()
    keys = {row.dedupe_key for row in rows}
    assert "email:new:2" in keys
    assert "jsonl:new:2" in keys


def test_google_sheets_dedupe_recorded_after_success(db_session):
    class GoogleOnlyNotifier:
        def __init__(self):
            class Channel:
                channel_name = "google_sheets"

            self.channels = [Channel()]

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            return ["google_sheets"]

    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser([[card("1")], [card("1"), card("2")]]),
        scorer=FakeScorer(),
        notifier=GoogleOnlyNotifier(),
    )

    run(service, db_session, search)
    run(service, db_session, search)

    row = db_session.scalar(
        select(AlertSent).where(AlertSent.dedupe_key == "google_sheets:new:2")
    )
    assert row is not None


def test_google_sheets_dedupe_not_recorded_after_failed_delivery(db_session):
    class GoogleFailNotifier:
        def __init__(self):
            class Channel:
                channel_name = "google_sheets"

            self.channels = [Channel()]

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            return []

    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser([[card("1")], [card("1"), card("2")]]),
        scorer=FakeScorer(),
        notifier=GoogleFailNotifier(),
    )

    run(service, db_session, search)
    result = run(service, db_session, search)

    assert result["alerted"] == 0
    row = db_session.scalar(
        select(AlertSent).where(AlertSent.dedupe_key == "google_sheets:new:2")
    )
    assert row is None


def test_existing_listing_retries_google_sheets_after_previous_failure(db_session):
    class GoogleFailNotifier:
        def __init__(self):
            class Channel:
                channel_name = "google_sheets"

            self.channels = [Channel()]
            self.calls = 0

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            self.calls += 1
            return []

    class GoogleSuccessNotifier:
        def __init__(self):
            class Channel:
                channel_name = "google_sheets"

            self.channels = [Channel()]
            self.calls = 0

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            self.calls += 1
            return ["google_sheets"]

    search = make_search(db_session)
    run(
        MonitorService(
            parser=FakeParser([[card("1")]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )

    fail_notifier = GoogleFailNotifier()
    fail_service = MonitorService(
        parser=FakeParser([[card("1"), card("2")]]),
        scorer=FakeScorer(),
        notifier=fail_notifier,
    )
    run(fail_service, db_session, search)

    listing = db_session.scalar(select(Listing).where(Listing.external_id == "2"))
    assert listing is not None
    fail_row = db_session.scalar(
        select(AlertSent).where(AlertSent.dedupe_key == "google_sheets:new:2")
    )
    assert fail_row is None

    success_notifier = GoogleSuccessNotifier()
    success_service = MonitorService(
        parser=FakeParser([[card("1"), card("2")]]),
        scorer=FakeScorer(),
        notifier=success_notifier,
    )
    run(success_service, db_session, search)

    success_row = db_session.scalar(
        select(AlertSent).where(AlertSent.dedupe_key == "google_sheets:new:2")
    )
    assert success_row is not None
    assert success_notifier.calls == 1


def test_retry_sends_only_pending_channels(db_session):
    class Channel:
        def __init__(self, name: str, should_succeed: bool):
            self.channel_name = name
            self.should_succeed = should_succeed
            self.calls = 0

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            self.calls += 1
            if self.should_succeed:
                return True
            raise RuntimeError(f"{self.channel_name} failed")

    class MultiNotifier:
        def __init__(self, email_success: bool, sheets_success: bool):
            self.email = Channel("email", email_success)
            self.sheets = Channel("google_sheets", sheets_success)
            self.channels = [self.email, self.sheets]

    search = make_search(db_session)
    run(
        MonitorService(
            parser=FakeParser([[card("1")]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )

    first_notifier = MultiNotifier(email_success=True, sheets_success=False)
    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2")]]),
            scorer=FakeScorer(),
            notifier=first_notifier,
        ),
        db_session,
        search,
    )
    assert first_notifier.email.calls == 1
    assert first_notifier.sheets.calls == 1

    second_notifier = MultiNotifier(email_success=True, sheets_success=True)
    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2")]]),
            scorer=FakeScorer(),
            notifier=second_notifier,
        ),
        db_session,
        search,
    )
    assert second_notifier.email.calls == 0
    assert second_notifier.sheets.calls == 1


def test_baseline_listing_price_change_creates_snapshot_without_new_alert(db_session):
    search = make_search(db_session)
    notifier = FakeNotifier()

    run(
        MonitorService(
            parser=FakeParser([[card("1", price=100.0)]]),
            scorer=FakeScorer(),
            notifier=notifier,
        ),
        db_session,
        search,
    )
    result = run(
        MonitorService(
            parser=FakeParser([[card("1", price=130.0)]]),
            scorer=FakeScorer(),
            notifier=notifier,
        ),
        db_session,
        search,
    )

    assert result["price_changed"] == 1
    assert result["alerted"] == 0
    assert scalar_count(db_session, ListingSnapshot) == 1
    assert scalar_count(db_session, AlertSent) == 0


def test_filtered_listing_not_alerted_later_after_price_change_snapshot(db_session):
    search = make_search(db_session, filters_json={"max_price": 100.0})

    run(
        MonitorService(
            parser=FakeParser([[card("1", price=50.0)]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )

    first_result = run(
        MonitorService(
            parser=FakeParser([[card("1", price=50.0), card("2", price=150.0)]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )
    assert first_result["filtered"] == 1
    assert scalar_count(db_session, AlertSent) == 0

    class GoogleSuccessNotifier:
        def __init__(self):
            class Channel:
                channel_name = "google_sheets"
                calls = 0

                async def send_listing_alert(self, message: str, payload: dict | None = None):
                    self.calls += 1
                    return True

            self.channel = Channel()
            self.channels = [self.channel]

    notifier = GoogleSuccessNotifier()
    second_result = run(
        MonitorService(
            parser=FakeParser([[card("1", price=50.0), card("2", price=160.0)]]),
            scorer=FakeScorer(),
            notifier=notifier,
        ),
        db_session,
        search,
    )
    assert second_result["price_changed"] == 1
    assert second_result["alerted"] == 0
    assert notifier.channel.calls == 0


def test_retry_uses_latest_scored_snapshot_when_newer_price_snapshot_has_no_llm(db_session):
    class Channel:
        def __init__(self, name: str, should_succeed: bool):
            self.channel_name = name
            self.should_succeed = should_succeed
            self.calls = 0

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            self.calls += 1
            if self.should_succeed:
                return True
            raise RuntimeError(f"{self.channel_name} failed")

    class MultiNotifier:
        def __init__(self, email_success: bool, sheets_success: bool):
            self.email = Channel("email", email_success)
            self.sheets = Channel("google_sheets", sheets_success)
            self.channels = [self.email, self.sheets]

    search = make_search(db_session)
    run(
        MonitorService(
            parser=FakeParser([[card("1")]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )

    first_notifier = MultiNotifier(email_success=True, sheets_success=False)
    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2", price=200.0)]]),
            scorer=FakeScorer(),
            notifier=first_notifier,
        ),
        db_session,
        search,
    )
    assert first_notifier.email.calls == 1
    assert first_notifier.sheets.calls == 1

    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2", price=220.0)]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )

    retry_notifier = MultiNotifier(email_success=True, sheets_success=True)
    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2", price=220.0)]]),
            scorer=FakeScorer(),
            notifier=retry_notifier,
        ),
        db_session,
        search,
    )

    row = db_session.scalar(
        select(AlertSent).where(AlertSent.dedupe_key == "google_sheets:new:2")
    )
    assert row is not None
    assert retry_notifier.email.calls == 0
    assert retry_notifier.sheets.calls == 1


def test_retry_alert_payload_includes_search_name(db_session):
    class CaptureChannel:
        def __init__(self, name: str, should_succeed: bool):
            self.channel_name = name
            self.should_succeed = should_succeed
            self.payloads = []

        async def send_listing_alert(self, message: str, payload: dict | None = None):
            self.payloads.append(payload)
            if self.should_succeed:
                return True
            raise RuntimeError(f"{self.channel_name} failed")

    class MultiNotifier:
        def __init__(self, email_success: bool, sheets_success: bool):
            self.email = CaptureChannel("email", email_success)
            self.sheets = CaptureChannel("google_sheets", sheets_success)
            self.channels = [self.email, self.sheets]

    search = make_search(db_session, name="commercial")
    run(
        MonitorService(
            parser=FakeParser([[card("1")]]),
            scorer=FakeScorer(),
            notifier=FakeNotifier(),
        ),
        db_session,
        search,
    )
    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2")]]),
            scorer=FakeScorer(),
            notifier=MultiNotifier(email_success=True, sheets_success=False),
        ),
        db_session,
        search,
    )

    retry_notifier = MultiNotifier(email_success=True, sheets_success=True)
    run(
        MonitorService(
            parser=FakeParser([[card("1"), card("2")]]),
            scorer=FakeScorer(),
            notifier=retry_notifier,
        ),
        db_session,
        search,
    )

    assert retry_notifier.sheets.payloads[0]["search_name"] == "commercial"
