import json
from argparse import Namespace

import pytest
from sqlalchemy.orm import sessionmaker

from app import cli
from app.parsers.errors import ParserError, ParserErrorType


class DummyParser:
    def __init__(self, stats=None):
        self._stats = stats

    def cycle_stats(self):
        return self._stats


class ServiceSuccess:
    def __init__(self, parser):
        self.parser = parser

    def run_once(self, search_id):
        return {"ok": True, "search_id": search_id, "status": "done"}

    def run_all_searches(self):
        return {"ok": True, "mode": "all"}


class ServiceParserError:
    def __init__(self, parser):
        self.parser = parser

    def run_once(self, _search_id):
        raise ParserError(ParserErrorType.LAYOUT_CHANGED, "markup changed")

    def run_all_searches(self):
        return {"ok": True, "mode": "all"}


class ServiceException:
    def __init__(self, parser):
        self.parser = parser

    def run_once(self, _search_id):
        raise ValueError("boom")

    def run_all_searches(self):
        return {"ok": True, "mode": "all"}


class ServiceKeyboardInterrupt:
    def __init__(self, parser):
        self.parser = parser

    def run_once(self, _search_id):
        raise KeyboardInterrupt()

    def run_all_searches(self):
        return {"ok": True, "mode": "all"}


def _prepare(monkeypatch, parser_stats, service_cls):
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "_build_parser", lambda: DummyParser(parser_stats))
    monkeypatch.setattr(cli, "MonitorService", service_cls)


def test_cmd_run_once_search_success_unchanged(monkeypatch, capsys):
    _prepare(monkeypatch, {"engine_used": "playwright"}, ServiceSuccess)
    monkeypatch.setattr(
        cli,
        "runtime_diagnostics",
        lambda: {
            "alert_channels": ["jsonl"],
            "scoring_enabled": False,
            "scrape_preferred_engine": "camoufox",
            "scrape_allowed_engines": "both",
            "scrape_headless": True,
        },
    )

    cli.cmd_run_once(Namespace(search_id=7))

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": True,
        "search_id": 7,
        "status": "done",
        "runtime": {
            "alert_channels": ["jsonl"],
            "scoring_enabled": False,
            "scrape_preferred_engine": "camoufox",
            "scrape_allowed_engines": "both",
            "scrape_headless": True,
        },
    }


def test_cmd_run_once_search_success_preserves_service_runtime(monkeypatch, capsys):
    _prepare(monkeypatch, {"engine_used": "playwright"}, ServiceSuccess)
    monkeypatch.setattr(
        cli,
        "runtime_diagnostics",
        lambda: {
            "alert_channels": ["jsonl"],
            "scoring_enabled": False,
            "scrape_preferred_engine": "camoufox",
            "scrape_allowed_engines": "both",
            "scrape_headless": True,
        },
    )

    class ServiceSuccessWithRuntime(ServiceSuccess):
        def run_once(self, search_id):
            return {
                "ok": True,
                "search_id": search_id,
                "status": "done",
                "runtime": {"alert_channels": ["service-runtime"]},
            }

    monkeypatch.setattr(cli, "MonitorService", ServiceSuccessWithRuntime)
    cli.cmd_run_once(Namespace(search_id=7))
    output = json.loads(capsys.readouterr().out)
    assert output["runtime"]["alert_channels"] == ["service-runtime"]


def test_cmd_run_once_search_parser_error_returns_structured_json(monkeypatch, capsys):
    _prepare(monkeypatch, {"engine_used": "playwright", "fallback_used": False}, ServiceParserError)

    cli.cmd_run_once(Namespace(search_id=11))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["search_id"] == 11
    assert output["error_type"] == ParserErrorType.LAYOUT_CHANGED.value
    assert output["error"] == "layout_changed: markup changed"
    assert isinstance(output["elapsed_ms"], int)
    assert output["elapsed_ms"] >= 0
    assert output["parser_stats"] == {"engine_used": "playwright", "fallback_used": False}
    assert "runtime" in output


