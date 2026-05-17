import asyncio
import json
from datetime import UTC, datetime

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
                    published_label="Сегодня 12:34",
                    published_at=datetime(2026, 5, 17, 9, 34, 0),
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


class _FakeLocator:
    def __init__(self, text: str = ""):
        self.first = self
        self._text = text

    async def count(self):
        return 1 if self._text else 0

    async def text_content(self):
        return self._text


class _FakeCardWithAddressMarker:
    def locator(self, selector: str):
        if selector == '[data-marker="item-address"]':
            return _FakeLocator("  Казань, ул. Баумана, 7  ")
        return _FakeLocator()


def test_extract_structured_address_prefers_card_marker():
    result = asyncio.run(
        AvitoParser._extract_structured_address(_FakeCardWithAddressMarker())
    )

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


class _FakeCardWithPublicationMarker:
    def locator(self, selector: str):
        if selector == '[data-marker*="item-date"]':
            return _FakeLocator("Вчера 09:10")
        return _FakeLocator()


def test_extract_structured_published_label_prefers_marker_over_fallback_text():
    result = asyncio.run(
        AvitoParser._extract_structured_published_label(_FakeCardWithPublicationMarker())
    )

    assert result == "Вчера 09:10"


def test_unknown_published_label_returns_none_without_exception():
    assert AvitoParser._extract_published_label("Адрес рядом с метро") == ""
    assert AvitoParser._parse_published_at("непонятная дата", datetime(2026, 5, 17)) is None
