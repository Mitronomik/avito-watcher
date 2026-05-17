import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.parsers.avito_parser import AvitoParser
from app.parsers.errors import ParserError, ParserErrorType
from app.parsers.schemas import ListingCard


SEARCH_URL = "https://www.avito.ru/moskva/kvartiry"


def test_fetch_search_cards_parses_real_card_html():
    parser = AvitoParser(now_func=lambda: datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC))
    html = """
    <html>
      <head><title>Avito</title></head>
      <body>
        <div data-marker="item">
          <a href="/moskva/kvartiry/some-advert_987654321">
            <h3>2-к. квартира, 54,3 м², 5/9 эт.</h3>
          </a>
          <span data-marker="item-price">5 000 000 ₽</span>
          <span>ул. Ленина, 10</span>
          <span data-marker="item-date">Сегодня 10:30</span>
        </div>
      </body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        result = asyncio.run(parser.fetch_search_cards(SEARCH_URL))

    assert isinstance(result, list)
    assert len(result) == 1
    card = result[0]
    assert isinstance(card, ListingCard)
    assert card.external_id == "987654321"
    assert card.price == 5_000_000.0
    assert card.area_m2 == 54.3
    assert card.rooms == "2-к."
    assert "Ленина" in card.address
    assert card.published_at is not None


def test_fetch_search_cards_captcha_html_raises_correct_error():
    parser = AvitoParser()
    html = "<html><body>verify you are human</body></html>"

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards(SEARCH_URL))

    assert exc_info.value.error_type == ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK


def test_fetch_search_cards_empty_results_raises_correct_error():
    parser = AvitoParser()
    html = "<html><body>ничего не найдено</body></html>"

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards(SEARCH_URL))

    assert exc_info.value.error_type == ParserErrorType.EMPTY_RESULTS


def test_fetch_search_cards_no_item_markers_raises_layout_changed():
    parser = AvitoParser()
    html = """
    <html>
      <head><title>Avito</title></head>
      <body><main><section>Обычная страница поиска без карточек</section></main></body>
    </html>
    """

    with patch.object(parser, "_fetch_page_html", new=AsyncMock(return_value=html)):
        with pytest.raises(ParserError) as exc_info:
            asyncio.run(parser.fetch_search_cards(SEARCH_URL))

    assert exc_info.value.error_type == ParserErrorType.LAYOUT_CHANGED
