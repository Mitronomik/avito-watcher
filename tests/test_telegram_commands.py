import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.bot.telegram_commands import TelegramSearchCommandHandlers
from app.repositories.search_repository import SearchRepository


class FakeMessage:
    def __init__(self) -> None:
        self.replies = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeSessionLocal:
    def __init__(self, db_session) -> None:
        self.db_session = db_session

    def __enter__(self):
        return self.db_session

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def run(coro) -> None:
    asyncio.run(coro)


def make_handler(db_session) -> TelegramSearchCommandHandlers:
    return TelegramSearchCommandHandlers(
        session_factory=lambda: FakeSessionLocal(db_session)
    )


def make_update() -> SimpleNamespace:
    return SimpleNamespace(effective_message=FakeMessage())


def make_context(*args: str) -> SimpleNamespace:
    return SimpleNamespace(args=list(args))


def test_start_and_help_show_available_commands(db_session):
    handler = make_handler(db_session)
    update = make_update()

    run(handler.start(update, make_context()))

    assert "/add <url> [name]" in update.effective_message.replies[0]
    assert "/pause <search_id>" in update.effective_message.replies[0]


def test_add_creates_search_with_custom_name_and_default_polling(db_session):
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.add(
            update,
            make_context("https://example.com/search", "Commercial", "SPb"),
        )
    )

    search = SearchRepository(db_session).list_all()[0]
    assert search.name == "Commercial SPb"
    assert search.source_url == "https://example.com/search"
    assert search.poll_interval_sec == 180
    assert search.is_active is True
    assert search.baseline_initialized is False
    assert "Search added: id=" in update.effective_message.replies[0]


def test_add_uses_default_name_when_name_is_omitted(db_session):
    handler = make_handler(db_session)
    update = make_update()

    run(handler.add(update, make_context("https://example.com/search")))

    search = SearchRepository(db_session).list_all()[0]
    assert search.name == "avito_search"
    assert search.source_url == "https://example.com/search"
    assert search.poll_interval_sec == 180
    assert search.is_active is True
    assert search.baseline_initialized is False


def test_add_rejects_non_http_url(db_session):
    handler = make_handler(db_session)
    update = make_update()

    run(handler.add(update, make_context("not-a-url", "name")))

    assert SearchRepository(db_session).list_all() == []
    assert update.effective_message.replies == ["Usage: /add <url> [name]"]


def test_add_validates_required_arguments(db_session):
    handler = make_handler(db_session)
    update = make_update()

    run(handler.add(update, make_context()))

    assert SearchRepository(db_session).list_all() == []
    assert update.effective_message.replies == ["Usage: /add <url> [name]"]


def test_list_shows_search_state_and_next_run_at(db_session):
    repo = SearchRepository(db_session)
    search = repo.create("spb", "https://www.avito.ru/all")
    search.next_run_at = datetime(2026, 5, 17, 12, 30, 0)
    db_session.commit()
    handler = make_handler(db_session)
    update = make_update()

    run(handler.list(update, make_context()))

    reply = update.effective_message.replies[0]
    assert "#1 | spb | active | baseline pending | next_run_at=2026-05-17 12:30:00" in reply


def test_pause_and_resume_use_repository_helpers(db_session, monkeypatch):
    repo = SearchRepository(db_session)
    search = repo.create("spb", "https://www.avito.ru/all")
    db_session.commit()
    calls = []
    original_pause = SearchRepository.pause
    original_resume = SearchRepository.resume

    def spy_pause(self, search_job):
        calls.append(("pause", search_job.id))
        original_pause(self, search_job)

    def spy_resume(self, search_job):
        calls.append(("resume", search_job.id))
        original_resume(self, search_job)

    monkeypatch.setattr(SearchRepository, "pause", spy_pause)
    monkeypatch.setattr(SearchRepository, "resume", spy_resume)
    handler = make_handler(db_session)
    pause_update = make_update()
    resume_update = make_update()

    run(handler.pause(pause_update, make_context(str(search.id))))
    db_session.refresh(search)
    assert search.is_active is False

    run(handler.resume(resume_update, make_context(str(search.id))))
    db_session.refresh(search)

    assert search.is_active is True
    assert calls == [("pause", search.id), ("resume", search.id)]
    assert pause_update.effective_message.replies == [f"Search paused: {search.id}"]
    assert resume_update.effective_message.replies == [f"Search resumed: {search.id}"]


def test_status_shows_counts_due_searches_and_last_errors(db_session):
    repo = SearchRepository(db_session)
    due = repo.create("due", "https://www.avito.ru/due")
    future = repo.create("future", "https://www.avito.ru/future")
    paused = repo.create("paused", "https://www.avito.ru/paused")
    future.next_run_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
    paused.is_active = False
    paused.last_error = "previous failure"
    db_session.commit()
    handler = make_handler(db_session)
    update = make_update()

    run(handler.status(update, make_context()))

    reply = update.effective_message.replies[0]
    assert "Searches: 3" in reply
    assert "Active: 2" in reply
    assert "Due now: 1" in reply
    assert f"- #{paused.id} paused: previous failure" in reply
    db_session.refresh(due)
    assert due.baseline_initialized is False