def test_cmd_run_once_search_non_parser_error_returns_structured_json(monkeypatch, capsys):
    _prepare(monkeypatch, {"engine_used": "playwright"}, ServiceException)

    cli.cmd_run_once(Namespace(search_id=13))

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["search_id"] == 13
    assert output["error_type"] == "ValueError"
    assert output["error"] == "boom"
    assert isinstance(output["elapsed_ms"], int)
    assert output["parser_stats"] == {"engine_used": "playwright"}
    assert "runtime" in output


def test_cmd_run_once_search_keyboard_interrupt_not_swallowed(monkeypatch):
    _prepare(monkeypatch, {"engine_used": "playwright"}, ServiceKeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        cli.cmd_run_once(Namespace(search_id=17))


def test_cmd_run_once_without_search_id_uses_run_all(monkeypatch, capsys):
    _prepare(monkeypatch, {"engine_used": "playwright"}, ServiceSuccess)

    cli.cmd_run_once(Namespace(search_id=None))

    output = json.loads(capsys.readouterr().out)
    assert output == {"ok": True, "mode": "all"}


def test_cmd_admin_server_runs_app_instance_without_custom_uvicorn_kwargs(monkeypatch):
    captured = {}

    def fake_run(app, host, port):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.cmd_admin_server(Namespace(host="127.0.0.1", port=8000))

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert captured["app"] is not None
    assert any(route.path == "/admin/searches" for route in captured["app"].routes)


def _search_for_diagnostics(**kwargs):
    return type(
        "Search",
        (),
        {
            "id": kwargs.get("id", 1),
            "name": kwargs.get("name", "search"),
            "is_active": kwargs.get("is_active", True),
            "source_url": kwargs.get("source_url", "https://www.avito.ru/spb/kvartiry/"),
            "filters_json": kwargs.get("filters_json", {}),
        },
    )()


def test_check_analysis_profiles_reports_missing_profile(db_session, monkeypatch, capsys):
    cli.SearchRepository(db_session).create(
        name="missing_profile",
        source_url="https://www.avito.ru/spb/kvartiry/",
        filters_json={},
    )
    db_session.commit()
    SessionLocal = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)

    cli.cmd_check_analysis_profiles(Namespace())

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["searches_total"] == 1
    assert output["searches_without_analysis_profile"] == 1
    assert output["searches"][0]["warning"] == "missing_analysis_profile"


def test_check_analysis_profiles_reports_commercial_rent_without_warning():
    item = cli._search_analysis_profile_diagnostic(
        _search_for_diagnostics(
            source_url="https://www.avito.ru/spb/kommercheskaya_nedvizhimost/",
            filters_json={
                "analysis_profile": "commercial_rent",
                "asset_type": "commercial",
                "deal_type": "rent",
            },
        )
    )

    assert item["analysis_profile"] == "commercial_rent"
    assert item["asset_type"] == "commercial"
    assert item["deal_type"] == "rent"
    assert item["warning"] is None


def test_check_analysis_profiles_reports_unknown_profile_warning():
    item = cli._search_analysis_profile_diagnostic(
        _search_for_diagnostics(filters_json={"analysis_profile": "villa_sale"})
    )

    assert item["analysis_profile"] == "villa_sale"
    assert item["warning"] == "unknown_analysis_profile"


def test_check_analysis_profiles_reports_url_profile_hints():
    commercial = cli._search_analysis_profile_diagnostic(
        _search_for_diagnostics(
            source_url="https://www.avito.ru/spb/kvartiry/",
            filters_json={"analysis_profile": "commercial_rent"},
        )
    )
    flat = cli._search_analysis_profile_diagnostic(
        _search_for_diagnostics(
            source_url="https://www.avito.ru/spb/kommercheskaya_nedvizhimost/",
            filters_json={"analysis_profile": "flat_sale"},
        )
    )

    assert commercial["warning"] == "commercial_profile_on_non_commercial_hint"
    assert flat["warning"] == "flat_profile_on_non_flat_hint"
