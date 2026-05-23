import asyncio
import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bs4 import BeautifulSoup

from app import cli
from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot
from app.parsers.avito_parser import AvitoParser, _Engine
from app.parsers.errors import ParserError, ParserErrorType
from app.parsers.schemas import ListingCard
from app.services.monitor_service import MonitorService
from tests.test_baseline_monitor import (
    FakeNotifier,
    FakeParser,
    FakeScorer,
    make_search,
    patch_session_local,
    scalar_count,
)


@pytest.fixture(autouse=True)
def isolate_parser_debug_dump_settings(tmp_path, monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_html", False)
    monkeypatch.setattr(
        "app.parsers.avito_parser.settings.scrape_debug_dump_dir",
        str(tmp_path / "debug_html"),
    )
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_max_bytes", 2_000_000)


def make_test_parser(**kwargs):
    parser = AvitoParser(preferred_engine=kwargs.pop("preferred_engine", "auto"), **kwargs)
    parser._allowed_engines_mode = "both"
    return parser


def test_fetch_search_cards_rejects_url_without_http_scheme():
    with pytest.raises(ParserError) as exc_info:
        asyncio.run(AvitoParser().fetch_search_cards("www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.INVALID_URL
    assert "http:// or https://" in str(exc_info.value)


def test_fetch_search_cards_rejects_non_avito_host():
    with pytest.raises(ParserError) as exc_info:
        asyncio.run(AvitoParser().fetch_search_cards("https://example.com/search"))

    assert exc_info.value.error_type == ParserErrorType.INVALID_URL
    assert "avito.ru" in str(exc_info.value)


def test_parser_detects_block_markers_without_listing_page_navigation():
    assert AvitoParser._looks_like_captcha_or_block(
        "Проверка безопасности", "Подтвердите, что вы не робот"
    )


def test_parser_detects_empty_results_text_without_listing_page_navigation():
    assert AvitoParser._looks_like_empty_results("Ничего не найдено по вашему запросу")


def test_fetch_search_cards_raises_possible_captcha_or_block_from_captcha_html():
    parser = AvitoParser()
    captcha_html = """
    <html>
      <head><title>Проверка безопасности</title></head>
      <body>Подтвердите, что вы не робот</body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=captcha_html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK


def test_fetch_search_cards_raises_empty_results_from_empty_results_html():
    parser = AvitoParser()
    empty_results_html = """
    <html>
      <head><title>Avito</title></head>
      <body>Ничего не найдено. Попробуйте изменить параметры поиска.</body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=empty_results_html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.EMPTY_RESULTS


def test_fetch_search_cards_raises_layout_changed_from_valid_html_without_item_cards():
    parser = AvitoParser()
    no_cards_html = """
    <html>
      <head><title>Avito</title></head>
      <body><main><section>Свежие объявления рядом с вами</section></main></body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=no_cards_html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED


def test_layout_changed_debug_dump_disabled_creates_no_files(tmp_path, monkeypatch):
    parser = AvitoParser()
    no_cards_html = "<html><head><title>Avito</title></head><body><main>empty</main></body></html>"
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_html", False)
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_dir", str(tmp_path))

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=no_cards_html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry?p=3"))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED
    assert list(tmp_path.iterdir()) == []


def test_layout_changed_debug_dump_enabled_creates_html_and_json(tmp_path, monkeypatch):
    parser = AvitoParser()
    html = (
        "<html><head><title>Avito test</title></head>"
        '<body><script>window.__initialData__={}</script><div data-marker="item-title"></div>'
        '<div data-marker="item-view/item-date"></div><main>no cards</main></body></html>'
    )
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_html", True)
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_dir", str(tmp_path))
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_max_bytes", 2_000_000)

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry?p=3"))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED
    html_files = list(tmp_path.glob("*.html"))
    meta_files = list(tmp_path.glob("*.json"))
    assert len(html_files) == 1
    assert len(meta_files) == 1
    assert html_files[0].read_text(encoding="utf-8") == html

    metadata = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert metadata["error_type"] == ParserErrorType.LAYOUT_CHANGED.value
    assert metadata["url_preview"] == "https://www.avito.ru/moskva/kvartiry?p=3"
    assert metadata["page"] == 3
    assert metadata["html_length"] == len(html)
    assert metadata["html_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest()
    assert metadata["title"] == "Avito test"
    assert metadata["has_data_marker_item"] is False
    assert metadata["has_item_title"] is True
    assert metadata["has_item_view"] is True
    assert metadata["has_hydration_or_initial_data"] is True
    assert metadata["looks_like_block_or_captcha"] is False
    assert metadata["empty_results_detected"] is False
    assert metadata["dump_html_path"].endswith(".html")
    assert metadata["dump_meta_path"].endswith(".json")
    assert html_files[0].exists()
    assert meta_files[0].exists()


def test_layout_changed_debug_dump_write_failure_still_raises_layout_changed(tmp_path, monkeypatch):
    parser = AvitoParser()
    html = "<html><head><title>Avito</title></head><body>no cards</body></html>"
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_html", True)
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_debug_dump_dir", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.mkdir", Mock(side_effect=RuntimeError("disk full")))

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED


def test_layout_changed_local_env_debug_dump_enabled_does_not_write_to_default_data_dir(
    tmp_path, monkeypatch
):
    parser = AvitoParser()
    html = "<html><head><title>Avito</title></head><body>no cards</body></html>"
    debug_dir = tmp_path / "data" / "debug_html"
    debug_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRAPE_DEBUG_DUMP_HTML", "true")
    monkeypatch.setenv("SCRAPE_DEBUG_DUMP_DIR", "./data/debug_html")

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry?p=7"))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED
    assert list(debug_dir.iterdir()) == []


def test_fetch_page_html_raises_possible_captcha_or_block_when_all_engines_fail():
    parser = AvitoParser()
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "possible_captcha_or_block", "html": ""},
            {"ok": False, "error_type": "possible_captcha_or_block", "html": ""},
        ]
    )

    with patch.object(parser, "_try_engine", new=try_engine):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(
                parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry")
            )

    assert exc_info.value.error_type == ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK
    assert try_engine.await_count == 2


def test_fetch_page_html_raises_proxy_unavailable_when_all_proxies_quarantined():
    parser = AvitoParser()

    class EmptyProxyManager:
        def get_proxy(self):
            return None

    parser._proxy_manager = EmptyProxyManager()
    try_engine = AsyncMock()

    with patch.object(parser, "_try_engine", new=try_engine):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.PROXY_UNAVAILABLE
    assert str(exc_info.value) == (
        "proxy_unavailable: No available proxies: all configured proxies are quarantined"
    )
    assert try_engine.await_count == 0


def test_parser_preserves_sha256_external_id_fallback():
    external_id = AvitoParser._extract_external_id("/moskva/kvartiry/custom-slug", 0)

    assert external_id.startswith("fallback-")
    assert len(external_id) == len("fallback-") + 16


def test_extract_external_id_success_case():
    assert (
        AvitoParser._extract_external_id("/moskva/kvartiry/komnata_987654321", 0)
        == "987654321"
    )


def test_extract_external_id_with_query_string():
    href = "/moskva/kvartiry/komnata_111222333?context=popup"

    assert AvitoParser._extract_external_id(href, 0) == "111222333"


def test_fetch_page_html_nodriver_blocked_camoufox_succeeds_engine_flip():
    parser = make_test_parser(preferred_engine="auto")
    html = "<html><body>ok</body></html>"
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "blocked", "html": ""},
            {"ok": True, "html": html},
        ]
    )

    with patch.object(parser, "_try_engine", new=try_engine):
        result = asyncio.run(
            parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry")
        )

    assert result == html
    assert parser._prefer_engine == _Engine.CAMOUFOX


def test_fetch_page_html_both_engines_blocked_raises():
    parser = AvitoParser()
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "blocked", "html": ""},
            {"ok": False, "error_type": "blocked", "html": ""},
        ]
    )

    with patch.object(parser, "_try_engine", new=try_engine):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(
                parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry")
            )

    assert exc_info.value.error_type == ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK




def test_fetch_page_html_cycle_mode_nodriver_session_open_failure_falls_back(monkeypatch):
    parser = make_test_parser(preferred_engine="auto")
    parser._cycle_active = True

    async def fail_open(_proxy):
        raise RuntimeError("nodriver open failed")

    async def ok_open(_proxy):
        class S:
            async def fetch(self, _url):
                return {"ok": True, "html": "<html></html>"}

            async def close(self):
                return None

        return S()

    monkeypatch.setattr("app.parsers.avito_parser.open_nodriver_session", fail_open)
    monkeypatch.setattr("app.parsers.avito_parser.open_camoufox_session", ok_open)

    html = asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert html == "<html></html>"
    assert parser._prefer_engine == _Engine.CAMOUFOX


def test_fetch_page_html_cycle_mode_evicts_broken_cached_session_and_falls_back(caplog):
    parser = make_test_parser(preferred_engine="auto")
    parser._cycle_active = True

    class BrokenSession:
        async def fetch(self, _url):
            return {"ok": False, "error_type": "exception", "error": "boom"}

        async def close(self):
            raise RuntimeError("close failed")

    class GoodSession:
        async def fetch(self, _url):
            return {"ok": True, "html": "<html>ok</html>"}

        async def close(self):
            return None

    parser._engine_sessions[(_Engine.NODRIVER, None)] = BrokenSession()
    parser._engine_sessions[(_Engine.CAMOUFOX, None)] = GoodSession()

    html = asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    assert html == "<html>ok</html>"
    assert parser._prefer_engine == _Engine.CAMOUFOX
    assert (_Engine.NODRIVER, None) not in parser._engine_sessions
    assert "failed to close browser session" in caplog.text


def test_ensure_engine_session_can_open_fresh_session_after_eviction(monkeypatch):
    parser = AvitoParser()
    parser._cycle_active = True

    class BrokenSession:
        async def fetch(self, _url):
            return {"ok": False, "error_type": "exception", "error": "boom"}

        async def close(self):
            return None

    class OpenedSession:
        async def fetch(self, _url):
            return {"ok": True, "html": "<html>fresh</html>"}

        async def close(self):
            return None

    parser._engine_sessions[(_Engine.NODRIVER, None)] = BrokenSession()

    opened = []

    async def open_nodriver(_proxy):
        opened.append(True)
        return OpenedSession()

    monkeypatch.setattr("app.parsers.avito_parser.open_nodriver_session", open_nodriver)

    result = asyncio.run(parser._try_engine("https://www.avito.ru/moskva/kvartiry", None, _Engine.NODRIVER))
    assert result["ok"] is False
    assert (_Engine.NODRIVER, None) not in parser._engine_sessions

    setup_error = asyncio.run(parser.ensure_engine_session(_Engine.NODRIVER, None))
    assert setup_error is None
    assert opened == [True]
    assert (_Engine.NODRIVER, None) in parser._engine_sessions


def test_session_reuse_and_open_counters_increment(monkeypatch):
    parser = AvitoParser()
    parser._cycle_active = True

    class OpenedSession:
        async def fetch(self, _url):
            return {"ok": True, "html": "<html>fresh</html>"}

        async def close(self):
            return None

    async def open_nodriver(_proxy):
        return OpenedSession()

    monkeypatch.setattr("app.parsers.avito_parser.open_nodriver_session", open_nodriver)

    asyncio.run(parser.ensure_engine_session(_Engine.NODRIVER, None))
    asyncio.run(parser._try_engine("https://www.avito.ru/moskva/kvartiry", None, _Engine.NODRIVER))
    stats = parser.cycle_stats()
    assert stats["session_open_count"] == 1
    assert stats["session_reuse_count"] == 1


def test_engine_exception_counts_as_engine_error_not_block():
    parser = AvitoParser()
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "exception", "error": "warmup failed"},
            {"ok": True, "html": "<html>ok</html>"},
        ]
    )

    with patch.object(parser, "_try_engine", new=try_engine):
        asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    stats = parser.cycle_stats()
    assert stats["engine_fallback_count"] == 1
    assert stats["engine_error_count"] == 1
    assert stats["block_detected_count"] == 0


def test_engine_block_counts_as_block_not_engine_error():
    parser = AvitoParser()
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "possible_captcha_or_block", "error": "blocked"},
            {"ok": True, "html": "<html>ok</html>"},
        ]
    )

    with patch.object(parser, "_try_engine", new=try_engine):
        asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    stats = parser.cycle_stats()
    assert stats["engine_fallback_count"] == 1
    assert stats["block_detected_count"] == 1
    assert stats["engine_error_count"] == 0


def test_auto_preserves_order_and_health_memory_skip_behavior():
    parser = AvitoParser(preferred_engine="auto")
    assert parser._choose_start_engine(None) == _Engine.NODRIVER

    parser._engine_recent_failures[(_Engine.NODRIVER, "no_proxy")] = 1
    assert parser._choose_start_engine(None) == _Engine.CAMOUFOX
    stats = parser.cycle_stats()
    assert stats["preferred_engine"] == "auto"
    assert stats["engine_selection_changed_by_health_memory"] is True


def test_nodriver_preferred_starts_nodriver_when_healthy():
    parser = AvitoParser(preferred_engine="nodriver")
    assert parser._choose_start_engine(None) == _Engine.NODRIVER


def test_camoufox_preferred_starts_camoufox():
    parser = AvitoParser(preferred_engine="camoufox")
    assert parser._choose_start_engine(None) == _Engine.CAMOUFOX


def test_fallback_happens_after_preferred_engine_failure_camoufox_first():
    parser = AvitoParser(preferred_engine="camoufox")
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "timeout", "error": "t"},
            {"ok": True, "html": "<html>ok</html>"},
        ]
    )
    with patch.object(parser, "_try_engine", new=try_engine):
        html = asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert html == "<html>ok</html>"
    first_engine = try_engine.await_args_list[0].args[2]
    second_engine = try_engine.await_args_list[1].args[2]
    assert first_engine == _Engine.CAMOUFOX
    assert second_engine == _Engine.NODRIVER
    assert parser.cycle_stats()["fallback_used"] is True


def test_health_memory_still_affects_nodriver_mode():
    parser = AvitoParser(preferred_engine="nodriver")
    parser._engine_recent_failures[(_Engine.NODRIVER, "no_proxy")] = 2
    assert parser._choose_start_engine(None) == _Engine.CAMOUFOX


def test_allowed_engines_camoufox_disables_nodriver_fallback(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_allowed_engines", "camoufox")
    parser = AvitoParser(preferred_engine="camoufox")
    try_engine = AsyncMock(return_value={"ok": False, "error_type": "possible_captcha_or_block", "error": "blocked"})
    with patch.object(parser, "_try_engine", new=try_engine):
        with pytest.raises(ParserError):
            asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert len(try_engine.await_args_list) == 1
    assert try_engine.await_args_list[0].args[2] == _Engine.CAMOUFOX
    assert parser.cycle_stats()["fallback_used"] is False
    assert parser.cycle_stats()["engine_fallback_count"] == 0


def test_camoufox_only_failure_log_does_not_claim_switching(monkeypatch, caplog):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_allowed_engines", "camoufox")
    parser = AvitoParser(preferred_engine="camoufox")
    try_engine = AsyncMock(return_value={"ok": False, "error_type": "timeout", "error": "t"})
    caplog.set_level("WARNING")
    with patch.object(parser, "_try_engine", new=try_engine):
        with pytest.raises(ParserError):
            asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert "switching engine" not in caplog.text
    assert "fallback_available=False" in caplog.text
    assert "allowed_engines=camoufox" in caplog.text


def test_both_engines_failure_log_mentions_fallback_available(monkeypatch, caplog):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_allowed_engines", "both")
    parser = AvitoParser(preferred_engine="camoufox")
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "timeout", "error": "t"},
            {"ok": True, "html": "<html>ok</html>"},
        ]
    )
    caplog.set_level("WARNING")
    with patch.object(parser, "_try_engine", new=try_engine):
        asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert "fallback_available=True" in caplog.text
    assert "allowed_engines=nodriver,camoufox" in caplog.text or "allowed_engines=camoufox,nodriver" in caplog.text
    assert parser.cycle_stats()["fallback_used"] is True
    assert parser.cycle_stats()["engine_fallback_count"] == 1


def test_allowed_engines_nodriver_disables_camoufox_fallback(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_allowed_engines", "nodriver")
    parser = AvitoParser(preferred_engine="nodriver")
    try_engine = AsyncMock(return_value={"ok": False, "error_type": "timeout", "error": "t"})
    with patch.object(parser, "_try_engine", new=try_engine):
        with pytest.raises(ParserError):
            asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert len(try_engine.await_args_list) == 1
    assert try_engine.await_args_list[0].args[2] == _Engine.NODRIVER
    assert parser.cycle_stats()["fallback_used"] is False
    assert parser.cycle_stats()["engine_fallback_count"] == 0


def test_preferred_engine_outside_allowed_set_falls_back_to_first_allowed(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_allowed_engines", "nodriver")
    parser = AvitoParser(preferred_engine="camoufox")
    assert parser._choose_start_engine(None) == _Engine.NODRIVER
    assert parser.cycle_stats()["preferred_engine"] == "camoufox"
    assert parser.cycle_stats()["selected_first_engine"] == "nodriver"


def test_proxy_failure_counter_tracks_reported_failures_without_quarantine_counter():
    parser = AvitoParser()
    try_engine = AsyncMock(
        side_effect=[
            {"ok": False, "error_type": "possible_captcha_or_block", "error": "blocked"},
            {"ok": True, "html": "<html>ok</html>"},
        ]
    )
    report_failure = Mock()

    class FakeProxyManager:
        def __init__(self):
            self.calls = 0

        def get_proxy(self):
            self.calls += 1
            return "http://proxy-1" if self.calls == 1 else "http://proxy-2"

        def report_failure(self, _proxy):
            return report_failure(_proxy)

        def report_success(self, _proxy):
            return None

    parser._proxy_manager = FakeProxyManager()
    with patch.object(parser, "_try_engine", new=try_engine):
        asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    stats = parser.cycle_stats()
    assert stats["proxy_failure_count"] == 1
    assert "proxy_quarantine_count" not in stats


def test_fallback_and_eviction_counters_increment():
    parser = make_test_parser(preferred_engine="auto")
    parser._cycle_active = True

    class BrokenSession:
        async def fetch(self, _url):
            return {"ok": False, "error_type": "exception", "error": "boom"}

        async def close(self):
            return None

    class GoodSession:
        async def fetch(self, _url):
            return {"ok": True, "html": "<html>ok</html>"}

        async def close(self):
            return None

    parser._engine_sessions[(_Engine.NODRIVER, None)] = BrokenSession()
    parser._engine_sessions[(_Engine.CAMOUFOX, None)] = GoodSession()

    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    stats = parser.cycle_stats()
    assert stats["engine_fallback_count"] == 1
    assert stats["session_evict_count"] == 1

def test_monitor_records_parser_error_type_in_last_error(db_session):
    search = make_search(db_session)
    service = MonitorService(
        parser=FakeParser(
            [
                ParserError(
                    ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
                    "Search page content looks blocked",
                )
            ]
        ),
        scorer=FakeScorer(),
        notifier=FakeNotifier(),
    )

    with pytest.raises(ParserError):
        asyncio.run(service.process_search(db_session, search))

    db_session.refresh(search)
    assert search.fail_count == 1
    assert search.baseline_initialized is False
    assert search.last_error.startswith("possible_captcha_or_block:")


def test_dry_run_search_prints_card_diagnostics_without_side_effects(
    monkeypatch, capsys, db_session
):
    class FakeDryRunParser:
        async def fetch_search_cards(self, url):
            assert url == "https://www.avito.ru/test"
            return [
                ListingCard(
                    external_id=str(idx),
                    title=f"Listing {idx}",
                    price=float(idx),
                    url=f"https://www.avito.ru/item_{idx}",
                    published_label="Сегодня 12:34",
                    published_at=datetime(2026, 5, 17, 9, 34, 0),
                )
                for idx in range(6)
            ]

    monkeypatch.setattr(cli, "_build_parser", lambda: FakeDryRunParser())

    parser = cli.build_parser()
    args = parser.parse_args(["dry-run-search", "--url", "https://www.avito.ru/test"])
    args.func(args)

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["total_cards"] == 6
    assert len(output["cards"]) == 5
    assert output["cards"][0] == {
        "external_id": "0",
        "title": "Listing 0",
        "price": 0.0,
        "url": "https://www.avito.ru/item_0",
        "published_label": "Сегодня 12:34",
        "published_at": "2026-05-17T09:34:00",
    }
    assert scalar_count(db_session, Listing) == 0
    assert scalar_count(db_session, ListingSnapshot) == 0
    assert scalar_count(db_session, AlertSent) == 0


def test_dry_run_search_reports_classified_parser_error(monkeypatch, capsys):
    class FakeDryRunParser:
        async def fetch_search_cards(self, url):
            assert url == "bad-url"
            raise ParserError(ParserErrorType.INVALID_URL, "search_url must start with http:// or https://")

    monkeypatch.setattr(cli, "_build_parser", lambda: FakeDryRunParser())

    parser = cli.build_parser()
    args = parser.parse_args(["dry-run-search", "--url", "bad-url"])
    args.func(args)

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["total_cards"] == 0
    assert output["cards"] == []
    assert output["error_type"] == "invalid_url"
    assert output["error"].startswith("invalid_url:")


def test_extract_area_m2_from_russian_card_text_variants():
    assert AvitoParser._extract_area_m2("1-к. квартира, 32 м², 5/9 эт.") == 32.0
    assert AvitoParser._extract_area_m2("Квартира 32,5 м² рядом с метро") == 32.5
    assert AvitoParser._extract_area_m2("Апартаменты 32.5 м²") == 32.5
    assert AvitoParser._extract_area_m2("Коммерческое помещение 32 кв. м") == 32.0


def test_extract_rooms_from_russian_card_text_variants():
    assert AvitoParser._extract_rooms("1-к. квартира, 32 м²") == "1-к."
    assert AvitoParser._extract_rooms("2-к. квартира, 45 м²") == "2-к."
    assert AvitoParser._extract_rooms("3-к. квартира, 68 м²") == "3-к."
    assert AvitoParser._extract_rooms("4-к. квартира, 90 м²") == "4-к."
    assert AvitoParser._extract_rooms("Студия, 25 м²") == "студия"
    assert AvitoParser._extract_rooms("Апартаменты, 40 м²") == "апартаменты"


def test_extract_address_from_conservative_russian_card_text():
    text = """
    2-к. квартира, 45 м², 7/12 эт.
    12 500 000 ₽
    Москва, ул. Ленина, 10
    Вчера 18:20
    """

    assert AvitoParser._extract_address_from_text(text) == "Москва, ул. Ленина, 10"


def test_extract_address_keeps_raw_card_text_unchanged():
    text = "1-к. квартира, 32 м²\nСанкт-Петербург, Невский проспект, 5\n"

    AvitoParser._extract_address_from_text(text)

    assert text == "1-к. квартира, 32 м²\nСанкт-Петербург, Невский проспект, 5\n"


def test_extract_structured_address_prefers_card_marker():
    soup = BeautifulSoup(
        """
        <div data-marker="item">
          <span data-marker="item-address">  Казань, ул. Баумана, 7  </span>
        </div>
        """,
        "lxml",
    )
    tag = soup.select_one('[data-marker="item"]')

    result = AvitoParser._extract_structured_address_bs(tag)

    assert result == "Казань, ул. Баумана, 7"


def test_parse_published_at_russian_labels():
    now = datetime(2026, 5, 17, 15, 0, 0, tzinfo=UTC)  # 18:00 Europe/Moscow.

    cases = {
        "Сегодня 12:34": datetime(2026, 5, 17, 9, 34, 0),
        "сегодня в 12:34": datetime(2026, 5, 17, 9, 34, 0),
        "Вчера 09:10": datetime(2026, 5, 16, 6, 10, 0),
        "вчера в 09:10": datetime(2026, 5, 16, 6, 10, 0),
        "2 часа назад": datetime(2026, 5, 17, 13, 0, 0),
        "1 час назад": datetime(2026, 5, 17, 14, 0, 0),
        "30 минут назад": datetime(2026, 5, 17, 14, 30, 0),
        "1 минуту назад": datetime(2026, 5, 17, 14, 59, 0),
        # "17 мая" has no time → midnight Moscow (00:00 MSK) → UTC 21:00 of prev day.
        "17 мая": datetime(2026, 5, 16, 21, 0, 0),
        "17 мая 14:20": datetime(2026, 5, 17, 11, 20, 0),
        "17 мая в 14:20": datetime(2026, 5, 17, 11, 20, 0),
    }

    for label, expected in cases.items():
        assert AvitoParser._parse_published_at(label, now) == expected


def test_extract_published_label_keeps_raw_text_unchanged():
    text = "1-к. квартира\nСегодня 12:34\n"

    assert AvitoParser._extract_published_label(text) == "Сегодня 12:34"
    assert text == "1-к. квартира\nСегодня 12:34\n"


def test_extract_structured_published_label_prefers_marker_over_fallback_text():
    soup = BeautifulSoup(
        """
        <div data-marker="item">
          <span data-marker="item-date">Вчера 09:10</span>
          <span>Сегодня 12:34</span>
        </div>
        """,
        "lxml",
    )
    tag = soup.select_one('[data-marker="item"]')

    result = AvitoParser._extract_structured_published_label_bs(tag)

    assert result == "Вчера 09:10"


def test_unknown_published_label_returns_none_without_exception():
    assert AvitoParser._extract_published_label("Адрес рядом с метро") == ""
    assert AvitoParser._parse_published_at("непонятная дата", datetime(2026, 5, 17)) is None


# Missing-card classification is now handled inline by fetch_search_cards after
# BeautifulSoup parses fetched HTML, so these tests exercise that real code path
# instead of the removed Playwright page helper.
def test_missing_cards_flow_detects_possible_captcha_or_block():
    parser = AvitoParser()
    html = """
    <html>
      <head><title>Доступ ограничен</title></head>
      <body>Подтвердите, что вы не робот</body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK


def test_missing_cards_flow_detects_empty_results():
    parser = AvitoParser()
    html = """
    <html>
      <head><title>Avito</title></head>
      <body>Ничего не найдено. Попробуйте изменить параметры поиска</body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.EMPTY_RESULTS


def test_missing_cards_flow_defaults_to_layout_changed():
    parser = AvitoParser()
    html = """
    <html>
      <head><title>Avito</title></head>
      <body>Unexpected page without cards</body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards("https://www.avito.ru/moskva/kvartiry"))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED

def test_run_all_searches_wraps_parser_cycle_hooks_on_exception(monkeypatch, db_session):
    class ParserWithCycle:
        def __init__(self):
            self.begin_calls = 0
            self.end_calls = 0

        async def begin_cycle(self):
            self.begin_calls += 1

        async def end_cycle(self):
            self.end_calls += 1

        async def fetch_search_cards(self, _search_url):
            raise RuntimeError("boom")

    parser = ParserWithCycle()
    service = MonitorService(parser=parser, scorer=FakeScorer(), notifier=FakeNotifier())
    make_search(db_session, name="boom")
    patch_session_local(monkeypatch, db_session)

    results = service.run_all_searches()

    assert len(results) == 1
    assert parser.begin_calls == 1
    assert parser.end_calls == 1


def test_run_all_searches_without_cycle_hooks(monkeypatch, db_session):
    class ParserWithoutCycle:
        async def fetch_search_cards(self, _search_url):
            return []

    parser = ParserWithoutCycle()
    service = MonitorService(parser=parser, scorer=FakeScorer(), notifier=FakeNotifier())
    make_search(db_session, name="no-hooks")
    patch_session_local(monkeypatch, db_session)

    results = service.run_all_searches()
    assert len(results) == 1


def test_end_cycle_closes_all_sessions_even_if_one_fails(caplog):
    parser = AvitoParser()

    class BadSession:
        async def fetch(self, _url):
            return {"ok": True, "html": ""}

        async def close(self):
            raise RuntimeError("close failed")

    closed = []

    class GoodSession:
        async def fetch(self, _url):
            return {"ok": True, "html": ""}

        async def close(self):
            closed.append(True)

    parser._engine_sessions[(_Engine.NODRIVER, None)] = BadSession()
    parser._engine_sessions[(_Engine.CAMOUFOX, None)] = GoodSession()

    asyncio.run(parser.end_cycle())

    assert closed == [True]
    assert "failed to close browser session" in caplog.text


def test_end_cycle_logs_summary(caplog):
    parser = AvitoParser()
    caplog.set_level("INFO")

    parser._cycle_counters.session_open_count = 2
    parser._cycle_counters.session_reuse_count = 3
    parser._cycle_counters.engine_fallback_count = 1

    asyncio.run(parser.end_cycle())

    assert "avito_parser.end_cycle stats=" in caplog.text


def test_fetch_page_html_falls_back_when_nodriver_returns_timeout(monkeypatch):
    parser = make_test_parser(preferred_engine="auto")
    parser._prefer_engine = _Engine.NODRIVER

    responses = iter([
        {"ok": False, "error_type": "timeout", "error": "warmup timeout"},
        {"ok": True, "html": "<html>ok</html>"},
    ])

    async def fake_try_engine(url, proxy_url, engine):
        return next(responses)

    async def no_setup(*_args, **_kwargs):
        return None

    monkeypatch.setattr(parser, "_try_engine", fake_try_engine)
    monkeypatch.setattr(parser, "ensure_engine_session", no_setup)

    html = asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    assert html == "<html>ok</html>"
    assert parser._prefer_engine == _Engine.CAMOUFOX


def test_second_fetch_same_proxy_skips_nodriver_after_timeout(monkeypatch):
    parser = make_test_parser(preferred_engine="auto")
    parser._prefer_engine = _Engine.NODRIVER
    proxy_url = "http://user:pass@1.2.3.4:8080"
    calls: list[_Engine] = []

    async def fake_try_engine(_url, _proxy_url, engine):
        calls.append(engine)
        if calls == [_Engine.NODRIVER]:
            return {"ok": False, "error_type": "timeout", "error": "warmup timeout"}
        return {"ok": True, "html": "<html>ok</html>"}

    async def no_setup(*_args, **_kwargs):
        return None

    class FakeProxyManager:
        def get_proxy(self):
            return proxy_url

        def report_failure(self, _proxy_url):
            return None

        def report_success(self, _proxy_url):
            return None

    parser._proxy_manager = FakeProxyManager()
    monkeypatch.setattr(parser, "_try_engine", fake_try_engine)
    monkeypatch.setattr(parser, "ensure_engine_session", no_setup)

    first = asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    second = asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    assert first == "<html>ok</html>"
    assert second == "<html>ok</html>"
    assert calls == [_Engine.NODRIVER, _Engine.CAMOUFOX, _Engine.CAMOUFOX]


def test_recent_nodriver_failure_not_inherited_by_other_proxy(monkeypatch):
    parser = make_test_parser(preferred_engine="auto")
    parser._prefer_engine = _Engine.NODRIVER
    calls: list[tuple[str | None, _Engine]] = []
    proxy_sequence = iter(
        [
            "http://user:pass@1.2.3.4:8080",
            "http://user:pass@1.2.3.4:8080",
            "http://user:pass@5.6.7.8:8080",
        ]
    )

    async def fake_try_engine(_url, proxy_url, engine):
        calls.append((proxy_url, engine))
        if proxy_url == "http://user:pass@1.2.3.4:8080" and engine == _Engine.NODRIVER:
            return {"ok": False, "error_type": "timeout", "error": "warmup timeout"}
        return {"ok": True, "html": "<html>ok</html>"}

    async def no_setup(*_args, **_kwargs):
        return None

    class FakeProxyManager:
        def get_proxy(self):
            return next(proxy_sequence)

        def report_failure(self, _proxy_url):
            return None

        def report_success(self, _proxy_url):
            return None

    parser._proxy_manager = FakeProxyManager()
    monkeypatch.setattr(parser, "_try_engine", fake_try_engine)
    monkeypatch.setattr(parser, "ensure_engine_session", no_setup)

    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    parser._prefer_engine = _Engine.NODRIVER
    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    assert calls[0] == ("http://user:pass@1.2.3.4:8080", _Engine.NODRIVER)
    assert calls[2] == ("http://user:pass@5.6.7.8:8080", _Engine.NODRIVER)


def test_no_proxy_does_not_inherit_proxy_nodriver_failure(monkeypatch):
    parser = make_test_parser(preferred_engine="auto")
    parser._prefer_engine = _Engine.NODRIVER
    calls: list[tuple[str | None, _Engine]] = []
    proxy_sequence = iter(["http://user:pass@1.2.3.4:8080", "http://user:pass@1.2.3.4:8080"])

    async def fake_try_engine(_url, proxy_url, engine):
        calls.append((proxy_url, engine))
        if proxy_url == "http://user:pass@1.2.3.4:8080" and engine == _Engine.NODRIVER:
            return {"ok": False, "error_type": "timeout", "error": "warmup timeout"}
        return {"ok": True, "html": "<html>ok</html>"}

    async def no_setup(*_args, **_kwargs):
        return None

    class FakeProxyManager:
        def get_proxy(self):
            return next(proxy_sequence)

        def report_failure(self, _proxy_url):
            return None

        def report_success(self, _proxy_url):
            return None

    parser._proxy_manager = FakeProxyManager()
    monkeypatch.setattr(parser, "_try_engine", fake_try_engine)
    monkeypatch.setattr(parser, "ensure_engine_session", no_setup)

    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    parser._prefer_engine = _Engine.NODRIVER
    parser._proxy_manager = None
    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    assert calls[0] == ("http://user:pass@1.2.3.4:8080", _Engine.NODRIVER)
    assert calls[2] == (None, _Engine.NODRIVER)


def test_successful_nodriver_does_not_mark_recent_failure(monkeypatch):
    parser = make_test_parser(preferred_engine="auto")
    parser._prefer_engine = _Engine.NODRIVER
    calls: list[_Engine] = []

    async def fake_try_engine(_url, _proxy_url, engine):
        calls.append(engine)
        return {"ok": True, "html": "<html>ok</html>"}

    async def no_setup(*_args, **_kwargs):
        return None

    monkeypatch.setattr(parser, "_try_engine", fake_try_engine)
    monkeypatch.setattr(parser, "ensure_engine_session", no_setup)

    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))
    asyncio.run(parser._fetch_page_html("https://www.avito.ru/moskva/kvartiry"))

    assert calls == [_Engine.NODRIVER, _Engine.NODRIVER]


