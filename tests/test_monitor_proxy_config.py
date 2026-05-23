from app.workers import monitor


def test_build_parser_uses_configured_proxy_quarantine_seconds(monkeypatch):
    monkeypatch.setattr(
        "app.workers.monitor.settings.proxy_urls",
        "http://a:b@1.1.1.1:8000",
    )
    monkeypatch.setattr("app.workers.monitor.settings.proxy_quarantine_seconds", 33)

    parser = monitor._build_parser()

    assert parser._proxy_manager is not None
    assert parser._proxy_manager.stats()["quarantine_seconds"] == 33
