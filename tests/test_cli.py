import json
from argparse import Namespace

import pytest

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

    cli.cmd_run_once(Namespace(search_id=7))

    output = json.loads(capsys.readouterr().out)
    assert output == {"ok": True, "search_id": 7, "status": "done"}


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
