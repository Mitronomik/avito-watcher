import asyncio
import hashlib
import json
import logging
from pathlib import Path
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.parse import unquote

from app.parsers.block_signals import looks_like_block_or_captcha
from app.core.config import settings
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
AVITO_LISTING_URL_PATH_RE = re.compile(r"^/[a-z0-9_-]+/kvartiry/[^/\s]+_(\d{10})(?:\?.*)?$", re.IGNORECASE)
MAX_FUTURE_PUBLISHED_AT_DAYS = 7
MAX_DIAGNOSTIC_STATE_PATHS = 12
MAX_DIAGNOSTIC_OBJECT_KEY_SETS = 8
MAX_DIAGNOSTIC_IGNORED_OBJECT_KEY_SETS = 6
MAX_PRELOADED_STATE_SCAN_NODES = 5000
MAX_PRELOADED_STATE_SCAN_DEPTH = 24
PUBLICATION_MARKER_SELECTORS = (
    '[data-marker*="item-date"]',
    '[data-marker*="date"]',
    '[data-marker*="time"]',
)
ITEM_PAGE_DESCRIPTION_MAX = 2000
ITEM_PAGE_SELLER_NAME_MAX = 120
ITEM_PAGE_BADGES_MAX = 20
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
logger = logging.getLogger(__name__)

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


class BrowserSession(Protocol):
    async def fetch(self, url: str) -> dict: ...

    async def close(self) -> None: ...


class _Engine(Enum):
    NODRIVER = "nodriver"
    CAMOUFOX = "camoufox"


@dataclass
class _CycleCounters:
    engine_used: str | None = None
    engine_fallback_count: int = 0
    session_open_count: int = 0
    session_reuse_count: int = 0
    session_evict_count: int = 0
    session_close_failure_count: int = 0
    block_detected_count: int = 0
    engine_error_count: int = 0
    timeout_failure_count: int = 0
    timeout_retry_attempt_count: int = 0
    timeout_retry_success_count: int = 0
    proxy_success_count: int = 0
    proxy_failure_count: int = 0
    proxy_quarantine_on_failure_count: int = 0
    engine_skip_recent_failure_count: int = 0
    preferred_engine: str | None = None
    selected_first_engine: str | None = None
    engine_selection_changed_by_health_memory: bool = False
    fallback_used: bool = False
    serp_state_fallback_attempted: bool = False
    serp_state_fallback_succeeded: bool = False
    serp_state_fallback_card_count: int = 0
    serp_link_fallback_attempted: bool = False
    serp_link_fallback_succeeded: bool = False
    serp_link_fallback_card_count: int = 0
    layout_changed_hint: str | None = None

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "engine_used": self.engine_used,
            "engine_fallback_count": self.engine_fallback_count,
            "session_open_count": self.session_open_count,
            "session_reuse_count": self.session_reuse_count,
            "session_evict_count": self.session_evict_count,
            "session_close_failure_count": self.session_close_failure_count,
            "block_detected_count": self.block_detected_count,
            "engine_error_count": self.engine_error_count,
            "timeout_failure_count": self.timeout_failure_count,
            "timeout_retry_attempt_count": self.timeout_retry_attempt_count,
            "timeout_retry_success_count": self.timeout_retry_success_count,
            "proxy_success_count": self.proxy_success_count,
            "proxy_failure_count": self.proxy_failure_count,
            "proxy_quarantine_on_failure_count": self.proxy_quarantine_on_failure_count,
            "engine_skip_recent_failure_count": self.engine_skip_recent_failure_count,
            "preferred_engine": self.preferred_engine,
            "selected_first_engine": self.selected_first_engine,
            "engine_selection_changed_by_health_memory": self.engine_selection_changed_by_health_memory,
            "fallback_used": self.fallback_used,
            "serp_state_fallback_attempted": self.serp_state_fallback_attempted,
            "serp_state_fallback_succeeded": self.serp_state_fallback_succeeded,
            "serp_state_fallback_card_count": self.serp_state_fallback_card_count,
            "serp_link_fallback_attempted": self.serp_link_fallback_attempted,
            "serp_link_fallback_succeeded": self.serp_link_fallback_succeeded,
            "serp_link_fallback_card_count": self.serp_link_fallback_card_count,
            "layout_changed_hint": self.layout_changed_hint,
        }


