import hashlib
import re
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo
from urllib.parse import urljoin, urlparse

from app.parsers.block_signals import looks_like_block_or_captcha
from app.parsers.browser_engine import fetch_with_camoufox, fetch_with_nodriver, open_camoufox_session, open_nodriver_session
from app.parsers.errors import ParserError, ParserErrorType
from app.parsers.proxy_manager import ProxyManager
from app.parsers.schemas import ListingCard

if TYPE_CHECKING:
    from bs4.element import Tag


CARD_LIMIT = 30
CARD_SELECTOR = '[data-marker="item"]'
AVITO_HOST_SUFFIX = "avito.ru"
EMPTY_RESULTS_KEYWORDS = (
    "ничего не найдено",
    "нет результатов",
    "объявлений не найдено",
    "попробуйте изменить параметры поиска",
)
ADDRESS_MARKER_SELECTORS = (
    '[data-marker="item-address"]',
    '[data-marker="item-location"]',
    '[data-marker*="address"]',
    '[data-marker*="location"]',
)
ADDRESS_HINTS = (
    "ул.",
    "улица",
    "проспект",
    "пр-т",
    "шоссе",
    "пер.",
    "переулок",
    "бульвар",
    "наб.",
    "набережная",
    "площадь",
    "пл.",
    "проезд",
    "тупик",
    "аллея",
    "линия",
    "мкр",
    "микрорайон",
    "район",
    "р-н",
    "метро",
    "м.",
    "жк",
)
AREA_RE = re.compile(r"(?<!\d)(\d+(?:[,.]\d+)?)\s*(?:м²|кв\.?\s*м)(?!\w)", re.IGNORECASE)
ROOMS_RE = re.compile(r"(?<!\d)([1-4])\s*-\s*к\.", re.IGNORECASE)
PUBLICATION_MARKER_SELECTORS = (
    '[data-marker*="item-date"]',
    '[data-marker*="date"]',
    '[data-marker*="time"]',
)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
PUBLICATION_PATTERNS = (
    re.compile(r"(?:сегодня|вчера)\s*(?:в\s*)?\d{1,2}:\d{2}", re.IGNORECASE),
    re.compile(r"\d+\s*(?:час|часа|часов|минуту|минуты|минут)\s+назад", re.IGNORECASE),
    re.compile(
        r"\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
        r"сентября|октября|ноября|декабря)(?:\s*(?:в\s*)?\d{1,2}:\d{2})?",
        re.IGNORECASE,
    ),
)


class _Engine(Enum):
    NODRIVER = "nodriver"
    CAMOUFOX = "camoufox"


