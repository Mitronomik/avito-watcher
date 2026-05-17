import asyncio
import json

import pytest

from app import cli
from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot
from app.parsers.avito_parser import AvitoParser
from app.parsers.errors import ParserError, ParserErrorType
from app.parsers.schemas import ListingCard
from app.services.monitor_service import MonitorService
from tests.test_baseline_monitor import (
    FakeNotifier,
    FakeParser,
    FakeScorer,
    make_search,
    scalar_count,
)


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


def test_parser_preserves_sha256_external_id_fallback():
    external_id = AvitoParser._extract_external_id("/moskva/kvartiry/custom-slug", 0)

    assert external_id.startswith("fallback-")
    assert len(external_id) == len("fallback-") + 16


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
                )
                for idx in range(6)
            ]

    monkeypatch.setattr(cli, "AvitoParser", FakeDryRunParser)

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
    }
    assert scalar_count(db_session, Listing) == 0
    assert scalar_count(db_session, ListingSnapshot) == 0
    assert scalar_count(db_session, AlertSent) == 0


def test_dry_run_search_reports_classified_parser_error(monkeypatch, capsys):
    class FakeDryRunParser:
        async def fetch_search_cards(self, url):
            assert url == "bad-url"
            raise ParserError(ParserErrorType.INVALID_URL, "search_url must start with http:// or https://")

    monkeypatch.setattr(cli, "AvitoParser", FakeDryRunParser)

    parser = cli.build_parser()
    args = parser.parse_args(["dry-run-search", "--url", "bad-url"])
    args.func(args)

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["total_cards"] == 0
    assert output["cards"] == []
    assert output["error_type"] == "invalid_url"
    assert output["error"].startswith("invalid_url:")
