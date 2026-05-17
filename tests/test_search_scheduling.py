from datetime import datetime, timedelta

from app.repositories import search_repository as search_repository_module
from app.repositories.search_repository import SearchRepository


def make_search(db_session, poll_interval_sec=180):
    repo = SearchRepository(db_session)
    search = repo.create(
        name="schedule",
        source_url="https://www.avito.ru/test",
        poll_interval_sec=poll_interval_sec,
    )
    db_session.commit()
    return search


def test_successful_check_schedules_normal_interval_with_jitter(monkeypatch, db_session):
    monkeypatch.setattr(search_repository_module.random, "randint", lambda _a, _b: 10)
    search = make_search(db_session, poll_interval_sec=60)
    search.fail_count = 3
    search.last_error = "old error"
    checked_at = datetime(2026, 5, 17, 12, 0, 0)

    SearchRepository(db_session).record_successful_check(search, checked_at)

    assert search.fail_count == 0
    assert search.last_error == ""
    assert search.last_success_at == checked_at
    assert search.next_run_at == checked_at + timedelta(seconds=70)


def test_failed_check_applies_exponential_backoff_with_jitter(monkeypatch, db_session):
    monkeypatch.setattr(search_repository_module.random, "randint", lambda _a, _b: -5)
    search = make_search(db_session, poll_interval_sec=180)
    search.fail_count = 1
    checked_at = datetime(2026, 5, 17, 12, 0, 0)

    SearchRepository(db_session).record_failed_check(search, checked_at, "boom")

    assert search.fail_count == 2
    assert search.last_error == "boom"
    assert search.next_run_at == checked_at + timedelta(seconds=355)


def test_failed_check_backoff_is_capped_at_7200_seconds(monkeypatch, db_session):
    monkeypatch.setattr(search_repository_module.random, "randint", lambda _a, _b: 0)
    search = make_search(db_session, poll_interval_sec=180)
    search.fail_count = 10
    checked_at = datetime(2026, 5, 17, 12, 0, 0)

    SearchRepository(db_session).record_failed_check(search, checked_at, "boom")

    assert search.fail_count == 11
    assert search.next_run_at == checked_at + timedelta(seconds=7200)


def test_next_run_at_is_never_before_checked_at(monkeypatch, db_session):
    monkeypatch.setattr(search_repository_module.random, "randint", lambda _a, _b: -15)
    search = make_search(db_session, poll_interval_sec=5)
    checked_at = datetime(2026, 5, 17, 12, 0, 0)

    SearchRepository(db_session).record_successful_check(search, checked_at)

    assert search.next_run_at == checked_at


def test_success_after_failure_resets_to_normal_interval(monkeypatch, db_session):
    monkeypatch.setattr(search_repository_module.random, "randint", lambda _a, _b: 0)
    search = make_search(db_session, poll_interval_sec=90)
    repo = SearchRepository(db_session)
    first_checked_at = datetime(2026, 5, 17, 12, 0, 0)
    second_checked_at = datetime(2026, 5, 17, 12, 5, 0)

    repo.record_failed_check(search, first_checked_at, "temporary failure")
    repo.record_successful_check(search, second_checked_at)

    assert search.fail_count == 0
    assert search.last_error == ""
    assert search.next_run_at == second_checked_at + timedelta(seconds=90)
