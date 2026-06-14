from datetime import datetime
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.admin import redact_admin_json, redact_admin_value
from app.db.base import Base
from app.main import create_app
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.search_job import SearchJob
from app.parsers.errors import ParserError, ParserErrorType


def test_create_app_default_admin_routes_disabled():
    assert settings.admin_ui_enabled is False
    app = create_app()
    assert not any(route.path == "/admin/searches" for route in app.routes)


def test_create_app_with_admin_enabled_includes_admin_routes():
    app = create_app(admin_ui_enabled=True)
    assert any(route.path == "/admin/searches" for route in app.routes)


def test_admin_root_disabled_and_enabled_with_header_key(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret")
    disabled_app = create_app(admin_ui_enabled=False)
    assert TestClient(disabled_app).get("/admin", headers={"X-API-Key": "secret"}).status_code == 404

    enabled_app = create_app(admin_ui_enabled=True)
    assert TestClient(enabled_app).get("/admin", headers={"X-API-Key": "secret"}).status_code == 200


def make_client(monkeypatch, *, technical_ops_enabled: bool = True, allow_query_api_key: bool = True):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.db import session as db_session_module

    def override_db():
        with Session() as s:
            yield s

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "admin_ui_technical_ops_enabled", technical_ops_enabled)
    monkeypatch.setattr(settings, "admin_ui_allow_query_api_key", allow_query_api_key)
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




def create_listing(Session, **kwargs):
    with Session() as s:
        listing = Listing(
            external_id=kwargs.get('external_id', 'ext-default'),
            url=kwargs.get('url', 'https://www.avito.ru/default'),
            title=kwargs.get('title', ''),
            price=kwargs.get('price'),
            area_m2=kwargs.get('area_m2'),
            address=kwargs.get('address', ''),
            published_label=kwargs.get('published_label', ''),
            first_seen_at=kwargs.get('first_seen_at', datetime(2026, 1, 1, 0, 0, 0)),
            last_seen_at=kwargs.get('last_seen_at', datetime(2026, 1, 1, 0, 0, 0)),
        )
        s.add(listing)
        s.commit()
        s.refresh(listing)
        return listing.id



def create_listing_analysis(Session, **kwargs):
    with Session() as s:
        analysis = ListingAnalysis(
            listing_external_id=kwargs.get('listing_external_id', 'ext-default'),
            search_job_id=kwargs.get('search_job_id'),
            context_key=kwargs.get('context_key', 'global'),
            profile=kwargs.get('profile', 'default'),
            status=kwargs.get('status', 'success'),
            analysis_version=kwargs.get('analysis_version', 'det-v1'),
            input_hash=kwargs.get('input_hash', f"hash-{kwargs.get('listing_external_id', 'ext-default')}-{kwargs.get('profile', 'default')}"),
            score=kwargs.get('score'),
            verdict=kwargs.get('verdict'),
            facts_json=kwargs.get('facts_json', {}),
            risks_json=kwargs.get('risks_json', {}),
            questions_json=kwargs.get('questions_json', {}),
            report_md=kwargs.get('report_md', ''),
            error_type=kwargs.get('error_type'),
            error_message=kwargs.get('error_message'),
            created_at=kwargs.get('created_at', datetime(2026, 1, 1, 0, 0, 0)),
            updated_at=kwargs.get('updated_at', datetime(2026, 1, 1, 0, 0, 0)),
        )
        s.add(analysis)
        s.commit()
        s.refresh(analysis)
        return analysis.id