class AvitoParser:
    def __init__(self, now_func=None, proxy_manager: ProxyManager | None = None) -> None:
        self.now_func = now_func or (lambda: datetime.now(UTC))
        self._proxy_manager = proxy_manager
        self._prefer_engine = _Engine.NODRIVER
        self._engine_sessions: dict[tuple[_Engine, str | None], object] = {}
        self._cycle_active = False

    def _now(self) -> datetime:
        return self.now_func()

    async def _try_engine(self, url: str, proxy_url: str | None, engine: _Engine) -> dict:
        session = self._engine_sessions.get((engine, proxy_url))
        if session is not None:
            return await session.fetch(url)
        if engine == _Engine.NODRIVER:
            return await fetch_with_nodriver(url, proxy_url)
        return await fetch_with_camoufox(url, proxy_url)

    async def begin_cycle(self) -> None:
        self._cycle_active = True

    async def end_cycle(self) -> None:
        self._cycle_active = False
        sessions = list(self._engine_sessions.values())
        self._engine_sessions.clear()
        for session in sessions:
            close = getattr(session, "close", None)
            if close is None:
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result

    async def ensure_engine_session(self, engine: _Engine, proxy_url: str | None) -> None:
        if not self._cycle_active:
            return
        key = (engine, proxy_url)
        if key in self._engine_sessions:
            return
        if engine == _Engine.NODRIVER:
            self._engine_sessions[key] = await open_nodriver_session(proxy_url)
        else:
            self._engine_sessions[key] = await open_camoufox_session(proxy_url)

    async def _fetch_page_html(self, url: str) -> str:
        """Fetch raw HTML using stealth engine with Nodriver→Camoufox fallback.

        Uses proxy from ProxyManager if configured (opt-in via PROXY_URLS env var).
        Falls back to direct (no-proxy) connection when PROXY_URLS is unset.
        Raises ParserError(POSSIBLE_CAPTCHA_OR_BLOCK) if all attempts blocked.
        """
        import logging as _logging
        _log = _logging.getLogger(__name__)

        proxy_url: str | None = None
        if self._proxy_manager:
            proxy_url = self._proxy_manager.get_proxy()

        # First attempt
        await self.ensure_engine_session(self._prefer_engine, proxy_url)
        result = await self._try_engine(url, proxy_url, self._prefer_engine)
        if result["ok"]:
            if proxy_url and self._proxy_manager:
                self._proxy_manager.report_success(proxy_url)
            return result["html"]

        _log.warning(
            "avito_parser: %s blocked (error_type=%s), switching engine",
            self._prefer_engine.value,
            result.get("error_type"),
        )
        if proxy_url and self._proxy_manager:
            self._proxy_manager.report_failure(proxy_url)
            proxy_url = self._proxy_manager.get_proxy()

        fallback = (
            _Engine.CAMOUFOX
            if self._prefer_engine == _Engine.NODRIVER
            else _Engine.NODRIVER
        )

        # Fallback attempt
        await self.ensure_engine_session(fallback, proxy_url)
        result2 = await self._try_engine(url, proxy_url, fallback)
        if result2["ok"]:
            if proxy_url and self._proxy_manager:
                self._proxy_manager.report_success(proxy_url)
            self._prefer_engine = fallback
            return result2["html"]

        if proxy_url and self._proxy_manager:
            self._proxy_manager.report_failure(proxy_url)

        raise ParserError(
            ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
            "All stealth engines blocked (nodriver + camoufox)",
        )

    async def fetch_search_cards(self, search_url: str) -> list[ListingCard]:
        self._validate_search_url(search_url)

        # Fetch HTML via stealth engine (nodriver → camoufox fallback)
        # _fetch_page_html raises ParserError only when all engines are blocked.
        page_html: str = await self._fetch_page_html(search_url)

        # Parse the returned HTML with BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page_html, "lxml")

        # Check for block/captcha in fetched HTML
        body_text = soup.get_text(separator=" ", strip=True)
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        if self._looks_like_captcha_or_block(title, body_text):
            raise ParserError(
                ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
                "Search page content looks like captcha, robot check, or access block",
            )

        if self._looks_like_empty_results(body_text):
            raise ParserError(
                ParserErrorType.EMPTY_RESULTS,
                "Avito search page loaded but reports empty results",
            )

        raw_cards = soup.select(CARD_SELECTOR)
        if not raw_cards:
            raise ParserError(
                ParserErrorType.LAYOUT_CHANGED,
                "No Avito search result cards found in fetched HTML",
            )

        result: list[ListingCard] = []

        for idx, card in enumerate(raw_cards[:CARD_LIMIT]):
            a_tag = card.select_one("a[href]")
            href = a_tag.get("href") if a_tag else None

            h3_tag = card.select_one("h3") or card.select_one('[itemprop="name"]')
            title = h3_tag.get_text(strip=True) if h3_tag else ""

            text = card.get_text(separator=" ", strip=True)
            price = self._extract_price(text)

            address = self._extract_structured_address_bs(card)
            if not address:
                address = self._extract_address_from_text(text)

            external_id = self._extract_external_id(href, idx)

            published_label = self._extract_structured_published_label_bs(card)
            if not published_label:
                published_label = self._extract_published_label(text)
            published_at = self._parse_published_at(published_label, self._now())

            result.append(
                ListingCard(
                    external_id=external_id,
                    url=urljoin("https://www.avito.ru", href or ""),
                    title=title,
                    price=price,
                    address=address,
                    area_m2=self._extract_area_m2(text),
                    rooms=self._extract_rooms(text),
                    published_label=published_label,
                    published_at=published_at,
                    raw={"position": idx, "text": text[:1000]},
                )
            )

        return result

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
        return looks_like_block_or_captcha(title, body_text)

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
    def _extract_area_m2(text: str) -> float | None:
        match = AREA_RE.search(text)
        if not match:
            return None

        return float(match.group(1).replace(",", "."))

    @staticmethod
    def _extract_rooms(text: str) -> str:
        match = ROOMS_RE.search(text)
        if match:
            return f"{match.group(1)}-к."

        lowered = text.lower()
        if "студия" in lowered:
            return "студия"
        if "апартаменты" in lowered:
            return "апартаменты"

        return ""

    @staticmethod
    def _normalize_text_line(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip(" ,;•·")

    @classmethod
    def _extract_structured_address_bs(cls, card: "Tag") -> str:
        for selector in ADDRESS_MARKER_SELECTORS:
            element = card.select_one(selector)
            if element is None:
                continue

            text = cls._normalize_text_line(element.get_text(separator=" ", strip=True))
            if text:
                return text

        return ""

    @classmethod
    def _extract_address_from_text(cls, text: str) -> str:
        for raw_line in text.splitlines():
            line = cls._normalize_text_line(raw_line)
            lowered = line.lower()
            if not line or len(line) > 160:
                continue
            if "₽" in line or AREA_RE.search(line) or ROOMS_RE.search(line):
                continue
            if any(hint in lowered for hint in ADDRESS_HINTS):
                return line

        match = re.search(
            r"((?:[А-ЯЁ][а-яё-]+,\s*)?(?:ул\.|улица|проспект|пр-т|шоссе|пер\.|"
            r"переулок|бульвар|наб\.|набережная|площадь|пл\.|проезд|мкр|"
            r"микрорайон)\s+[^\n,;•]{2,80}(?:,\s*\d+[А-Яа-яA-Za-z/-]*)?)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return ""

        return cls._normalize_text_line(match.group(1))


    @staticmethod
    def _to_moscow(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).astimezone(MOSCOW_TZ)
        return value.astimezone(MOSCOW_TZ)

    @staticmethod
    def _to_naive_utc(value: datetime) -> datetime:
        # Store Avito publication timestamps as naive UTC datetimes, matching other DB timestamps.
        return value.astimezone(UTC).replace(tzinfo=None)

    @classmethod
    def _extract_structured_published_label_bs(cls, card: "Tag") -> str:
        for selector in PUBLICATION_MARKER_SELECTORS:
            element = card.select_one(selector)
            if element is None:
                continue
            label = cls._extract_published_label(element.get_text(separator=" ", strip=True))
            if label:
                return label
        return ""

    @classmethod
    def _extract_published_label(cls, text: str) -> str:
        normalized = cls._normalize_text_line(text)
        if not normalized:
            return ""
        for pattern in PUBLICATION_PATTERNS:
            match = pattern.search(normalized)
            if match:
                return cls._normalize_text_line(match.group(0))
        return ""

    @classmethod
    def _parse_published_at(cls, label: str, now: datetime) -> datetime | None:
        label = cls._normalize_text_line(label).lower()
        if not label:
            return None

        now_msk = cls._to_moscow(now)

        relative_match = re.fullmatch(
            r"(\d+)\s*(час|часа|часов|минуту|минуты|минут)\s+назад", label
        )
        if relative_match:
            amount = int(relative_match.group(1))
            unit = relative_match.group(2)
            delta = timedelta(hours=amount) if unit.startswith("час") else timedelta(minutes=amount)
            return cls._to_naive_utc(now_msk - delta)

        today_yesterday_match = re.fullmatch(
            r"(сегодня|вчера)\s*(?:в\s*)?(\d{1,2}):(\d{2})", label
        )
        if today_yesterday_match:
            day_word, hour, minute = today_yesterday_match.groups()
            day = now_msk.date()
            if day_word == "вчера":
                day -= timedelta(days=1)
            local_dt = datetime(
                day.year, day.month, day.day, int(hour), int(minute), tzinfo=MOSCOW_TZ
            )
            return cls._to_naive_utc(local_dt)

        month_match = re.fullmatch(
            r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|"
            r"сентября|октября|ноября|декабря)(?:\s*(?:в\s*)?(\d{1,2}):(\d{2}))?",
            label,
        )
        if month_match:
            day_text, month_text, hour_text, minute_text = month_match.groups()
            day = int(day_text)
            month = MONTHS_RU[month_text]
            year = now_msk.year
            hour = int(hour_text) if hour_text is not None else 0
            minute = int(minute_text) if minute_text is not None else 0
            try:
                local_dt = datetime(year, month, day, hour, minute, tzinfo=MOSCOW_TZ)
            except ValueError:
                return None
            if local_dt > now_msk + timedelta(days=1):
                local_dt = local_dt.replace(year=year - 1)
            return cls._to_naive_utc(local_dt)

        return None

    @staticmethod
    def _extract_external_id(href: str | None, idx: int) -> str:
        if not href:
            return f"unknown-{idx}"

        match = re.search(r"_(\d+)(?:\?|$)", href)
        if match:
            return match.group(1)

        digest = hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
        return f"fallback-{digest}"
