import hashlib
import re
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from app.core.config import settings
from app.parsers.schemas import ListingCard


class AvitoParser:
    async def fetch_search_cards(self, search_url: str) -> list[ListingCard]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=settings.scrape_headless)
            try:
                page = await browser.new_page()
                await page.goto(search_url, wait_until="domcontentloaded", timeout=settings.scrape_timeout_ms)
                await page.wait_for_timeout(2000)

                cards = await page.locator('[data-marker="item"]').all()
                result: list[ListingCard] = []

                for idx, card in enumerate(cards[:30]):
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
