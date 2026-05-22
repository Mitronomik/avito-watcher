import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.parsers.avito_parser import AvitoParser
from app.parsers.errors import ParserError, ParserErrorType

SEARCH_URL = "https://www.avito.ru/moskva/kvartiry?s=104&user=1"


def _page_html(external_ids: list[str]) -> str:
    cards = "".join(
        f'<div data-marker="item"><a href="/item_{eid}"><h3>Item {eid}</h3></a><span>1 000 000 ₽</span></div>'
        for eid in external_ids
    )
    return f"<html><head><title>Avito</title></head><body>{cards}</body></html>"


def test_paginated_builds_urls_and_preserves_query_params(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_max_pages", 3)
    parser = AvitoParser()
    fetch = AsyncMock(side_effect=[_page_html(["1"]), _page_html(["2"]), _page_html(["3"])])

    with patch.object(parser, "_fetch_page_html", new=fetch):
        result = asyncio.run(parser.fetch_search_cards_paginated(SEARCH_URL))

    assert [c.external_id for c in result["cards"]] == ["1", "2", "3"]
    called_urls = [call.args[0] for call in fetch.await_args_list]
    assert called_urls[0] == SEARCH_URL
    assert "s=104" in called_urls[1] and "user=1" in called_urls[1] and "p=2" in called_urls[1]
    assert "s=104" in called_urls[2] and "user=1" in called_urls[2] and "p=3" in called_urls[2]


def test_paginated_dedupes_and_stops_on_duplicate_page(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_max_pages", 3)
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_stop_on_duplicate_page", True)
    parser = AvitoParser()
    fetch = AsyncMock(side_effect=[_page_html(["1", "2"]), _page_html(["1", "2"]), _page_html(["3"])])

    with patch.object(parser, "_fetch_page_html", new=fetch):
        result = asyncio.run(parser.fetch_search_cards_paginated(SEARCH_URL))

    assert [c.external_id for c in result["cards"]] == ["1", "2"]
    assert result["duplicate_cards_skipped"] == 2
    assert result["pagination_stopped_reason"] == "duplicate_page"
    assert result["pages_attempted"] == 2


def test_paginated_stops_on_empty_results(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_max_pages", 3)
    parser = AvitoParser()
    fetch = AsyncMock(side_effect=[_page_html(["1"]), "<html><body>ничего не найдено</body></html>"])

    with patch.object(parser, "_fetch_page_html", new=fetch):
        result = asyncio.run(parser.fetch_search_cards_paginated(SEARCH_URL))

    assert [c.external_id for c in result["cards"]] == ["1"]
    assert result["pagination_stopped_reason"] == "page_error"
    assert result["page_errors"][0]["error_type"] == ParserErrorType.EMPTY_RESULTS.value


def test_page1_parser_error_raises(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_max_pages", 2)
    parser = AvitoParser()
    fetch = AsyncMock(side_effect=ParserError(ParserErrorType.LAYOUT_CHANGED, "broken"))

    with patch.object(parser, "_fetch_page_html", new=fetch):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards_paginated(SEARCH_URL))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED


def test_later_page_error_becomes_diagnostic_and_stops(monkeypatch):
    monkeypatch.setattr("app.parsers.avito_parser.settings.scrape_max_pages", 3)
    parser = AvitoParser()
    fetch = AsyncMock(side_effect=[_page_html(["1"]), ParserError(ParserErrorType.LAYOUT_CHANGED, "broken")])

    with patch.object(parser, "_fetch_page_html", new=fetch):
        result = asyncio.run(parser.fetch_search_cards_paginated(SEARCH_URL))

    assert [c.external_id for c in result["cards"]] == ["1"]
    assert result["pagination_stopped_reason"] == "page_error"
    assert result["page_errors"] == [{"page": 2, "error_type": "layout_changed", "error": "layout_changed: broken"}]