def make_search_with_filters(db_session, filters_json=None):
    repo = SearchRepository(db_session)
    search = repo.create(
        "filtered",
        "https://www.avito.ru/filtered",
        filters_json=filters_json or {},
    )
    db_session.commit()
    return search


def test_showfilters_with_empty_filters(db_session):
    search = make_search_with_filters(db_session)
    handler = make_handler(db_session)
    update = make_update()

    run(handler.showfilters(update, make_context(str(search.id))))

    assert update.effective_message.replies == [
        f"Filters for search {search.id} are empty."
    ]


def test_setfilters_numeric_values(db_session):
    search = make_search_with_filters(db_session, {"include_keywords": ["office"]})
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.setfilters(
            update,
            make_context(str(search.id), "min_price=1000000", "max_area=80.5"),
        )
    )
    db_session.refresh(search)

    assert search.filters_json == {
        "include_keywords": ["office"],
        "min_price": 1000000.0,
        "max_area": 80.5,
    }
    assert update.effective_message.replies == [
        f"Filters updated for search {search.id}: max_area, min_price"
    ]


def test_setfilters_boolean_require_published_at_true(db_session):
    search = make_search_with_filters(db_session)
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.setfilters(
            update,
            make_context(str(search.id), "require_published_at=true"),
        )
    )
    db_session.refresh(search)

    assert search.filters_json["require_published_at"] is True


def test_setfilters_keyword_list_parsing(db_session):
    search = make_search_with_filters(db_session)
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.setfilters(
            update,
            make_context(str(search.id), "exclude_keywords=доля, аренда,,ипотека"),
        )
    )
    db_session.refresh(search)

    assert search.filters_json["exclude_keywords"] == ["доля", "аренда", "ипотека"]


def test_setfilters_published_on_date_valid(db_session):
    search = make_search_with_filters(db_session)
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.setfilters(
            update,
            make_context(str(search.id), "published_on_date=2026-05-17"),
        )
    )
    db_session.refresh(search)

    assert search.filters_json["published_on_date"] == "2026-05-17"


def test_setfilters_rejects_unknown_key(db_session):
    search = make_search_with_filters(db_session, {"max_price": 10.0})
    handler = make_handler(db_session)
    update = make_update()

    run(handler.setfilters(update, make_context(str(search.id), "unknown=1")))
    db_session.refresh(search)

    assert search.filters_json == {"max_price": 10.0}
    assert "Unknown filter key: unknown" in update.effective_message.replies[0]


def test_setfilters_rejects_invalid_number(db_session):
    search = make_search_with_filters(db_session, {"max_price": 10.0})
    handler = make_handler(db_session)
    update = make_update()

    run(handler.setfilters(update, make_context(str(search.id), "max_price=cheap")))
    db_session.refresh(search)

    assert search.filters_json == {"max_price": 10.0}
    assert update.effective_message.replies == ["Invalid numeric value for max_price: cheap"]


def test_setfilters_rejects_invalid_date(db_session):
    search = make_search_with_filters(db_session, {"max_price": 10.0})
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.setfilters(
            update,
            make_context(str(search.id), "published_on_date=17-05-2026"),
        )
    )
    db_session.refresh(search)

    assert search.filters_json == {"max_price": 10.0}
    assert update.effective_message.replies == [
        "Invalid date for published_on_date: 17-05-2026. Use YYYY-MM-DD."
    ]


def test_invalid_setfilters_does_not_partially_update_filters_json(db_session):
    search = make_search_with_filters(db_session, {"max_price": 10.0})
    handler = make_handler(db_session)
    update = make_update()

    run(
        handler.setfilters(
            update,
            make_context(str(search.id), "min_price=5", "max_area=bad"),
        )
    )
    db_session.refresh(search)

    assert search.filters_json == {"max_price": 10.0}
    assert update.effective_message.replies == ["Invalid numeric value for max_area: bad"]


def test_clearfilters_resets_filters_json(db_session):
    search = make_search_with_filters(db_session, {"max_price": 10.0})
    handler = make_handler(db_session)
    update = make_update()

    run(handler.clearfilters(update, make_context(str(search.id))))
    db_session.refresh(search)

    assert search.filters_json == {}
    assert update.effective_message.replies == [f"Filters cleared for search {search.id}."]


def test_filter_commands_missing_search_id_return_helpful_message(db_session):
    handler = make_handler(db_session)
    update = make_update()

    run(handler.showfilters(update, make_context("999")))

    assert update.effective_message.replies == ["Search not found: 999"]
