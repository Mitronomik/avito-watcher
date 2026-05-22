from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.main import create_app
from app.models.search_job import SearchJob
from app.parsers.errors import ParserError, ParserErrorType


def test_create_app_default_admin_routes_disabled():
    app = create_app()
    assert not any(route.path == "/admin/searches" for route in app.routes)


def test_create_app_with_admin_enabled_includes_admin_routes():
    app = create_app(admin_ui_enabled=True)
    assert any(route.path == "/admin/searches" for route in app.routes)


def make_client(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.db import session as db_session_module

    def override_db():
        with Session() as s:
            yield s

    monkeypatch.setattr(settings, "api_key", "")
    test_app = create_app(admin_ui_enabled=True)
    test_app.dependency_overrides[db_session_module.get_db] = override_db
    return TestClient(test_app), Session


def create_job(Session, name="test_job"):
    with Session() as s:
        job = SearchJob(name=name, source_url="https://www.avito.ru/moskva/kvartiry", filters_json={"human_title": "T"}, poll_interval_sec=180)
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def test_list_and_new(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_job(Session)
    assert "test_job" in client.get("/admin/searches").text
    page = client.get("/admin/searches/new").text
    assert "New search" in page
    for heading in ("Basic", "Avito source", "Internal filters", "Metadata", "Runtime"):
        assert heading in page
    assert "name='profile'" in page
    assert "name='category'" in page
    assert "name='city'" in page
    assert "name='seller'" in page
    assert "name='floor'" in page


def test_api_key_query_preserved_in_links_and_forms(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_job(Session)
    monkeypatch.setattr(settings, "api_key", "secret")
    page = client.get("/admin/searches?api_key=secret").text
    assert "api_key=secret" in page
    assert "/admin/searches/new?api_key=secret" in page
    new_page = client.get("/admin/searches/new?api_key=secret").text
    assert "action='/admin/searches?api_key=secret'" in new_page
    response = client.post("/admin/searches?api_key=secret", data={"name": "query_key", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/searches?saved=1&api_key=secret")


def test_create_and_edit_and_preserve(monkeypatch):
    client, Session = make_client(monkeypatch)
    resp = client.post("/admin/searches", data={"name": "abc_job", "source_url": "https://m.avito.ru/x", "poll_interval_sec": "300", "human_title": "Hello", "include_keywords": "a,b", "is_active": "on"}, follow_redirects=False)
    assert resp.status_code == 303
    with Session() as s:
        job = s.query(SearchJob).filter_by(name="abc_job").one()
        assert job.filters_json["human_title"] == "Hello"
        assert job.filters_json["include_keywords"] == ["a", "b"]
        job.baseline_initialized = True
        job.baseline_initialized_at = datetime(2026, 1, 1)
        s.commit()
        job_id = job.id
    assert "abc_job" in client.get(f"/admin/searches/{job_id}/edit").text
    client.post(f"/admin/searches/{job_id}", data={"name": "abc_job", "source_url": "https://www.avito.ru/ok", "poll_interval_sec": "600", "human_title": "Hi", "min_price": "100", "is_active": ""}, follow_redirects=False)
    with Session() as s:
        job = s.get(SearchJob, job_id)
        assert job.source_url == "https://www.avito.ru/ok"
        assert job.poll_interval_sec == 600
        assert job.filters_json["min_price"] == 100.0
        assert job.filters_json["human_title"] == "Hi"
        assert job.baseline_initialized is True
        assert job.baseline_initialized_at == datetime(2026, 1, 1)


def test_duplicate_name_rejected(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_job(Session, name="dup")
    assert "name already exists" in client.post("/admin/searches", data={"name": "dup", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1"}).text


def test_edit_duplicate_name_rejected_and_same_name_allowed(monkeypatch):
    client, Session = make_client(monkeypatch)
    first_id = create_job(Session, name="first")
    create_job(Session, name="second")
    bad = client.post(f"/admin/searches/{first_id}", data={"name": "second", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1"})
    assert "name already exists" in bad.text
    ok = client.post(f"/admin/searches/{first_id}", data={"name": "first", "source_url": "https://www.avito.ru/b", "poll_interval_sec": "1"}, follow_redirects=False)
    assert ok.status_code == 303


def test_reset_activate_deactivate(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session)
    with Session() as s:
        job = s.get(SearchJob, job_id)
        job.baseline_initialized = True
        job.baseline_initialized_at = datetime(2026, 1, 1)
        job.next_run_at = datetime(2026, 1, 2)
        s.commit()
    client.post(f"/admin/searches/{job_id}/deactivate")
    client.post(f"/admin/searches/{job_id}/activate")
    client.post(f"/admin/searches/{job_id}/reset-baseline")
    with Session() as s:
        job = s.get(SearchJob, job_id)
        assert job.is_active is True
        assert job.baseline_initialized is False
        assert job.baseline_initialized_at is None
        assert job.next_run_at is None


def test_validation_numeric_and_empty_fields(monkeypatch):
    client, Session = make_client(monkeypatch)
    assert "name must match" in client.post("/admin/searches", data={"name": "!!", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1"}).text
    assert "valid avito.ru URL" in client.post("/admin/searches", data={"name": "abc", "source_url": "https://example.com", "poll_interval_sec": "1"}).text
    client.post("/admin/searches", data={"name": "abc2", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "min_area": "40", "profile": "", "city": ""})
    with Session() as s:
        job = s.query(SearchJob).filter_by(name="abc2").one()
        assert job.filters_json["min_area"] == 40.0
        assert "city" not in job.filters_json
        assert "profile" not in job.filters_json


def test_edit_form_selects_existing_metadata_values(monkeypatch):
    client, Session = make_client(monkeypatch)
    with Session() as s:
        job = SearchJob(
            name="meta_job",
            source_url="https://www.avito.ru/spb/kvartiry",
            poll_interval_sec=180,
            filters_json={"profile": "smoke", "category": "commercial", "city": "murino", "seller": "agency", "floor": "not_first"},
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
    page = client.get(f"/admin/searches/{job_id}/edit").text
    assert "<option value='smoke' selected>smoke</option>" in page
    assert "<option value='commercial' selected>commercial</option>" in page
    assert "<option value='murino' selected>murino</option>" in page
    assert "<option value='agency' selected>agency</option>" in page
    assert "<option value='not_first' selected>not_first</option>" in page


def test_create_freshness_preset_sets_max_age_hours(monkeypatch):
    client, Session = make_client(monkeypatch)
    resp = client.post(
        "/admin/searches",
        data={"name": "fresh_job", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "180", "freshness_preset": "12", "max_age_hours": "99"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        job = s.query(SearchJob).filter_by(name="fresh_job").one()
        assert job.filters_json["max_age_hours"] == 12.0


def test_create_freshness_custom_uses_typed_max_age_hours(monkeypatch):
    client, Session = make_client(monkeypatch)
    resp = client.post(
        "/admin/searches",
        data={"name": "custom_fresh", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "180", "freshness_preset": "custom", "max_age_hours": "36"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        job = s.query(SearchJob).filter_by(name="custom_fresh").one()
        assert job.filters_json["max_age_hours"] == 36.0


def test_validation_error_preserves_selected_and_typed_values(monkeypatch):
    client, _ = make_client(monkeypatch)
    bad = client.post(
        "/admin/searches",
        data={
            "name": "bad_fields",
            "source_url": "https://www.avito.ru/a",
            "poll_interval_sec": "1",
            "freshness_preset": "custom",
            "max_age_hours": "oops",
            "profile": "smoke",
            "category": "flats_rent",
            "city": "kudrovo",
            "seller": "owner",
            "floor": "first",
            "include_keywords": "x,y",
        },
    )
    page = bad.text
    assert "max_age_hours must be a valid number" in page
    assert "value='oops'" in page
    assert "<option value='smoke' selected>smoke</option>" in page
    assert "<option value='flats_rent' selected>flats_rent</option>" in page
    assert "<option value='kudrovo' selected>kudrovo</option>" in page
    assert "<option value='owner' selected>owner</option>" in page
    assert "<option value='first' selected>first</option>" in page
    assert "value='x,y'" in page


def test_run_once_parser_error_and_generic_and_keyboard(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session)

    class ParserErrService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, _search_id):
            raise ParserError(ParserErrorType.LAYOUT_CHANGED, "layout")

    monkeypatch.setattr("app.admin.MonitorService", ParserErrService)
    monkeypatch.setattr("app.admin._build_parser", lambda: object())
    monkeypatch.setattr("app.admin._parser_stats_snapshot", lambda _p: {"engine_used": "x"})
    text = client.post(f"/admin/searches/{job_id}/run-once").text
    assert "layout_changed" in text
    assert "parser_stats" in text

    class GenericErrService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, _search_id):
            raise ValueError("boom")

    monkeypatch.setattr("app.admin.MonitorService", GenericErrService)
    text = client.post(f"/admin/searches/{job_id}/run-once").text
    assert "ValueError" in text

    class InterruptService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, _search_id):
            raise KeyboardInterrupt()

    monkeypatch.setattr("app.admin.MonitorService", InterruptService)
    with pytest.raises(KeyboardInterrupt):
        client.post(f"/admin/searches/{job_id}/run-once")


def test_searches_dashboard_statuses_actions_and_previews(monkeypatch):
    client, Session = make_client(monkeypatch)
    with Session() as s:
        due_error = SearchJob(
            name="due_error",
            source_url="https://www.avito.ru/moskva/kvartiry/dlinnyy-url/" + ("a" * 180),
            poll_interval_sec=180,
            is_active=True,
            baseline_initialized=False,
            next_run_at=None,
            fail_count=1,
            last_error="X" * 240,
        )
        waiting_healthy = SearchJob(
            name="waiting_healthy",
            source_url="https://www.avito.ru/spb/kommercheskaya_nedvizhimost",
            poll_interval_sec=180,
            is_active=True,
            baseline_initialized=True,
            next_run_at=datetime(2999, 1, 1),
            fail_count=0,
            last_error="",
        )
        inactive = SearchJob(
            name="inactive",
            source_url="https://www.avito.ru/kazan/kvartiry",
            poll_interval_sec=180,
            is_active=False,
            baseline_initialized=True,
            next_run_at=datetime(2999, 1, 1),
            fail_count=0,
            last_error="",
        )
        s.add_all([due_error, waiting_healthy, inactive])
        s.commit()
        s.refresh(due_error)
        s.refresh(waiting_healthy)

    page = client.get("/admin/searches").text
    assert "Active" in page and "Inactive" in page
    assert "Baseline ready" in page and "Needs baseline" in page
    assert "Error" in page and "Healthy" in page
    assert "Due" in page and "Waiting" in page
    assert "due now" in page
    assert "target='_blank'" in page and "rel='noopener noreferrer'" in page
    assert f"python3 -m app.cli run-once --search-id {due_error.id}" in page
    assert f"python3 -m app.cli run-once --search-id {waiting_healthy.id}" in page
    assert ("X" * 160) in page
    assert ("X" * 161) not in page


def test_searches_dashboard_api_key_preserved_in_new_links(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session, name="api_keep")
    monkeypatch.setattr(settings, "api_key", "secret")
    page = client.get("/admin/searches?api_key=secret").text
    assert f"/admin/searches/{job_id}/edit?api_key=secret" in page
    assert f"/admin/searches/{job_id}/deactivate?api_key=secret" in page
    assert f"/admin/searches/{job_id}/reset-baseline?api_key=secret" in page
    assert f"/admin/searches/{job_id}/run-once?api_key=secret" in page


def test_legacy_name_edit_without_name_change_succeeds(monkeypatch):
    client, Session = make_client(monkeypatch)
    legacy_id = create_job(Session, name="СПб коммерческая")
    resp = client.post(f"/admin/searches/{legacy_id}", data={"name": "СПб коммерческая", "source_url": "https://www.avito.ru/spb/a", "poll_interval_sec": "300", "human_title": "Legacy updated"}, follow_redirects=False)
    assert resp.status_code == 303
    with Session() as s:
        job = s.get(SearchJob, legacy_id)
        assert job.filters_json["human_title"] == "Legacy updated"


def test_legacy_name_edit_to_invalid_non_slug_fails(monkeypatch):
    client, Session = make_client(monkeypatch)
    legacy_id = create_job(Session, name="СПб коммерческая")
    bad = client.post(f"/admin/searches/{legacy_id}", data={"name": "другое имя", "source_url": "https://www.avito.ru/spb/a", "poll_interval_sec": "300"})
    assert "name must match" in bad.text
    assert "Nothing was saved because validation failed." in bad.text


def test_legacy_name_edit_to_valid_slug_succeeds(monkeypatch):
    client, Session = make_client(monkeypatch)
    legacy_id = create_job(Session, name="СПб коммерческая")
    resp = client.post(f"/admin/searches/{legacy_id}", data={"name": "spb_kommerc", "source_url": "https://www.avito.ru/spb/a", "poll_interval_sec": "300"}, follow_redirects=False)
    assert resp.status_code == 303
    with Session() as s:
        assert s.get(SearchJob, legacy_id).name == "spb_kommerc"


def test_validation_error_navigation_links_and_api_key(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session, name="legacy_name")
    monkeypatch.setattr(settings, "api_key", "secret")
    bad = client.post(f"/admin/searches/{job_id}?api_key=secret", data={"name": "!!", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "return_url": f"/admin/searches/{job_id}/edit"})
    assert "Nothing was saved because validation failed." in bad.text
    assert ">Back<" in bad.text
    assert "api_key=secret" in bad.text
    assert "Back to search list" in bad.text


def test_safe_return_url_used_after_update(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session, name="safejob")
    resp = client.post(f"/admin/searches/{job_id}", data={"name": "safejob", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "return_url": "/admin/searches/new?x=1"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/admin/searches/new?x=1")


def test_unsafe_return_url_ignored(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session, name="unsafejob")
    resp = client.post(f"/admin/searches/{job_id}", data={"name": "unsafejob", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "return_url": "https://evil.example/x"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/searches?updated=1"


def test_success_marker_and_notice(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session, name="noticejob")
    resp = client.post(f"/admin/searches/{job_id}", data={"name": "noticejob", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1"}, follow_redirects=False)
    assert resp.headers["location"] == "/admin/searches?updated=1"
    page_saved = client.get("/admin/searches?saved=1").text
    page_updated = client.get("/admin/searches?updated=1").text
    assert "Saved successfully." in page_saved
    assert "Updated successfully." in page_updated


def test_name_field_helper_text_visible(monkeypatch):
    client, _ = make_client(monkeypatch)
    page = client.get("/admin/searches/new").text
    assert "Latin letters, digits, _ and -, 3-121 chars." in page