class AvitoParser:
    def __init__(self, now_func=None, proxy_manager: ProxyManager | None = None, preferred_engine: str | None = None) -> None:
        self.now_func = now_func or (lambda: datetime.now(UTC))
        self._proxy_manager = proxy_manager
        self._prefer_engine = _Engine.NODRIVER
        self._preferred_engine_mode = preferred_engine or settings.scrape_preferred_engine
        if self._preferred_engine_mode not in {"auto", "nodriver", "camoufox"}:
            raise ValueError("preferred_engine must be one of: auto, nodriver, camoufox")
        self._allowed_engines_mode = settings.scrape_allowed_engines
        if self._allowed_engines_mode not in {"both", "nodriver", "camoufox"}:
            raise ValueError("scrape_allowed_engines must be one of: both, nodriver, camoufox")
        self._engine_sessions: dict[tuple[_Engine, str | None], BrowserSession] = {}
        self._engine_recent_failures: dict[tuple[_Engine, str], int] = {}
        self._cycle_active = False
        self._cycle_counters = _CycleCounters()

    def _now(self) -> datetime:
        return self.now_func()

    @staticmethod
    def _proxy_key(proxy_url: str | None) -> str:
        return proxy_url or "no_proxy"

    def _choose_start_engine(self, proxy_url: str | None) -> _Engine:
        allowed_engines = self._allowed_engines()
        proxy_key = self._proxy_key(proxy_url)
        nodriver_failures = self._engine_recent_failures.get((_Engine.NODRIVER, proxy_key), 0)
        if self._preferred_engine_mode == "camoufox":
            preferred_engine = _Engine.CAMOUFOX
        elif self._preferred_engine_mode == "nodriver":
            preferred_engine = _Engine.NODRIVER
        else:
            preferred_engine = self._prefer_engine

        start_engine = preferred_engine if preferred_engine in allowed_engines else allowed_engines[0]
        changed_by_health = False
        if (
            preferred_engine == _Engine.NODRIVER
            and nodriver_failures > 0
            and _Engine.CAMOUFOX in allowed_engines
        ):
            self._cycle_counters.engine_skip_recent_failure_count += 1
            start_engine = _Engine.CAMOUFOX
            changed_by_health = True
            logger.info(
                "avito_parser: skipping nodriver due to recent failures proxy=%s failures=%s",
                proxy_key,
                nodriver_failures,
            )
        self._cycle_counters.preferred_engine = self._preferred_engine_mode
        self._cycle_counters.selected_first_engine = start_engine.value
        self._cycle_counters.engine_selection_changed_by_health_memory = changed_by_health
        logger.debug(
            "avito_parser: engine decision proxy=%s preferred_engine=%s selected_first_engine=%s changed_by_health_memory=%s nodriver_recent_failures=%s",
            proxy_key,
            self._preferred_engine_mode,
            start_engine.value,
            changed_by_health,
            nodriver_failures,
        )
        return start_engine

    def _allowed_engines(self) -> tuple[_Engine, ...]:
        if self._allowed_engines_mode == "camoufox":
            return (_Engine.CAMOUFOX,)
        if self._allowed_engines_mode == "nodriver":
            return (_Engine.NODRIVER,)
        return (_Engine.NODRIVER, _Engine.CAMOUFOX)

    def _record_engine_result(self, engine: _Engine, proxy_url: str | None, result: dict) -> None:
        proxy_key = self._proxy_key(proxy_url)
        key = (engine, proxy_key)
        if result.get("ok"):
            self._engine_recent_failures.pop(key, None)
            return
        if engine != _Engine.NODRIVER:
            return
        if result.get("error_type") in {"timeout", "possible_captcha_or_block"}:
            self._engine_recent_failures[key] = self._engine_recent_failures.get(key, 0) + 1

    def _proxy_quarantine_events(self) -> int | None:
        if not self._proxy_manager or not hasattr(self._proxy_manager, "stats"):
            return None
        stats = self._proxy_manager.stats()
        events = stats.get("quarantine_events") if isinstance(stats, dict) else None
        return events if isinstance(events, int) else None

    async def _evict_engine_session(self, engine: _Engine, proxy_url: str | None) -> None:
        key = (engine, proxy_url)
        session = self._engine_sessions.pop(key, None)
        if session is None:
            return
        self._cycle_counters.session_evict_count += 1
        try:
            await session.close()
        except Exception as exc:
            self._cycle_counters.session_close_failure_count += 1
            logger.warning("avito_parser: failed to close browser session: %s", exc)

    async def _try_engine(self, url: str, proxy_url: str | None, engine: _Engine) -> dict:
        session = self._engine_sessions.get((engine, proxy_url))
        if session is not None:
            self._cycle_counters.session_reuse_count += 1
            result = await session.fetch(url)
            if not result.get("ok") and result.get("error_type") in {"exception", "timeout"}:
                await self._evict_engine_session(engine, proxy_url)
            return result
        self._cycle_counters.engine_used = engine.value
        if engine == _Engine.NODRIVER:
            return await fetch_with_nodriver(url, proxy_url)
        return await fetch_with_camoufox(url, proxy_url)

    async def begin_cycle(self) -> None:
        self._cycle_active = True
        self._cycle_counters = _CycleCounters()

    async def end_cycle(self) -> None:
        self._cycle_active = False
        sessions = list(self._engine_sessions.values())
        self._engine_sessions.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception as exc:
                self._cycle_counters.session_close_failure_count += 1
                logger.warning("avito_parser: failed to close browser session: %s", exc)
        logger.info("avito_parser.end_cycle stats=%s", self._cycle_counters.as_dict())

    def cycle_stats(self) -> dict[str, int | str | None]:
        return self._cycle_counters.as_dict().copy()

    async def ensure_engine_session(self, engine: _Engine, proxy_url: str | None) -> dict | None:
        if not self._cycle_active:
            return None
        key = (engine, proxy_url)
        if key in self._engine_sessions:
            return None
        try:
            if engine == _Engine.NODRIVER:
                self._engine_sessions[key] = await open_nodriver_session(proxy_url)
            else:
                self._engine_sessions[key] = await open_camoufox_session(proxy_url)
            self._cycle_counters.session_open_count += 1
            self._cycle_counters.engine_used = engine.value
        except Exception as exc:
            return {"ok": False, "engine": engine.value, "error_type": "exception", "error": str(exc)}
        return None

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
            if proxy_url is None:
                raise ParserError(
                    ParserErrorType.PROXY_UNAVAILABLE,
                    "No available proxies: all configured proxies are quarantined",
                )

        retry_on_timeout = bool(settings.scrape_timeout_retry_once)
        retry_delay_ms = max(int(settings.scrape_timeout_retry_delay_ms), 0)
        timeout_retry_attempted = False

        # First attempt
        start_engine = self._choose_start_engine(proxy_url)
        setup_error = await self.ensure_engine_session(start_engine, proxy_url)
        result = setup_error or await self._try_engine(url, proxy_url, start_engine)
        self._record_engine_result(start_engine, proxy_url, result)
        if result.get("error_type") == "timeout":
            self._cycle_counters.timeout_failure_count += 1
            if retry_on_timeout:
                timeout_retry_attempted = True
                self._cycle_counters.timeout_retry_attempt_count += 1
                if retry_delay_ms > 0:
                    await asyncio.sleep(retry_delay_ms / 1000.0)
                retry_result = await self._try_engine(url, proxy_url, start_engine)
                self._record_engine_result(start_engine, proxy_url, retry_result)
                if retry_result.get("error_type") == "timeout":
                    self._cycle_counters.timeout_failure_count += 1
                if retry_result.get("ok"):
                    self._cycle_counters.timeout_retry_success_count += 1
                result = retry_result
        if result["ok"]:
            if proxy_url and self._proxy_manager:
                self._proxy_manager.report_success(proxy_url)
                self._cycle_counters.proxy_success_count += 1
            self._cycle_counters.engine_used = start_engine.value
            return result["html"]

        allowed_engines = self._allowed_engines()
        fallback = next((engine for engine in allowed_engines if engine != start_engine), None)
        _log.warning(
            "avito_parser.engine_failure engine=%s error_type=%s allowed_engines=%s fallback_available=%s timeout_retry_attempted=%s",
            start_engine.value,
            result.get("error_type"),
            ",".join(engine.value for engine in allowed_engines),
            bool(fallback),
            timeout_retry_attempted,
        )
        if result.get("error_type") == "possible_captcha_or_block":
            self._cycle_counters.block_detected_count += 1
        else:
            self._cycle_counters.engine_error_count += 1
        if proxy_url and self._proxy_manager:
            before_events = self._proxy_quarantine_events()
            self._proxy_manager.report_failure(proxy_url)
            self._cycle_counters.proxy_failure_count += 1
            after_events = self._proxy_quarantine_events()
            if before_events is not None and after_events is not None and after_events > before_events:
                self._cycle_counters.proxy_quarantine_on_failure_count += 1
            proxy_url = self._proxy_manager.get_proxy()

        if fallback is None:
            raise ParserError(
                ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
                f"Stealth engine failed ({start_engine.value}); error_type={result.get('error_type')}",
            )

        # Fallback attempt
        self._cycle_counters.engine_fallback_count += 1
        self._cycle_counters.fallback_used = True
        setup_error2 = await self.ensure_engine_session(fallback, proxy_url)
        result2 = setup_error2 or await self._try_engine(url, proxy_url, fallback)
        self._record_engine_result(fallback, proxy_url, result2)
        if result2.get("error_type") == "timeout":
            self._cycle_counters.timeout_failure_count += 1
        if result2["ok"]:
            if proxy_url and self._proxy_manager:
                self._proxy_manager.report_success(proxy_url)
                self._cycle_counters.proxy_success_count += 1
            self._prefer_engine = fallback
            self._cycle_counters.engine_used = fallback.value
            return result2["html"]

        if proxy_url and self._proxy_manager:
            before_events = self._proxy_quarantine_events()
            self._proxy_manager.report_failure(proxy_url)
            self._cycle_counters.proxy_failure_count += 1
            after_events = self._proxy_quarantine_events()
            if before_events is not None and after_events is not None and after_events > before_events:
                self._cycle_counters.proxy_quarantine_on_failure_count += 1

        raise ParserError(
            ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
            f"All stealth engines failed (nodriver + camoufox); last_error_type={result2.get('error_type')}",
        )

    async def fetch_search_cards(self, search_url: str) -> list[ListingCard]:
        paginated = await self.fetch_search_cards_paginated(search_url)
        return paginated["cards"]

    async def fetch_search_cards_paginated(self, search_url: str) -> dict:
        self._validate_search_url(search_url)
        max_pages = max(int(settings.scrape_max_pages), 1)
        per_page_limit = max(int(settings.scrape_cards_per_page_limit), 1)
        stop_on_duplicate_page = bool(settings.scrape_stop_on_duplicate_page)

        all_cards: list[ListingCard] = []
        seen_ids: set[str] = set()
        duplicate_cards_skipped = 0
        page_errors: list[dict] = []
        pages_seen = 0
        pages_attempted = 0
        stop_reason = "max_pages_reached"

        for page in range(1, max_pages + 1):
            pages_attempted += 1
            page_url = self._build_page_url(search_url, page)
            if page > 1:
                delay_ms = max(int(settings.scrape_page_delay_ms), 0)
                jitter_ms = max(int(settings.scrape_page_jitter_ms), 0)
                sleep_ms = delay_ms + (random.randint(0, jitter_ms) if jitter_ms > 0 else 0)
                if sleep_ms > 0:
                    await asyncio.sleep(sleep_ms / 1000.0)
            try:
                page_cards = await self._fetch_and_parse_page_cards(page_url, per_page_limit)
            except ParserError as exc:
                if page == 1:
                    raise
                if exc.error_type == ParserErrorType.EMPTY_RESULTS:
                    stop_reason = "empty_results"
                    break
                page_errors.append(
                    {
                        "page": page,
                        "error_type": exc.error_type.value,
                        "error": str(exc),
                        "page_url_preview": page_url[:220],
                    }
                )
                stop_reason = "page_error"
                break

            pages_seen += 1
            if not page_cards:
                stop_reason = "empty_page"
                break

            unique_on_page = 0
            for card in page_cards:
                if card.external_id in seen_ids:
                    duplicate_cards_skipped += 1
                    continue
                seen_ids.add(card.external_id)
                all_cards.append(card)
                unique_on_page += 1

            if unique_on_page == 0 and stop_on_duplicate_page:
                stop_reason = "duplicate_page"
                break
        else:
            stop_reason = "max_pages_reached"

        cards_processed_before_dedupe = len(all_cards) + duplicate_cards_skipped
        return {
            "cards": all_cards,
            "pages_seen": pages_seen,
            "pages_attempted": pages_attempted,
            "cards_processed_before_dedupe": cards_processed_before_dedupe,
            "cards_seen_before_dedupe": cards_processed_before_dedupe,
            "cards_seen_after_dedupe": len(all_cards),
            "duplicate_cards_skipped": duplicate_cards_skipped,
            "pagination_stopped_reason": stop_reason,
            "page_errors": page_errors,
        }

    async def _fetch_and_parse_page_cards(self, search_url: str, per_page_limit: int) -> list[ListingCard]:
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

        raw_cards = soup.select(CARD_SELECTOR)
        fallback_cards: list[ListingCard] = []
        fallback_diag: dict = {}
        if not raw_cards:
            fallback_diag = self._build_fallback_diagnostics(soup=soup, page_html=page_html)
            fallback_cards = self._parse_cards_from_serp_fallback(
                soup=soup, page_html=page_html, diagnostics=fallback_diag
            )
            self._apply_serp_fallback_diagnostics_to_cycle(fallback_diag)

        if self._looks_like_captcha_or_block(title, body_text) and not (
            fallback_diag.get("has_catalog_items_state", False)
            or fallback_diag.get("has_listing_links_without_card_markers", False)
        ):
            raise ParserError(
                ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
                "Search page content looks like captcha, robot check, or access block",
            )

        if self._looks_like_empty_results(body_text):
            raise ParserError(
                ParserErrorType.EMPTY_RESULTS,
                "Avito search page loaded but reports empty results",
            )

        if not raw_cards and fallback_cards:
            return fallback_cards[: min(per_page_limit, CARD_LIMIT)]
        if not raw_cards:
            self._maybe_dump_layout_changed_html(
                search_url=search_url,
                page_html=page_html,
                title=title,
                body_text=body_text,
                diagnostics=fallback_diag,
            )
            raise ParserError(
                ParserErrorType.LAYOUT_CHANGED,
                "No Avito search result cards found in fetched HTML",
            )

        result: list[ListingCard] = []

        limit = min(per_page_limit, CARD_LIMIT)
        for idx, card in enumerate(raw_cards[:limit]):
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

    def _apply_serp_fallback_diagnostics_to_cycle(self, diagnostics: dict) -> None:
        # Aggregate over the whole parser cycle (all paginated pages):
        # - booleans are sticky OR;
        # - counters accumulate;
        # - layout hint keeps the first non-empty value as the earliest root-cause signal.
        self._cycle_counters.serp_state_fallback_attempted = (
            self._cycle_counters.serp_state_fallback_attempted
            or bool(diagnostics.get("serp_state_fallback_attempted", False))
        )
        self._cycle_counters.serp_state_fallback_succeeded = (
            self._cycle_counters.serp_state_fallback_succeeded
            or bool(diagnostics.get("serp_state_fallback_succeeded", False))
        )
        self._cycle_counters.serp_state_fallback_card_count += int(diagnostics.get("serp_state_fallback_card_count", 0))
        self._cycle_counters.serp_link_fallback_attempted = (
            self._cycle_counters.serp_link_fallback_attempted
            or bool(diagnostics.get("serp_link_fallback_attempted", False))
        )
        self._cycle_counters.serp_link_fallback_succeeded = (
            self._cycle_counters.serp_link_fallback_succeeded
            or bool(diagnostics.get("serp_link_fallback_succeeded", False))
        )
        self._cycle_counters.serp_link_fallback_card_count += int(diagnostics.get("serp_link_fallback_card_count", 0))
        layout_changed_hint = diagnostics.get("layout_changed_hint")
        if layout_changed_hint and (
            self._cycle_counters.layout_changed_hint is None
            or self._cycle_counters.layout_changed_hint == "plain_layout_changed"
        ):
            self._cycle_counters.layout_changed_hint = str(layout_changed_hint)

    def _maybe_dump_layout_changed_html(
        self, search_url: str, page_html: str, title: str, body_text: str, diagnostics: dict | None = None
    ) -> None:
        if not settings.scrape_debug_dump_html:
            return

        html_hash = hashlib.sha256(page_html.encode("utf-8")).hexdigest()
        url_hash = hashlib.sha256(search_url.encode("utf-8")).hexdigest()
        page = self._infer_page_from_url(search_url)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        base_name = f"{timestamp}_{ParserErrorType.LAYOUT_CHANGED.value}_p{page}_{url_hash[:12]}"
        dump_dir = Path(settings.scrape_debug_dump_dir)
        dump_html_path = dump_dir / f"{base_name}.html"
        dump_meta_path = dump_dir / f"{base_name}.json"
        clipped_html = page_html[: max(0, settings.scrape_debug_dump_max_bytes)]

        diagnostics = diagnostics or {}
        metadata = {
            "error_type": ParserErrorType.LAYOUT_CHANGED.value,
            "url_preview": search_url[:300],
            "page": page,
            "html_length": len(page_html),
            "html_sha256": html_hash,
            "title": title,
            "has_data_marker_item": '[data-marker="item"]' in page_html,
            "has_item_title": "item-title" in page_html,
            "has_item_view": "item-view" in page_html,
            "has_hydration_or_initial_data": any(
                marker in page_html
                for marker in (
                    "__initialData__",
                    "__INITIAL_STATE__",
                    "initialData",
                    "initialState",
                    "hydration",
                    "window.__",
                )
            ),
            "looks_like_block_or_captcha": self._looks_like_captcha_or_block(title, body_text),
            "empty_results_detected": self._looks_like_empty_results(body_text),
            "layout_changed_hint": diagnostics.get("layout_changed_hint", "plain_layout_changed"),
            "has_preloaded_state": diagnostics.get("has_preloaded_state", False),
            "has_catalog_items_state": diagnostics.get("has_catalog_items_state", False),
            "catalog_items_candidate_count": diagnostics.get("catalog_items_candidate_count", 0),
            "external_id_candidate_count": diagnostics.get("external_id_candidate_count", 0),
            "state_10_digit_id_candidate_count": diagnostics.get("state_10_digit_id_candidate_count", 0),
            "avito_listing_url_candidate_count": diagnostics.get("avito_listing_url_candidate_count", 0),
            "listing_like_state_object_count": diagnostics.get("listing_like_state_object_count", 0),
            "navigation_like_state_object_count": diagnostics.get("navigation_like_state_object_count", 0),
            "no_listing_payload_detected": diagnostics.get("no_listing_payload_detected", False),
            "has_listing_links_without_card_markers": diagnostics.get("has_listing_links_without_card_markers", False),
            "script_tag_count": diagnostics.get("script_tag_count", 0),
            "body_text_length": diagnostics.get("body_text_length", len(body_text)),
            "serp_state_fallback_attempted": diagnostics.get("serp_state_fallback_attempted", False),
            "serp_state_fallback_succeeded": diagnostics.get("serp_state_fallback_succeeded", False),
            "serp_state_fallback_card_count": diagnostics.get("serp_state_fallback_card_count", 0),
            "serp_link_fallback_attempted": diagnostics.get("serp_link_fallback_attempted", False),
            "serp_link_fallback_succeeded": diagnostics.get("serp_link_fallback_succeeded", False),
            "serp_link_fallback_card_count": diagnostics.get("serp_link_fallback_card_count", 0),
            "dump_html_path": str(dump_html_path),
            "dump_meta_path": str(dump_meta_path),
        }
        try:
            dump_dir.mkdir(parents=True, exist_ok=True)
            dump_html_path.write_text(clipped_html, encoding="utf-8")
            dump_meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.warning(
                "avito_parser.debug_dump saved error_type=%s html_path=%s meta_path=%s",
                ParserErrorType.LAYOUT_CHANGED.value,
                dump_html_path,
                dump_meta_path,
            )
        except Exception as exc:
            logger.warning(
                "avito_parser.debug_dump failed error_type=%s error=%s",
                ParserErrorType.LAYOUT_CHANGED.value,
                exc,
            )

    @staticmethod
    def _infer_page_from_url(search_url: str) -> int:
        parsed = urlparse(search_url)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key == "p":
                try:
                    page = int(value)
                    return page if page > 0 else 1
                except (TypeError, ValueError):
                    return 1
        return 1

    @staticmethod
    def _build_page_url(search_url: str, page: int) -> str:
        if page <= 1:
            return search_url
        parsed = urlparse(search_url)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "p"]
        query.append(("p", str(page)))
        return urlunparse(parsed._replace(query=urlencode(query)))

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

    @classmethod
    def _extract_item_page_publication_label(cls, page_html: str) -> str:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page_html, "lxml")
        marker = soup.select_one('[data-marker="item-view/item-date"]')
        if marker is not None:
            normalized = cls._normalize_text_line(marker.get_text(separator=" ", strip=True))
            if normalized:
                return normalized

        match = re.search(r'"sortFormatedDate"\s*:\s*"((?:\\.|[^"\\])*)"', page_html)
        if match:
            try:
                decoded = json.loads(f'"{match.group(1)}"')
            except json.JSONDecodeError:
                decoded = ""
            label = cls._extract_published_label(str(decoded))
            if label:
                return label

        return cls._extract_published_label(soup.get_text(separator=" ", strip=True))

    async def fetch_item_publication_label(self, item_url: str) -> str:
        page_html = await self._fetch_page_html(item_url)
        return self._extract_item_page_publication_label(page_html)

    async def fetch_item_details(self, item_url: str) -> dict:
        from bs4 import BeautifulSoup

        page_html = await self._fetch_page_html(item_url)
        soup = BeautifulSoup(page_html, "lxml")
        warnings: list[str] = []

        published_label = self._extract_item_page_publication_label(page_html)
        description = self._normalize_text_line(
            (soup.select_one('[data-marker="item-view/item-description"]') or soup.select_one("[itemprop='description']") or {}).get_text(
                separator=" ", strip=True
            ) if (soup.select_one('[data-marker="item-view/item-description"]') or soup.select_one("[itemprop='description']")) else ""
        )
        if len(description) > ITEM_PAGE_DESCRIPTION_MAX:
            description = description[:ITEM_PAGE_DESCRIPTION_MAX].rstrip()
            warnings.append("description_truncated")

        seller_block = soup.select_one('[data-marker="seller-info/name"]') or soup.select_one('[data-marker="seller-info"]')
        seller_name = self._normalize_text_line(
            seller_block.get_text(separator=" ", strip=True) if seller_block is not None else ""
        )
        if len(seller_name) > ITEM_PAGE_SELLER_NAME_MAX:
            seller_name = seller_name[:ITEM_PAGE_SELLER_NAME_MAX].rstrip()
            warnings.append("seller_name_truncated")
        seller_profile_tag = soup.select_one('a[data-marker*="seller-info/name"]') or soup.select_one('a[href*="/user/"]')
        seller_profile_url = ""
        if seller_profile_tag is not None:
            href = str(seller_profile_tag.get("href", "")).strip()
            if href:
                parsed_href = urlparse(href)
                if not parsed_href.netloc:
                    seller_profile_url = urljoin("https://www.avito.ru", href)
                elif parsed_href.netloc.endswith(AVITO_HOST_SUFFIX):
                    seller_profile_url = href
                else:
                    warnings.append("seller_profile_url_external_ignored")

        seller_text = " ".join(
            part for part in (seller_name, soup.get_text(separator=" ", strip=True)[:2000]) if part
        ).lower()
        seller_type = "unknown"
        confidence = "low"
        if any(token in seller_text for token in ("собственник", "частное лицо")):
            seller_type = "owner"
            confidence = "high"
        elif any(token in seller_text for token in ("агентство", "компания", "застройщик")):
            seller_type = "agency"
            confidence = "high"
        elif "профессиональный профиль" in seller_text:
            seller_type = "company"
            confidence = "medium"
        else:
            warnings.append("seller_type_ambiguous")

        address_detail = self._normalize_text_line(
            (soup.select_one('[data-marker="item-view/address"]') or soup.select_one('[data-marker*="address"]') or {}).get_text(
                separator=" ", strip=True
            ) if (soup.select_one('[data-marker="item-view/address"]') or soup.select_one('[data-marker*="address"]')) else ""
        )
        metro = self._normalize_text_line(
            (soup.select_one('[data-marker*="item-metro"]') or soup.select_one('[data-marker*="metro"]') or {}).get_text(
                separator=" ", strip=True
            ) if (soup.select_one('[data-marker*="item-metro"]') or soup.select_one('[data-marker*="metro"]')) else ""
        )
        walking_time_label = self._normalize_text_line(
            (soup.select_one('[data-marker*="walk"]') or {}).get_text(separator=" ", strip=True)
            if soup.select_one('[data-marker*="walk"]') else ""
        )
        badges = [
            self._normalize_text_line(tag.get_text(separator=" ", strip=True))
            for tag in soup.select('[data-marker*="badge"], [class*="badge"]')
            if self._normalize_text_line(tag.get_text(separator=" ", strip=True))
        ][:ITEM_PAGE_BADGES_MAX]
        if len(soup.select('[data-marker*="badge"], [class*="badge"]')) > ITEM_PAGE_BADGES_MAX:
            warnings.append("badges_truncated")

        image_urls = set(re.findall(r"https://[^\s\"'>]*avito\.[a-z.]+/[^\s\"'>]+(?:jpg|jpeg|png|webp)", page_html, re.IGNORECASE))
        image_count = len(image_urls) if image_urls else None
        if image_count is not None and image_count > 100:
            image_count = 100
            warnings.append("image_count_clamped")

        return {
            "source": "item_page",
            "url": item_url,
            "published_label": published_label or "",
            "description": description,
            "seller_name": seller_name,
            "seller_type": seller_type,
            "seller_profile_url": seller_profile_url,
            "address_detail": address_detail,
            "metro": metro,
            "walking_time_label": walking_time_label,
            "badges": badges,
            "image_count": image_count,
            "confidence": confidence,
            "warnings": warnings,
        }

    @staticmethod
    def _extract_external_id(href: str | None, idx: int) -> str:
        if not href:
            return f"unknown-{idx}"

        match = re.search(r"_(\d+)(?:\?|$)", href)
        if match:
            return match.group(1)

        digest = hashlib.sha256(href.encode("utf-8")).hexdigest()[:16]
        return f"fallback-{digest}"

    @staticmethod
    def _extract_catalog_items_from_preloaded_state(page_html: str) -> list[dict]:
        state = AvitoParser._extract_preloaded_state(page_html)
        if not isinstance(state, dict):
            return []
        items = (((state or {}).get("data") or {}).get("catalog") or {}).get("items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @staticmethod
    def _extract_preloaded_state(page_html: str) -> dict | None:
        match = re.search(r"window\.__preloadedState__\s*=\s*\"((?:\\.|[^\"\\])*)\"", page_html)
        if not match:
            return None
        try:
            encoded = json.loads(f"\"{match.group(1)}\"")
            decoded = unquote(encoded)
            state = json.loads(decoded)
        except (json.JSONDecodeError, ValueError):
            return None
        return state if isinstance(state, dict) else None

    @classmethod
    def _analyze_preloaded_state_candidates(cls, state: dict | None) -> dict:
        if not isinstance(state, dict):
            return {
                "candidate_state_paths": [],
                "candidate_object_key_sets": [],
                "ignored_candidate_object_key_sets": [],
                "listing_like_state_object_count": 0,
                "navigation_like_state_object_count": 0,
                "state_10_digit_id_candidate_count_in_state": 0,
                "external_id_candidate_count_in_state": 0,
            }
        listing_paths: list[str] = []
        listing_key_sets: list[str] = []
        ignored_key_sets: list[str] = []
        listing_like_count = 0
        navigation_like_count = 0
        state_10_digit_ids: set[str] = set()
        listing_external_ids: set[str] = set()
        remaining_nodes = MAX_PRELOADED_STATE_SCAN_NODES

        def walk(node: object, path: str, depth: int) -> None:
            nonlocal remaining_nodes
            nonlocal listing_like_count
            nonlocal navigation_like_count
            if depth > MAX_PRELOADED_STATE_SCAN_DEPTH or remaining_nodes <= 0:
                return
            remaining_nodes -= 1
            if isinstance(node, dict):
                keys = {str(k) for k in node.keys()}
                has_title = any(k in keys for k in ("title", "name"))
                has_url = any(k in keys for k in ("url", "href", "urlPath"))
                url_raw = node.get("url") or node.get("href") or node.get("urlPath")
                url_candidate = cls._normalize_text_line(str(url_raw)) if isinstance(url_raw, str) else ""
                normalized_url, url_id = cls._normalize_and_validate_recursive_fallback_url(url_candidate)

                ext = node.get("externalId") or node.get("itemId") or node.get("id")
                ext_id = str(ext).strip() if isinstance(ext, (int, str)) else ""
                ext_id_valid = ext_id if re.fullmatch(r"\d{10}", ext_id) else ""
                resolved_listing_id = ""
                if ext_id_valid and url_id and ext_id_valid == url_id:
                    resolved_listing_id = ext_id_valid
                elif ext_id_valid and not url_id:
                    resolved_listing_id = ""
                elif url_id and not ext_id_valid:
                    resolved_listing_id = url_id

                is_listing_like = bool(has_title and has_url and normalized_url and resolved_listing_id)
                if is_listing_like:
                    listing_like_count += 1
                    listing_external_ids.add(resolved_listing_id)
                    if len(listing_paths) < MAX_DIAGNOSTIC_STATE_PATHS:
                        listing_paths.append(path[:120])
                    if len(listing_key_sets) < MAX_DIAGNOSTIC_OBJECT_KEY_SETS:
                        listing_key_sets.append(",".join(sorted(keys))[:160])
                elif has_title and has_url and (
                    "categorytree" in path.lower()
                    or "categoryid" in keys
                    or "subs" in keys
                ):
                    navigation_like_count += 1
                    if len(ignored_key_sets) < MAX_DIAGNOSTIC_IGNORED_OBJECT_KEY_SETS:
                        ignored_key_sets.append(",".join(sorted(keys))[:160])

                for id_key in ("id", "itemId", "externalId"):
                    id_value = node.get(id_key)
                    if isinstance(id_value, int):
                        candidate = str(id_value)
                    elif isinstance(id_value, str):
                        candidate = id_value.strip()
                    else:
                        continue
                    if re.fullmatch(r"\d{10}", candidate):
                        state_10_digit_ids.add(candidate)
                for key, value in node.items():
                    if isinstance(key, str):
                        next_path = f"{path}.{key}" if path else key
                    else:
                        next_path = f"{path}.[key]" if path else "[key]"
                    walk(value, next_path, depth + 1)
                return
            if isinstance(node, list):
                for idx, value in enumerate(node[:250]):
                    if remaining_nodes <= 0:
                        break
                    walk(value, f"{path}[{idx}]", depth + 1)

        walk(state, "", 0)
        return {
            "candidate_state_paths": list(dict.fromkeys(listing_paths)),
            "candidate_object_key_sets": list(dict.fromkeys(listing_key_sets)),
            "ignored_candidate_object_key_sets": list(dict.fromkeys(ignored_key_sets)),
            "listing_like_state_object_count": listing_like_count,
            "navigation_like_state_object_count": navigation_like_count,
            "state_10_digit_id_candidate_count_in_state": len(state_10_digit_ids),
            "external_id_candidate_count_in_state": len(listing_external_ids),
        }

    @classmethod
    def _extract_cards_from_preloaded_state_candidates(cls, state: dict | None) -> list[ListingCard]:
        if not isinstance(state, dict):
            return []
        cards: list[ListingCard] = []
        seen: set[str] = set()
        remaining_nodes = MAX_PRELOADED_STATE_SCAN_NODES

        def _resolve_valid_external_id(ext_candidate: object, normalized_url: str, url_id: str) -> str | None:
            ext_id = str(ext_candidate).strip() if isinstance(ext_candidate, (int, str)) else ""
            ext_id_valid = ext_id if re.fullmatch(r"\d{10}", ext_id) else ""
            if ext_id_valid and url_id and ext_id_valid != url_id:
                return None
            if ext_id_valid:
                return ext_id_valid
            if url_id:
                return url_id
            return None

        def walk(node: object, depth: int) -> None:
            nonlocal remaining_nodes
            if len(cards) >= CARD_LIMIT or depth > MAX_PRELOADED_STATE_SCAN_DEPTH or remaining_nodes <= 0:
                return
            remaining_nodes -= 1
            if isinstance(node, dict):
                ext = node.get("externalId") or node.get("itemId") or node.get("id")
                title_raw = node.get("title") or node.get("name")
                title = cls._normalize_text_line(str(title_raw)) if isinstance(title_raw, str) else ""
                url_raw = node.get("url") or node.get("href") or node.get("urlPath")
                url_candidate = cls._normalize_text_line(str(url_raw)) if isinstance(url_raw, str) else ""
                normalized_url, url_id = cls._normalize_and_validate_recursive_fallback_url(url_candidate)
                ext_id = _resolve_valid_external_id(ext, normalized_url, url_id)
                price_raw = node.get("price")
                if isinstance(price_raw, dict):
                    price_raw = price_raw.get("value")
                reliable_url = bool(normalized_url and url_id)
                has_minimal = bool(ext_id and title and reliable_url)
                has_extended = bool(ext_id and title and isinstance(price_raw, (int, float)) and reliable_url)
                if (has_minimal or has_extended) and ext_id not in seen:
                    seen.add(ext_id)
                    cards.append(ListingCard(
                        external_id=ext_id,
                        url=normalized_url,
                        title=title,
                        price=float(price_raw) if isinstance(price_raw, (int, float)) else None,
                        address=cls._normalize_text_line(str(node.get("address") or node.get("location") or "")),
                        area_m2=cls._extract_area_m2(title),
                        rooms=cls._extract_rooms(title),
                        published_label="",
                        published_at=None,
                        raw={"source": "serp_preloaded_state_recursive"},
                    ))
                for value in node.values():
                    if len(cards) >= CARD_LIMIT or remaining_nodes <= 0:
                        break
                    walk(value, depth + 1)
            elif isinstance(node, list):
                for value in node[:250]:
                    if len(cards) >= CARD_LIMIT or remaining_nodes <= 0:
                        break
                    walk(value, depth + 1)

        walk(state, 0)
        return cards

    @staticmethod
    def _normalize_listing_url(url_path: str) -> str:
        return urljoin("https://www.avito.ru", url_path)

    @classmethod
    def _normalize_and_validate_recursive_fallback_url(cls, url_value: str) -> tuple[str, str]:
        if not url_value:
            return "", ""
        normalized_url = cls._normalize_listing_url(url_value) if url_value.startswith("/") else url_value
        parsed = urlparse(normalized_url)
        host = (parsed.hostname or "").lower()
        if not (host == AVITO_HOST_SUFFIX or host.endswith(f".{AVITO_HOST_SUFFIX}")):
            return "", ""
        match = AVITO_LISTING_URL_PATH_RE.match(parsed.path or "")
        if not match:
            return "", ""
        return normalized_url, match.group(1)

    @classmethod
    def _extract_catalog_item_address(cls, item: dict) -> str:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        geo = payload.get("geoForItems") if isinstance(payload.get("geoForItems"), dict) else {}
        parts = []
        formatted = geo.get("formattedAddress")
        if isinstance(formatted, str) and formatted.strip():
            parts.append(cls._normalize_text_line(formatted))
        refs = geo.get("geoReferences")
        if isinstance(refs, list):
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                for key in ("content", "after", "afterWithIcon"):
                    value = ref.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(cls._normalize_text_line(value))
        parts = [p for p in parts if p]
        if parts:
            return ", ".join(dict.fromkeys(parts))
        detailed = item.get("addressDetailed") if isinstance(item.get("addressDetailed"), dict) else {}
        location = detailed.get("locationName")
        return cls._normalize_text_line(location) if isinstance(location, str) else ""

    @classmethod
    def _extract_cards_from_catalog_items(cls, page_html: str) -> list[ListingCard]:
        result: list[ListingCard] = []
        seen: set[str] = set()
        for item in cls._extract_catalog_items_from_preloaded_state(page_html):
            if item.get("type") not in (None, "item"):
                continue
            item_id = item.get("id")
            url_path = item.get("urlPath")
            if not item_id or not isinstance(url_path, str) or not AVITO_LISTING_URL_PATH_RE.match(url_path):
                continue
            ext_id = str(item_id)
            if ext_id in seen:
                continue
            seen.add(ext_id)
            title = cls._normalize_text_line(str(item.get("title") or ""))
            price_data = item.get("priceDetailed") if isinstance(item.get("priceDetailed"), dict) else {}
            price = price_data.get("value")
            published_ts = item.get("sortTimeStamp") if item.get("sortTimeStamp") is not None else item.get("allowTimeStamp")
            published_at = cls._parse_catalog_timestamp(published_ts)
            result.append(ListingCard(
                external_id=ext_id,
                url=cls._normalize_listing_url(url_path),
                title=title,
                price=float(price) if isinstance(price, (int, float)) else None,
                address=cls._extract_catalog_item_address(item),
                area_m2=cls._extract_area_m2(title),
                rooms=cls._extract_rooms(title),
                published_label="",
                published_at=published_at,
                raw={"source": "serp_preloaded_state"},
            ))
        return result

    @classmethod
    def _parse_catalog_timestamp(cls, value: object) -> datetime | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value < 0:
            return None
        ts = float(value)
        if ts >= 10_000_000_000:
            ts = ts / 1000.0
        elif ts < 1_000_000_000:
            return None
        try:
            parsed = datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        if parsed > now_utc + timedelta(days=MAX_FUTURE_PUBLISHED_AT_DAYS):
            return None
        return parsed

    @classmethod
    def _extract_avito_listing_urls(cls, page_html: str) -> set[str]:
        return {
            match.group(0)
            for match in re.finditer(r"/[a-z0-9_-]+/kvartiry/[^\\s\"'<>]+_\\d{10}(?:\\?[^\\s\"'<>]*)?", page_html, re.IGNORECASE)
            if AVITO_LISTING_URL_PATH_RE.match(match.group(0))
        }

    @classmethod
    def _extract_cards_from_listing_links(cls, page_html: str) -> list[ListingCard]:
        result: list[ListingCard] = []
        for idx, path in enumerate(sorted(cls._extract_avito_listing_urls(page_html))):
            match = AVITO_LISTING_URL_PATH_RE.match(path)
            if not match:
                continue
            result.append(ListingCard(
                external_id=match.group(1),
                url=cls._normalize_listing_url(path),
                title="",
                price=None,
                address="",
                area_m2=None,
                rooms="",
                published_label="",
                published_at=None,
                raw={"source": "serp_listing_links", "position": idx},
            ))
        return result

    @classmethod
    def _build_fallback_diagnostics(cls, soup, page_html: str) -> dict:
        avito_urls = cls._extract_avito_listing_urls(page_html)
        state = cls._extract_preloaded_state(page_html)
        catalog_items = cls._extract_catalog_items_from_preloaded_state(page_html)
        state_diag = cls._analyze_preloaded_state_candidates(state)
        html_external_id_count = len({m.group(1) for m in re.finditer(r"_(\d{10})(?:\\?|\"|$)", page_html)})
        return {
            "serp_state_fallback_attempted": False,
            "serp_state_fallback_succeeded": False,
            "serp_state_fallback_card_count": 0,
            "serp_link_fallback_attempted": False,
            "serp_link_fallback_succeeded": False,
            "serp_link_fallback_card_count": 0,
            "has_preloaded_state": "window.__preloadedState__" in page_html,
            "has_catalog_items_state": bool(catalog_items),
            "catalog_items_candidate_count": len(catalog_items),
            "external_id_candidate_count": state_diag["external_id_candidate_count_in_state"],
            "state_10_digit_id_candidate_count": max(
                html_external_id_count,
                state_diag["state_10_digit_id_candidate_count_in_state"],
            ),
            "avito_listing_url_candidate_count": len(avito_urls),
            "candidate_state_paths": state_diag["candidate_state_paths"],
            "candidate_object_key_sets": state_diag["candidate_object_key_sets"],
            "ignored_candidate_object_key_sets": state_diag["ignored_candidate_object_key_sets"],
            "listing_like_state_object_count": state_diag["listing_like_state_object_count"],
            "navigation_like_state_object_count": state_diag["navigation_like_state_object_count"],
            "has_listing_links_without_card_markers": bool(avito_urls),
            "script_tag_count": len(soup.find_all("script")),
            "body_text_length": len(soup.get_text(separator=" ", strip=True)),
            "layout_changed_hint": "plain_layout_changed",
        }

    @classmethod
    def _parse_cards_from_serp_fallback(cls, soup, page_html: str, diagnostics: dict) -> list[ListingCard]:
        diagnostics["serp_state_fallback_attempted"] = True
        state_cards = cls._extract_cards_from_catalog_items(page_html)
        if not state_cards:
            state_cards = cls._extract_cards_from_preloaded_state_candidates(cls._extract_preloaded_state(page_html))
        diagnostics["serp_state_fallback_card_count"] = len(state_cards)
        diagnostics["serp_state_fallback_succeeded"] = bool(state_cards)
        if state_cards:
            diagnostics["layout_changed_hint"] = "preloaded_state_with_listing_items"
            return state_cards
        diagnostics["serp_link_fallback_attempted"] = True
        link_cards = cls._extract_cards_from_listing_links(page_html)
        diagnostics["serp_link_fallback_card_count"] = len(link_cards)
        diagnostics["serp_link_fallback_succeeded"] = bool(link_cards)
        if link_cards:
            diagnostics["layout_changed_hint"] = "listing_links_without_card_markers"
            return link_cards
        if (
            diagnostics["has_preloaded_state"]
            and not diagnostics["has_catalog_items_state"]
            and not diagnostics.get("has_data_marker_item", False)
            and diagnostics.get("listing_like_state_object_count", 0) == 0
            and diagnostics.get("navigation_like_state_object_count", 0) > 0
        ):
            diagnostics["layout_changed_hint"] = "hydration_without_listing_payload"
            diagnostics["no_listing_payload_detected"] = True
        elif (
            diagnostics["has_preloaded_state"]
            and not diagnostics["has_catalog_items_state"]
            and not diagnostics.get("has_data_marker_item", False)
            and diagnostics["external_id_candidate_count"] > 0
        ):
            diagnostics["layout_changed_hint"] = "hydration_without_cards_without_catalog_items"
        elif diagnostics["has_preloaded_state"]:
            diagnostics["layout_changed_hint"] = "hydration_without_cards"
        elif cls._looks_like_empty_results(soup.get_text(separator=" ", strip=True)):
            diagnostics["layout_changed_hint"] = "empty_results"
        return []
