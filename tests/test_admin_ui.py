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
    assert "New search" in client.get("/admin/searches/new").text


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
    assert response.headers["location"].endswith("/admin/searches?api_key=secret")


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
