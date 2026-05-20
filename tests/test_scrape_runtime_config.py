from app.core.config import Settings


def test_scrape_headless_default_true():
    settings = Settings(database_url="sqlite:///tmp.db")
    assert settings.scrape_headless is True


def test_scrape_humanize_default_false():
    settings = Settings(database_url="sqlite:///tmp.db")
    assert settings.scrape_humanize is False


def test_scrape_runtime_env_overrides(monkeypatch):
    monkeypatch.setenv("SCRAPE_HEADLESS", "false")
    monkeypatch.setenv("SCRAPE_HUMANIZE", "true")

    settings = Settings(database_url="sqlite:///tmp.db")

    assert settings.scrape_headless is False
    assert settings.scrape_humanize is True