def test_list_and_new(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_job(Session)
    assert "test_job" in client.get("/admin/searches").text
    page = client.get("/admin/searches/new").text
    assert "New search" in page
    for heading in ("Basic", "Avito source", "Internal filters", "Metadata", "Runtime"):
        assert heading in page
    assert "analysis_profile controls which specialized analysis provider is used" in page
    assert "commercial_rent" in page
    assert "default fallback" in page
    assert "flat_sale" in page and "flat_rent" in page
    assert "does not affect parsing or alert delivery" in page
    assert "listing_search_matches" in page
    assert "name='analysis_profile'" in page
    assert "name='asset_type'" in page
    assert "name='deal_type'" in page
    assert "name='profile'" in page
    assert "name='category'" in page
    assert "name='city'" in page
    assert "name='seller'" in page
    assert "name='floor'" in page
    assert "name='missing_published_at_policy'" in page
    assert "name='source_sort'" in page


def test_create_saves_analysis_metadata(monkeypatch):
    client, Session = make_client(monkeypatch)
    resp = client.post(
        "/admin/searches",
        data={
            "name": "analysis_admin",
            "source_url": "https://www.avito.ru/spb/kommercheskaya_nedvizhimost/",
            "poll_interval_sec": "180",
            "analysis_profile": "commercial_rent",
            "asset_type": "commercial",
            "deal_type": "rent",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        job = s.query(SearchJob).filter_by(name="analysis_admin").one()
        assert job.filters_json["analysis_profile"] == "commercial_rent"
        assert job.filters_json["asset_type"] == "commercial"
        assert job.filters_json["deal_type"] == "rent"


def test_create_saves_missing_published_at_policy_and_source_sort(monkeypatch):
    client, Session = make_client(monkeypatch)
    resp = client.post(
        "/admin/searches",
        data={
            "name": "policy_job",
            "source_url": "https://www.avito.ru/a",
            "poll_interval_sec": "180",
            "missing_published_at_policy": "allow_when_date_sorted",
            "source_sort": "date",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        job = s.query(SearchJob).filter_by(name="policy_job").one()
        assert job.filters_json["missing_published_at_policy"] == "allow_when_date_sorted"
        assert job.filters_json["source_sort"] == "date"


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


def test_edit_form_selects_existing_published_at_policy_values(monkeypatch):
    client, Session = make_client(monkeypatch)
    with Session() as s:
        job = SearchJob(
            name="policy_meta_job",
            source_url="https://www.avito.ru/spb/kvartiry",
            poll_interval_sec=180,
            filters_json={"missing_published_at_policy": "allow_when_date_sorted", "source_sort": "date"},
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
    page = client.get(f"/admin/searches/{job_id}/edit").text
    assert "<option value='allow_when_date_sorted' selected>allow_when_date_sorted</option>" in page
    assert "<option value='date' selected>date</option>" in page


def test_edit_can_clear_published_at_policy_fields(monkeypatch):
    client, Session = make_client(monkeypatch)
    with Session() as s:
        job = SearchJob(
            name="policy_clear_job",
            source_url="https://www.avito.ru/spb/kvartiry",
            poll_interval_sec=180,
            filters_json={"human_title": "Keep me", "missing_published_at_policy": "allow", "source_sort": "date"},
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
    resp = client.post(
        f"/admin/searches/{job_id}",
        data={
            "name": "policy_clear_job",
            "source_url": "https://www.avito.ru/spb/kvartiry",
            "poll_interval_sec": "180",
            "human_title": "Keep me",
            "missing_published_at_policy": "",
            "source_sort": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with Session() as s:
        job = s.get(SearchJob, job_id)
        assert job.filters_json["human_title"] == "Keep me"
        assert "missing_published_at_policy" not in job.filters_json
        assert "source_sort" not in job.filters_json


def test_invalid_missing_published_at_policy_validation_error(monkeypatch):
    client, _ = make_client(monkeypatch)
    page = client.post(
        "/admin/searches",
        data={"name": "invalid_policy", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "missing_published_at_policy": "bad"},
    ).text
    assert "missing_published_at_policy must be one of: reject, allow, allow_when_date_sorted" in page


def test_invalid_source_sort_validation_error(monkeypatch):
    client, _ = make_client(monkeypatch)
    page = client.post(
        "/admin/searches",
        data={"name": "invalid_source_sort", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "source_sort": "price"},
    ).text
    assert "source_sort must be empty or date" in page


@pytest.mark.parametrize(
    ("max_age_hours", "expected_option"),
    [
        (12, "<option value='12' selected>12 hours</option>"),
        (24, "<option value='24' selected>24 hours</option>"),
        (36, "<option value='custom' selected>custom</option>"),
    ],
)
def test_edit_form_inferrs_freshness_preset_from_max_age_hours(monkeypatch, max_age_hours, expected_option):
    client, Session = make_client(monkeypatch)
    with Session() as s:
        job = SearchJob(
            name=f"fresh_infer_{max_age_hours}",
            source_url="https://www.avito.ru/spb/kvartiry",
            poll_interval_sec=180,
            filters_json={"max_age_hours": max_age_hours},
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
    page = client.get(f"/admin/searches/{job_id}/edit").text
    assert expected_option in page


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


def test_validation_error_keeps_submitted_custom_freshness_preset_selected(monkeypatch):
    client, _ = make_client(monkeypatch)
    bad = client.post(
        "/admin/searches",
        data={"name": "fresh_custom_bad", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "freshness_preset": "custom", "max_age_hours": "oops"},
    )
    assert "<option value='custom' selected>custom</option>" in bad.text


def test_validation_error_keeps_submitted_12_freshness_preset_selected(monkeypatch):
    client, _ = make_client(monkeypatch)
    bad = client.post(
        "/admin/searches",
        data={"name": "!!", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1", "freshness_preset": "12", "max_age_hours": "oops"},
    )
    assert "<option value='12' selected>12 hours</option>" in bad.text


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
    monkeypatch.setattr(
        "app.admin.runtime_diagnostics",
        lambda: {
            "alert_channels": ["jsonl"],
            "scoring_enabled": False,
            "scrape_preferred_engine": "camoufox",
            "scrape_allowed_engines": "both",
            "scrape_headless": True,
        },
    )
    text = client.post(f"/admin/searches/{job_id}/run-once").text
    assert "layout_changed" in text
    assert "parser_stats" in text
    assert "runtime" in text

    class GenericErrService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, _search_id):
            raise ValueError("boom")

    monkeypatch.setattr("app.admin.MonitorService", GenericErrService)
    text = client.post(f"/admin/searches/{job_id}/run-once").text
    assert "ValueError" in text
    assert "runtime" in text

    class InterruptService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, _search_id):
            raise KeyboardInterrupt()

    monkeypatch.setattr("app.admin.MonitorService", InterruptService)
    with pytest.raises(KeyboardInterrupt):
        client.post(f"/admin/searches/{job_id}/run-once")


def test_run_once_success_page_includes_runtime_json(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session)

    class OkService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, search_id):
            return {
                "ok": True,
                "search_id": search_id,
                "created": 0,
                "alerted": 0,
                "filtered": 0,
                "total_seen": 0,
                "pages_seen": 1,
                "pages_attempted": 1,
                "pagination_stopped_reason": "no_more_pages",
                "page_errors": [],
                "scored": 0,
                "parser_stats": {"engine_used": "camoufox", "layout_changed_hint": "no", "timeout_failure_count": 0, "proxy_quarantine_on_failure_count": 0},
                "delivery_attempted_by_channel": {"jsonl": 0, "telegram": 1},
                "delivery_success_by_channel": {"jsonl": 0, "telegram": 1},
                "delivery_skipped_by_channel": {"jsonl": 0, "telegram": 0},
                "delivery_failed_by_channel": {"jsonl": 0, "telegram": 0},
                "delivery_unknown_by_channel": {"jsonl": 0, "telegram": 0},
                "delivery_unsuccessful_by_channel": {"jsonl": 0, "telegram": 0},
                "elapsed_ms": 1,
                "runtime": {"alert_channels": ["jsonl"]},
            }

    monkeypatch.setattr("app.admin.MonitorService", OkService)
    text = client.post(f"/admin/searches/{job_id}/run-once").text
    assert "runtime" in text
    assert "alert_channels" in text
    assert "Delivery counters" in text
    assert "layout_changed_hint" in text
    assert "timeout_failure_count" in text
    assert "proxy_quarantine_on_failure_count" in text
    assert "neutral" in text


def test_run_once_delivery_warning_badge_when_failed(monkeypatch):
    client, Session = make_client(monkeypatch)
    job_id = create_job(Session)

    class WarnService:
        def __init__(self, parser=None):
            self.parser = parser

        def run_once(self, _search_id):
            return {
                "ok": True,
                "parser_stats": {},
                "delivery_attempted_by_channel": {"email": 2},
                "delivery_success_by_channel": {"email": 1},
                "delivery_skipped_by_channel": {"email": 0},
                "delivery_failed_by_channel": {"email": 1},
                "delivery_unknown_by_channel": {"email": 0},
                "delivery_unsuccessful_by_channel": {"email": 1},
            }

    monkeypatch.setattr("app.admin.MonitorService", WarnService)
    text = client.post(f"/admin/searches/{job_id}/run-once").text
    assert "warning" in text


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
    assert "/admin/alerts?api_key=secret" in page


def test_searches_dashboard_worker_status_block(monkeypatch, tmp_path):
    client, Session = make_client(monkeypatch)
    lock_path = tmp_path / "monitor.lock"
    lock_path.write_text("lock", encoding="utf-8")
    monkeypatch.setattr(settings, "monitor_worker_lock_path", str(lock_path))
    monkeypatch.setattr(settings, "alert_channels", "telegram, jsonl")
    monkeypatch.setattr(settings, "scoring_enabled", True)
    monkeypatch.setattr(settings, "scrape_preferred_engine", "playwright")
    monkeypatch.setattr(settings, "scrape_headless", False)
    monkeypatch.setattr(settings, "scrape_timeout_retry_once", True)
    monkeypatch.setattr(settings, "scrape_max_pages", 3)
    monkeypatch.setattr(settings, "scrape_debug_dump_html", True)
    debug_dir = tmp_path / "debug_html"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "sample.html").write_text("<html>ok</html>", encoding="utf-8")
    monkeypatch.setattr(settings, "scrape_debug_dump_dir", str(debug_dir))
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(tmp_path / "alerts.jsonl"))
    monkeypatch.setattr(settings, "jsonl_outbox_enabled", True)
    monkeypatch.setattr(settings, "google_sheets_webhook_enabled", True)
    monkeypatch.setattr(settings, "google_sheets_webhook_url", "https://example.com/hook")
    monkeypatch.setattr(settings, "google_sheets_webhook_secret", "gs-secret")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 2525)
    monkeypatch.setattr(settings, "email_enabled", False)
    monkeypatch.setattr(settings, "smtp_username", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "smtp-secret")
    monkeypatch.setattr(settings, "email_from", "from@example.com")
    monkeypatch.setattr(settings, "email_to", "to@example.com")
    monkeypatch.setattr(settings, "telegram_bot_token", "tg-secret")
    monkeypatch.setattr(settings, "telegram_chat_id", "42")
    monkeypatch.setattr(settings, "api_key", "secret")
    now = datetime.utcnow()
    with Session() as s:
        active_due = SearchJob(
            name="active_due_worker",
            source_url="https://www.avito.ru/a",
            poll_interval_sec=120,
            is_active=True,
            baseline_initialized=True,
            next_run_at=None,
            last_success_at=now,
            last_error="",
        )
        active_waiting_with_error = SearchJob(
            name="active_waiting_worker",
            source_url="https://www.avito.ru/b",
            poll_interval_sec=120,
            is_active=True,
            baseline_initialized=True,
            next_run_at=datetime(2999, 1, 1),
            last_success_at=datetime(2020, 1, 1),
            last_error="E" * 220,
        )
        inactive = SearchJob(
            name="inactive_worker",
            source_url="https://www.avito.ru/c",
            poll_interval_sec=120,
            is_active=False,
            baseline_initialized=True,
            next_run_at=None,
            last_success_at=datetime(2099, 1, 1),
            last_error="ignored",
        )
        s.add_all([active_due, active_waiting_with_error, inactive])
        s.commit()
    page = client.get("/admin/searches?api_key=secret").text
    assert "Worker status" in page
    assert "python3 -m app.workers.monitor" in page
    assert str(lock_path) in page
    assert "Lock file:</strong> exists" in page
    assert "alert_channels=telegram, jsonl" in page
    assert "scrape_preferred_engine=playwright" in page
    assert "scoring_enabled=True" in page
    assert "scrape_headless=False" in page
    assert "scrape_timeout_retry_once=True" in page
    assert "scrape_max_pages=3" in page
    assert "jsonl channel_enabled=yes jsonl_enabled=yes" in page
    assert "google_sheets channel_enabled=no integration_enabled=yes webhook_url_set=yes secret_set=yes" in page
    assert "email channel_enabled=no email_enabled=no smtp_host=smtp.example.com smtp_port=2525 username_set=yes password_set=yes email_from_set=yes email_to_set=yes" in page
    assert "telegram channel_enabled=yes token_set=yes chat_id_set=yes" in page
    assert "debug_dump_file_count=1" in page
    assert "smtp-secret" not in page
    assert "tg-secret" not in page
    assert "gs-secret" not in page
    assert "Active searches:</strong> 2" in page
    assert "Due now:</strong> 1" in page
    assert f"Last success:</strong> {now}" in page
    assert ("E" * 160) in page
    assert ("E" * 161) not in page
    assert "separate long-running process" in page
    assert "/admin/searches/new?api_key=secret" in page
    assert "start worker" not in page.lower()
    assert "stop worker" not in page.lower()


def test_worker_status_last_error_uses_latest_last_checked_at_not_id(monkeypatch):
    client, Session = make_client(monkeypatch)
    with Session() as s:
        newer_checked_lower_id = SearchJob(
            name="newer_checked_lower_id",
            source_url="https://www.avito.ru/newer",
            poll_interval_sec=120,
            is_active=True,
            baseline_initialized=True,
            last_checked_at=datetime(2026, 1, 2, 12, 0, 0),
            last_error="newer error",
        )
        older_checked_higher_id = SearchJob(
            name="older_checked_higher_id",
            source_url="https://www.avito.ru/older",
            poll_interval_sec=120,
            is_active=True,
            baseline_initialized=True,
            last_checked_at=datetime(2026, 1, 1, 12, 0, 0),
            last_error="older error",
        )
        s.add_all([newer_checked_lower_id, older_checked_higher_id])
        s.commit()
        assert newer_checked_lower_id.id < older_checked_higher_id.id
    page = client.get("/admin/searches").text
    assert "Last error:</strong> newer error" in page
    assert "Last error:</strong> older error" not in page


def test_alerts_empty_state_when_file_missing(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch)
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(tmp_path / "missing.jsonl"))
    page = client.get("/admin/alerts").text
    assert "No alerts found yet." in page


def test_alerts_empty_state_when_file_empty(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch)
    outbox = tmp_path / "alerts.jsonl"
    outbox.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(outbox))
    page = client.get("/admin/alerts").text
    assert "No alerts found yet." in page


def test_alerts_latest_first_filter_limit_and_link_attrs(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch)
    outbox = tmp_path / "alerts.jsonl"
    records = [
        {"timestamp": "2026-01-01T00:00:01Z", "search_name": "alpha", "title": "first", "url": "https://www.avito.ru/1"},
        {"timestamp": "2026-01-01T00:00:02Z", "search_name": "beta", "title": "second", "url": "https://www.avito.ru/2"},
        {"timestamp": "2026-01-01T00:00:03Z", "search_name": "alpha", "title": "third", "url": "https://www.avito.ru/3"},
    ]
    outbox.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(outbox))
    page = client.get("/admin/alerts?search_name=alpha&limit=1").text
    assert "third" in page
    assert "<td>first</td>" not in page
    assert "target='_blank'" in page
    assert "rel='noopener noreferrer'" in page


def test_alerts_limit_capped_and_invalid_json_skipped(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch)
    outbox = tmp_path / "alerts.jsonl"
    outbox.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-01-01T00:00:00Z", "search_name": "a", "title": "one", "url": "https://www.avito.ru/1"}),
                "{invalid",
                json.dumps({"timestamp": "2026-01-01T00:00:01Z", "search_name": "b", "title": "two", "url": "https://www.avito.ru/2"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(outbox))
    page = client.get("/admin/alerts?limit=9999").text
    assert "Skipped invalid JSONL lines: 1" in page
    assert "value='500'" in page


def test_alerts_api_key_preserved_in_forms_and_links(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch)
    outbox = tmp_path / "alerts.jsonl"
    outbox.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "api_key", "secret")
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(outbox))
    page = client.get("/admin/alerts?api_key=secret").text
    assert "action='/admin/alerts?api_key=secret'" in page
    assert "name='api_key' value='secret'" in page


def test_alerts_url_href_attribute_escapes_quotes(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch)
    outbox = tmp_path / "alerts.jsonl"
    outbox.write_text(
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "search_name": "quote_test",
                "title": "quoted_url",
                "url": "https://www.avito.ru/test?x='a'&y=\"b\"",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "jsonl_outbox_path", str(outbox))
    page = client.get("/admin/alerts").text
    assert "href='https://www.avito.ru/test?x=&#x27;a&#x27;&amp;y=&quot;b&quot;'" in page


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


def test_listings_empty_state(monkeypatch):
    client, _ = make_client(monkeypatch)
    page = client.get('/admin/listings').text
    assert 'No listings found yet.' in page


def test_listings_renders_newest_first(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing(Session, external_id='old', title='Old', last_seen_at=datetime(2026, 1, 1, 0, 0, 0))
    create_listing(Session, external_id='new', title='New', last_seen_at=datetime(2026, 1, 2, 0, 0, 0))
    page = client.get('/admin/listings').text
    assert page.index('new') < page.index('old')


def test_listings_limit_applied_and_capped(monkeypatch):
    client, Session = make_client(monkeypatch)
    for idx in range(510):
        create_listing(Session, external_id=f'ext-{idx}', title=f'Title {idx}', last_seen_at=datetime(2026, 1, 1, 0, 0, idx % 60))
    page_limit_2 = client.get('/admin/listings?limit=2').text
    assert page_limit_2.count('<tr><td>') == 2
    page_capped = client.get('/admin/listings?limit=9999').text
    assert page_capped.count('<tr><td>') == 500
    assert "value='500'" in page_capped


def test_listings_q_filter_title_address_external_id(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing(Session, external_id='ext-target', title='Alpha title', address='Moscow')
    create_listing(Session, external_id='ext-other', title='Beta', address='Spb target street')
    assert 'Alpha title' in client.get('/admin/listings?q=alpha').text
    assert 'Spb target street' in client.get('/admin/listings?q=target').text
    assert 'ext-target' in client.get('/admin/listings?q=ext-target').text


def test_listings_published_missing_and_present(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing(Session, external_id='missing-empty', title='Missing pub empty', published_label='')
    create_listing(Session, external_id='missing-null', title='Missing pub null', published_label=None)
    create_listing(Session, external_id='present', title='Present pub', published_label='today')

    missing_page = client.get('/admin/listings?published=missing').text
    present_page = client.get('/admin/listings?published=present').text

    assert 'Missing pub empty' in missing_page
    assert 'Missing pub null' in missing_page
    assert 'Present pub' not in missing_page

    assert 'Present pub' in present_page
    assert 'Missing pub empty' not in present_page
    assert 'Missing pub null' not in present_page


def test_listings_external_link_attrs(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing(Session, external_id='link', title='Link row', url='https://www.avito.ru/link')
    page = client.get('/admin/listings').text
    assert "target='_blank'" in page
    assert "rel='noopener noreferrer'" in page


def test_listings_api_key_preserved_in_forms_and_nav(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing(Session, external_id='key', title='Key row')
    monkeypatch.setattr(settings, 'api_key', 'secret')
    page = client.get('/admin/listings?api_key=secret').text
    assert "action='/admin/listings?api_key=secret'" in page
    assert "name='api_key' value='secret'" in page
    assert '/admin/searches?api_key=secret' in page
    assert '/admin/alerts?api_key=secret' in page


def test_searches_contains_link_to_listings(monkeypatch):
    client, _ = make_client(monkeypatch)
    page = client.get('/admin/searches').text
    assert '/admin/listings' in page


def test_admin_worker_status_block_handles_missing_file(monkeypatch, tmp_path):
    client, _Session = make_client(monkeypatch)
    status_path = tmp_path / "missing_worker_status.json"
    monkeypatch.setattr(settings, "monitor_worker_status_path", str(status_path))
    monkeypatch.setattr(settings, "monitor_worker_stale_after_seconds", 180)

    page = client.get("/admin/searches").text

    assert "Worker status file" in page
    assert "Missing status file" in page
    assert f"<code>{status_path}</code>" in page
    assert "Age seconds:</strong> —" in page


def test_admin_worker_status_block_renders_crash_retry_counters(monkeypatch, tmp_path):
    client, _Session = make_client(monkeypatch)
    status_path = tmp_path / "worker_status.json"
    status_path.write_text(
        json.dumps(
            {
                "updated_at": "2999-01-01T00:00:00Z",
                "cycle_started_at": "2999-01-01T00:00:00Z",
                "cycle_finished_at": "2999-01-01T00:00:01Z",
                "cycle_ok": True,
                "cycle_error_type": None,
                "cycle_error": "",
                "searches_processed": 3,
                "result_count": 3,
                "selected_first_engine": "camoufox",
                "engine_used": "nodriver",
                "fallback_used": True,
                "browser_driver_crash_count": 2,
                "browser_driver_crash_retry_attempt_count": 2,
                "browser_driver_crash_retry_success_count": 1,
                "close_failure_after_driver_crash_count": 1,
                "engine_error_count": 4,
                "timeout_failure_count": 5,
                "block_detected_count": 6,
                "proxy_failure_count": 7,
                "session_open_count": 8,
                "session_reuse_count": 9,
                "session_evict_count": 10,
                "session_close_failure_count": 11,
                "layout_changed_hint": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "monitor_worker_status_path", str(status_path))
    monkeypatch.setattr(settings, "monitor_worker_stale_after_seconds", 180)

    page = client.get("/admin/searches").text

    assert "Fresh" in page
    assert "Cycle OK" in page
    assert "searches_processed=3" in page
    assert "selected_first_engine=camoufox; engine_used=nodriver" in page
    assert "fallback_used=True" in page
    assert "browser_driver_crash_count=2" in page
    assert "browser_driver_crash_retry_attempt_count=2" in page
    assert "browser_driver_crash_retry_success_count=1" in page
    assert "close_failure_after_driver_crash_count=1" in page
    assert "engine_error_count=4" in page
    assert "timeout_failure_count=5" in page
    assert "block_detected_count=6" in page
    assert "proxy_failure_count=7" in page
    assert "session_open_count=8" in page
    assert "session_reuse_count=9" in page
    assert "session_evict_count=10" in page
    assert "session_close_failure_count=11" in page
    assert "layout_changed_hint=False" in page



def test_listing_analyses_page_renders_table_and_listing_link(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing(
        Session,
        external_id='analysis-ext',
        title='Analysis listing',
        price=123456.0,
        area_m2=42.5,
        address='Analysis street',
        url='https://www.avito.ru/analysis-ext',
    )
    create_listing_analysis(
        Session,
        listing_external_id='analysis-ext',
        search_job_id=7,
        context_key='search:7',
        profile='flat_rent',
        status='success',
        analysis_version='det-flat-rent-v1',
        score=0.82,
        verdict='interesting',
        report_md='## Report\nLooks good',
        facts_json={'rooms': 2},
        risks_json={'risk': 'low'},
        questions_json={'ask': 'documents'},
    )

    page = client.get('/admin/listing-analyses').text

    for heading in (
        '<th>id</th>',
        '<th>search_job_id</th>',
        '<th>context_key</th>',
        '<th>listing_external_id</th>',
        '<th>profile</th>',
        '<th>analysis_version</th>',
        '<th>status</th>',
        '<th>score</th>',
        '<th>verdict</th>',
        '<th>created_at</th>',
        '<th>updated_at</th>',
    ):
        assert heading in page
    assert 'flat_rent' in page
    assert 'success' in page
    assert '0.82' in page
    assert 'interesting' in page
    assert 'Analysis listing' in page
    assert '123456.0' in page
    assert '42.5' in page
    assert 'Analysis street' in page
    assert 'https://www.avito.ru/analysis-ext' in page
    assert "target='_blank'" in page
    assert "rel='noopener noreferrer'" in page


def test_listing_analyses_report_detail_pre_blocks(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(
        Session,
        listing_external_id='detail-ext',
        report_md='### Detailed report\nLine & more',
        facts_json={'price': 100},
        risks_json={'flood': False},
        questions_json={'seller': ['why selling?']},
    )

    page = client.get('/admin/listing-analyses').text

    assert '<details>' in page
    assert '<h4>report_md</h4><pre>### Detailed report' in page
    assert 'Line &amp; more' in page
    assert '<h4>facts</h4><pre>' in page
    assert '&quot;price&quot;: 100' in page
    assert '<h4>risks</h4><pre>' in page
    assert '&quot;flood&quot;: false' in page
    assert '<h4>questions</h4><pre>' in page
    assert 'why selling?' in page


def test_listing_analyses_filter_by_profile(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(Session, listing_external_id='flat-ext', profile='flat_sale', input_hash='hash-flat')
    create_listing_analysis(Session, listing_external_id='rent-ext', profile='flat_rent', input_hash='hash-rent')

    page = client.get('/admin/listing-analyses?profile=flat_sale').text

    assert 'flat-ext' in page
    assert 'flat_sale' in page
    assert 'rent-ext' not in page
    assert "name='profile' value='flat_sale'" in page


def test_listing_analyses_empty_search_job_id_is_ignored(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(Session, listing_external_id='empty-job-10-ext', search_job_id=10, input_hash='hash-empty-10')
    create_listing_analysis(Session, listing_external_id='empty-job-11-ext', search_job_id=11, input_hash='hash-empty-11')

    response = client.get('/admin/listing-analyses?search_job_id=')

    assert response.status_code == 200
    page = response.text
    assert 'empty-job-10-ext' in page
    assert 'empty-job-11-ext' in page
    assert "name='search_job_id' type='number' value=''" in page


def test_listing_analyses_filter_by_search_job_id(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(Session, listing_external_id='job-10-ext', search_job_id=10, input_hash='hash-10')
    create_listing_analysis(Session, listing_external_id='job-11-ext', search_job_id=11, input_hash='hash-11')

    page = client.get('/admin/listing-analyses?search_job_id=10').text

    assert 'job-10-ext' in page
    assert 'job-11-ext' not in page
    assert "name='search_job_id' type='number' value='10'" in page


def test_listing_analyses_invalid_search_job_id_shows_warning_and_ignores_filter(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(Session, listing_external_id='invalid-job-10-ext', search_job_id=10, input_hash='hash-invalid-10')
    create_listing_analysis(Session, listing_external_id='invalid-job-11-ext', search_job_id=11, input_hash='hash-invalid-11')

    response = client.get('/admin/listing-analyses?search_job_id=abc')

    assert response.status_code == 200
    page = response.text
    assert 'Ignored invalid search_job_id filter: abc. Please enter an integer.' in page
    assert 'invalid-job-10-ext' in page
    assert 'invalid-job-11-ext' in page
    assert "name='search_job_id' type='number' value='abc'" in page


def test_listing_analyses_failed_analysis_displays_error(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(
        Session,
        listing_external_id='failed-ext',
        status='failed',
        score=None,
        verdict=None,
        error_type='ProviderError',
        error_message='deterministic provider failed safely',
    )

    page = client.get('/admin/listing-analyses').text

    assert 'failed-ext' in page
    assert 'failed' in page
    assert 'ProviderError' in page
    assert 'deterministic provider failed safely' in page


def test_listing_analyses_page_is_read_only_and_has_no_runtime_side_effects(monkeypatch):
    client, Session = make_client(monkeypatch)
    create_listing_analysis(Session, listing_external_id='safe-ext')

    def fail_if_called(*args, **kwargs):
        raise AssertionError('admin listing analyses page must not start parser, worker, or notifier flows')

    monkeypatch.setattr('app.admin._build_parser', fail_if_called)
    monkeypatch.setattr('app.admin.MonitorService.run_once', fail_if_called)

    page = client.get('/admin/listing-analyses').text

    assert 'safe-ext' in page
    assert "method='post'" not in page
    assert '>delete<' not in page.lower()
    assert '>edit<' not in page.lower()
    assert '>run once<' not in page.lower()
    assert 'does not execute, edit, delete, or re-run analyses' in page


def test_pr19a_operator_dashboard_and_technical_ops_default(monkeypatch):
    client, Session = make_client(monkeypatch, technical_ops_enabled=False, allow_query_api_key=False)
    create_job(Session, name="safe_shell")

    page = client.get("/admin").text
    assert "Панель оператора" in page
    assert "filters_json" not in page
    assert "payload_json" not in page
    assert "input_hash" not in page
    assert "api_key=" not in page

    searches_page = client.get("/admin/searches").text
    assert "safe_shell" in searches_page
    assert "Технические действия выключены" in searches_page
    assert "<button>run once</button>" not in searches_page
    assert "api_key=" not in searches_page

    assert client.get("/admin/searches/new").status_code == 403
    assert client.get("/admin/searches/1/edit").status_code == 403
    assert client.post("/admin/searches").status_code == 403
    assert client.post("/admin/searches/1", data={"name": "safe_shell", "source_url": "https://www.avito.ru/a", "poll_interval_sec": "1"}).status_code == 403
    assert client.post("/admin/searches/1/activate").status_code == 403
    assert client.post("/admin/searches/1/deactivate").status_code == 403
    assert client.post("/admin/searches/1/reset-baseline").status_code == 403
    assert client.post("/admin/searches/1/run-once").status_code == 403


def test_pr19a_query_api_key_disabled_by_default(monkeypatch):
    client, _ = make_client(monkeypatch, allow_query_api_key=False)
    monkeypatch.setattr(settings, "api_key", "secret")
    assert client.get("/admin?api_key=secret").status_code == 403
    assert client.get("/admin", headers={"X-API-Key": "secret"}).status_code == 200


def test_pr19a_redaction_helpers():
    assert redact_admin_value("secret", "telegram_bot_token") == "[redacted]"
    rendered = redact_admin_json({"smtp_password": "secret", "url": "https://script.google.com/macros/s/abc/exec"})
    assert "secret" not in rendered
    assert "https://script.google.com/.../exec" in rendered
