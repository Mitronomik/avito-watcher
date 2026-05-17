import hashlib
import re
from urllib.parse import urljoin, urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.core.config import settings
from app.parsers.errors import ParserError, ParserErrorType
from app.parsers.schemas import ListingCard


CARD_LIMIT = 30
AVITO_HOST_SUFFIX = "avito.ru"
BLOCK_KEYWORDS = (
    "captcha",
    "капча",
    "подтвердите, что вы не робот",
    "проверка безопасности",
    "доступ ограничен",
    "доступ заблокирован",
    "слишком много запросов",
    "too many requests",
    "access denied",
    "verify you are human",
    "robot check",
)
EMPTY_RESULTS_KEYWORDS = (
    "ничего не найдено",
    "нет результатов",
    "объявлений не найдено",
    "попробуйте изменить параметры поиска",
)


class AvitoParser:
    async def fetch_search_cards(self, search_url: str) -> list[ListingCard]:
        self._validate_search_url(search_url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.scrape_headless)
            try:
                page = await browser.new_page()
                try:
                    await page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=settings.scrape_timeout_ms,
                    )
                except PlaywrightTimeoutError as exc:
                    raise ParserError(
                        ParserErrorType.NAVIGATION_TIMEOUT,
                        f"Timed out while navigating to Avito search page after {settings.scrape_timeout_ms} ms",
                    ) from exc

                await page.wait_for_timeout(2000)

                title = await page.title()
                body_text = (await page.locator("body").first.text_content()) or ""
                if self._looks_like_captcha_or_block(title, body_text):
                    raise ParserError(
                        ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
                        "Search page content looks like captcha, robot check, or access block",
                    )

                cards = await page.locator('[data-marker="item"]').all()
                if not cards:
                    error_type = (
                        ParserErrorType.EMPTY_RESULTS
                        if self._looks_like_empty_results(body_text)
                        else ParserErrorType.LAYOUT_CHANGED
                    )
                    raise ParserError(
                        error_type,
                        "No Avito search result cards found without opening listing pages",
                    )

                result: list[ListingCard] = []

                for idx, card in enumerate(cards[:CARD_LIMIT]):
                    link_locator = card.locator("a").first
                    href = await link_locator.get_attribute("href") if await link_locator.count() else None

                    title_locator = card.locator("h3").first
                    title = await title_locator.text_content() if await title_locator.count() else ""

                    text = (await card.text_content()) or ""
                    price = self._extract_price(text)
                    external_id = self._extract_external_id(href, idx)

                    result.append(
                        ListingCard(
                            external_id=external_id,
                            url=urljoin("https://www.avito.ru", href or ""),
                            title=(title or "").strip(),
                            price=price,
                            raw={"position": idx, "text": text[:1000]},
                        )
                    )

                return result
            finally:
                await browser.close()

    @staticmethod
    def _validate_search_url(search_url: str) -> None:
        parsed = urlparse(search_url)
        if parsed.scheme not in {"http", "https"}:
            raise ParserError(
                ParserErrorType.INVALID_URL,
                "search_url must start with http:// or https://",
            )

        hostname = (parsed.hostname or "").lower()
        if not (hostname == AVITO_HOST_SUFFIX or hostname.endswith(f".{AVITO_HOST_SUFFIX}")):
            raise ParserError(
                ParserErrorType.INVALID_URL,
                "search_url host must be avito.ru or an avito.ru subdomain",
            )

    @staticmethod
    def _looks_like_captcha_or_block(title: str, body_text: str) -> bool:
        content = f"{title}\n{body_text}".lower()
        return any(keyword in content for keyword in BLOCK_KEYWORDS)

    @staticmethod
    def _looks_like_empty_results(body_text: str) -> bool:
        content = body_text.lower()
        return any(keyword in content for keyword in EMPTY_RESULTS_KEYWORDS)

    @staticmethod
    def _extract_price(text: str) -> float | None:
        match = re.search(r"(\d[\d\s]{3,})\s*₽", text)
        if not match:
            return None

        digits = re.sub(r"\D", "", match.group(1))
        return float(digits) if digits else None

    @staticmethod
    def _extract_external_id(href: str | None, idx: int) -> str:
        if not href:
            return f"unknown-{idx}"

        match = re.search(r"_(\d+)(?:\?|$)", href)
        if match:
            return match.group(1)

        digest = hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
        return f"fallback-{digest}"
