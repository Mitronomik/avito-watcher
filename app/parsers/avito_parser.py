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
PUBLICATION_MARKER_SELECTORS = (
    '[data-marker*="item-date"]',
    '[data-marker*="date"]',
    '[data-marker*="time"]',
)
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
    proxy_success_count: int = 0
    proxy_failure_count: int = 0
    engine_skip_recent_failure_count: int = 0
    preferred_engine: str | None = None
    selected_first_engine: str | None = None
    engine_selection_changed_by_health_memory: bool = False
    fallback_used: bool = False

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
            "proxy_success_count": self.proxy_success_count,
            "proxy_failure_count": self.proxy_failure_count,
            "engine_skip_recent_failure_count": self.engine_skip_recent_failure_count,
            "preferred_engine": self.preferred_engine,
            "selected_first_engine": self.selected_first_engine,
            "engine_selection_changed_by_health_memory": self.engine_selection_changed_by_health_memory,
            "fallback_used": self.fallback_used,
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

        # First attempt
        start_engine = self._choose_start_engine(proxy_url)
        setup_error = await self.ensure_engine_session(start_engine, proxy_url)
        result = setup_error or await self._try_engine(url, proxy_url, start_engine)
        self._record_engine_result(start_engine, proxy_url, result)
        if result["ok"]:
            if proxy_url and self._proxy_manager:
                self._proxy_manager.report_success(proxy_url)
                self._cycle_counters.proxy_success_count += 1
            self._cycle_counters.engine_used = start_engine.value
            return result["html"]

        allowed_engines = self._allowed_engines()
        fallback = next((engine for engine in allowed_engines if engine != start_engine), None)
        _log.warning(
            "avito_parser.engine_failure engine=%s error_type=%s allowed_engines=%s fallback_available=%s",
            start_engine.value,
            result.get("error_type"),
            ",".join(engine.value for engine in allowed_engines),
            bool(fallback),
        )
        if result.get("error_type") == "possible_captcha_or_block":
            self._cycle_counters.block_detected_count += 1
        else:
            self._cycle_counters.engine_error_count += 1
        if proxy_url and self._proxy_manager:
            self._proxy_manager.report_failure(proxy_url)
            self._cycle_counters.proxy_failure_count += 1
            proxy_url = self._proxy_manager.get_proxy()

        if fallback is None:
            raise ParserError(
                ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
                f"Stealth engine blocked ({start_engine.value})",
            )

        # Fallback attempt
        self._cycle_counters.engine_fallback_count += 1
        self._cycle_counters.fallback_used = True
        setup_error2 = await self.ensure_engine_session(fallback, proxy_url)
        result2 = setup_error2 or await self._try_engine(url, proxy_url, fallback)
        self._record_engine_result(fallback, proxy_url, result2)
        if result2["ok"]:
            if proxy_url and self._proxy_manager:
                self._proxy_manager.report_success(proxy_url)
                self._cycle_counters.proxy_success_count += 1
            self._prefer_engine = fallback
            self._cycle_counters.engine_used = fallback.value
            return result2["html"]

        if proxy_url and self._proxy_manager:
            self._proxy_manager.report_failure(proxy_url)
            self._cycle_counters.proxy_failure_count += 1

        raise ParserError(
            ParserErrorType.POSSIBLE_CAPTCHA_OR_BLOCK,
            "All stealth engines blocked (nodriver + camoufox)",
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
        fallback_diag = self._build_fallback_diagnostics(soup=soup, page_html=page_html)
        if not raw_cards:
            fallback_cards = self._parse_cards_from_serp_fallback(
                soup=soup, page_html=page_html, diagnostics=fallback_diag
            )

        if self._looks_like_captcha_or_block(title, body_text) and not (
            fallback_diag["has_catalog_items_state"] or fallback_diag["has_listing_links_without_card_markers"]
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
            "avito_listing_url_candidate_count": diagnostics.get("avito_listing_url_candidate_count", 0),
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
        match = re.search(r"window\.__preloadedState__\s*=\s*\"((?:\\.|[^\"\\])*)\"", page_html)
        if not match:
            return []
        try:
            encoded = json.loads(f"\"{match.group(1)}\"")
            decoded = unquote(encoded)
            state = json.loads(decoded)
        except (json.JSONDecodeError, ValueError):
            return []
        items = (((state or {}).get("data") or {}).get("catalog") or {}).get("items")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @staticmethod
    def _normalize_listing_url(url_path: str) -> str:
        return urljoin("https://www.avito.ru", url_path)

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
        catalog_items = cls._extract_catalog_items_from_preloaded_state(page_html)
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
            "external_id_candidate_count": len({m.group(1) for m in re.finditer(r"_(\d{10})(?:\\?|\"|$)", page_html)}),
            "avito_listing_url_candidate_count": len(avito_urls),
            "has_listing_links_without_card_markers": bool(avito_urls),
            "script_tag_count": len(soup.find_all("script")),
            "body_text_length": len(soup.get_text(separator=" ", strip=True)),
            "layout_changed_hint": "plain_layout_changed",
        }

    @classmethod
    def _parse_cards_from_serp_fallback(cls, soup, page_html: str, diagnostics: dict) -> list[ListingCard]:
        diagnostics["serp_state_fallback_attempted"] = True
        state_cards = cls._extract_cards_from_catalog_items(page_html)
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
        if diagnostics["has_preloaded_state"]:
            diagnostics["layout_changed_hint"] = "hydration_without_cards"
        elif cls._looks_like_empty_results(soup.get_text(separator=" ", strip=True)):
            diagnostics["layout_changed_hint"] = "empty_results"
        return []
