from app.core.config import Settings
from app.services.monitor_service import runtime_diagnostics
import pytest


def test_scrape_headless_default_true():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_headless is True


def test_scrape_humanize_default_false():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_humanize is False


def test_scrape_runtime_env_overrides(monkeypatch):
    monkeypatch.setenv("SCRAPE_HEADLESS", "false")
    monkeypatch.setenv("SCRAPE_HUMANIZE", "true")

    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)

    assert settings.scrape_headless is False
    assert settings.scrape_humanize is True


def test_scrape_preferred_engine_default_auto():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_preferred_engine == "auto"


def test_scrape_allowed_engines_default_both():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_allowed_engines == "both"


def test_proxy_quarantine_seconds_default():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.proxy_quarantine_seconds == 7200


def test_timeout_retry_settings_defaults():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_timeout_retry_once is False
    assert settings.scrape_timeout_retry_delay_ms == 300


def test_scoring_enabled_default_true():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scoring_enabled is True

def test_scrape_preferred_engine_invalid_value_fails_clearly(monkeypatch):
    monkeypatch.setenv("SCRAPE_PREFERRED_ENGINE", "bad")
    with pytest.raises(Exception) as exc_info:
        Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert "scrape_preferred_engine" in str(exc_info.value)


def test_pagination_settings_defaults():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_max_pages == 1
    assert settings.scrape_cards_per_page_limit == 30
    assert settings.scrape_stop_on_duplicate_page is True
    assert settings.scrape_page_delay_ms == 0
    assert settings.scrape_page_jitter_ms == 0


def test_scrape_debug_dump_settings_defaults():
    settings = Settings(database_url="sqlite:///tmp.db", _env_file=None)
    assert settings.scrape_debug_dump_html is False
    assert settings.scrape_debug_dump_dir == "./data/debug_html"
    assert settings.scrape_debug_dump_max_bytes == 2_000_000


def test_runtime_diagnostics_includes_scrape_debug_dump_settings(monkeypatch):
    monkeypatch.setattr("app.services.monitor_service.settings.scrape_debug_dump_html", True)
    monkeypatch.setattr("app.services.monitor_service.settings.scrape_debug_dump_dir", "./tmp/debug")
    monkeypatch.setattr("app.services.monitor_service.settings.scrape_debug_dump_max_bytes", 12345)

    runtime = runtime_diagnostics()

    assert runtime["scrape_debug_dump_html"] is True
    assert runtime["scrape_debug_dump_dir"] == "./tmp/debug"
    assert runtime["scrape_debug_dump_max_bytes"] == 12345


def test_runtime_diagnostics_includes_timeout_retry_settings(monkeypatch):
    monkeypatch.setattr("app.services.monitor_service.settings.scrape_timeout_retry_once", True)
    monkeypatch.setattr("app.services.monitor_service.settings.scrape_timeout_retry_delay_ms", 222)
    runtime = runtime_diagnostics()
    assert runtime["scrape_timeout_retry_once"] is True
    assert runtime["scrape_timeout_retry_delay_ms"] == 222