def test_fetch_page_html_cycle_mode_evicts_cached_session_on_timeout():
    parser = AvitoParser()
    parser._cycle_active = True

    class TimeoutSession:
        async def fetch(self, _url):
            return {"ok": False, "error_type": "timeout", "error": "stuck"}

        async def close(self):
            return None

    parser._engine_sessions[(_Engine.NODRIVER, None)] = TimeoutSession()

    result = asyncio.run(parser._try_engine("https://www.avito.ru/moskva/kvartiry", None, _Engine.NODRIVER))
    assert result["error_type"] == "timeout"
    assert (_Engine.NODRIVER, None) not in parser._engine_sessions


def test_extract_item_page_publication_label_from_marker():
    html = '<span data-marker="item-view/item-date"> · 17 мая в 12:01</span>'
    assert AvitoParser._extract_item_page_publication_label(html) == "17 мая в 12:01"


def test_extract_item_page_publication_label_from_sort_formated_date():
    html = '<script>window.__initial={"sortFormatedDate":"17 мая в 12:01"}</script>'
    assert AvitoParser._extract_item_page_publication_label(html) == "17 мая в 12:01"


def test_extract_item_page_publication_label_fallback_text():
    html = '<div>Размещено сегодня в 12:34</div>'
    assert AvitoParser._extract_item_page_publication_label(html) == "сегодня в 12:34"
