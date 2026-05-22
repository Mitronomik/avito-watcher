from app.core.config import Settings
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
