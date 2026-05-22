from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.models.search_job import SearchJob


def make_client(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.db import session as db_session_module

    def override_db():
        with Session() as s:
            yield s

    app.dependency_overrides[db_session_module.get_db] = override_db
    monkeypatch.setattr(settings, "api_key", "")
    return TestClient(app), Session


def create_job(Session):
    with Session() as s:
        job = SearchJob(name="test_job", source_url="https://www.avito.ru/moskva/kvartiry", filters_json={"human_title": "T"}, poll_interval_sec=180)
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def test_list_and_new(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_job(Session)
    assert "test_job" in client.get("/admin/searches").text
    assert "New search" in client.get("/admin/searches/new").text


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


def test_run_once_and_api_key(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session)

    class DummyService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, search_id):
            return {"ok": True, "search_id": search_id}

    monkeypatch.setattr("app.admin.MonitorService", DummyService)
    monkeypatch.setattr("app.admin._build_parser", lambda: object())
    assert '&quot;ok&quot;: true' in client.post(f"/admin/searches/{job_id}/run-once").text.lower()

    monkeypatch.setattr(settings, "api_key", "secret")
    assert client.get("/admin/searches").status_code == 403
    assert client.get("/admin/searches?api_key=secret").status_code == 200
    assert client.get("/admin/searches", headers={"X-API-Key": "secret"}).status_code == 200
