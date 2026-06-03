import asyncio
import importlib.metadata
import logging
import platform
import random
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.agents.llm_providers import resolve_llm_runtime_config
from app.agents.scorer import ListingScorer
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.search_job import SearchJob
from app.models.listing_snapshot import ListingSnapshot
from app.notifiers.composite import CompositeNotifier
from app.notifiers.email import EmailNotifier
from app.notifiers.google_sheets_webhook import GoogleSheetsWebhookNotifier
from app.notifiers.jsonl_outbox import JsonlOutboxNotifier
from app.notifiers.telegram import TelegramNotifier
from app.parsers.avito_parser import AvitoParser
from app.parsers.schemas import ListingCard
from app.repositories.alert_repository import AlertRepository
from app.repositories.listing_repository import ListingRepository
from app.repositories.search_repository import SearchRepository
from app.utils.formatting import build_listing_message

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
logger = logging.getLogger(__name__)

PARSER_DIAGNOSTIC_KEYS = (
    "preferred_engine",
    "selected_first_engine",
    "engine_selection_changed_by_health_memory",
    "fallback_used",
    "engine_used",
    "engine_fallback_count",
    "engine_skip_recent_failure_count",
    "block_detected_count",
    "engine_error_count",
    "timeout_failure_count",
    "timeout_retry_attempt_count",
    "timeout_retry_success_count",
    "browser_driver_crash_count",
    "browser_driver_crash_retry_attempt_count",
    "browser_driver_crash_retry_success_count",
    "close_failure_after_driver_crash_count",
    "proxy_success_count",
    "proxy_failure_count",
    "proxy_quarantine_on_failure_count",
    "session_open_count",
    "session_reuse_count",
    "session_evict_count",
    "session_close_failure_count",
    "serp_state_fallback_attempted",
    "serp_state_fallback_succeeded",
    "serp_state_fallback_card_count",
    "serp_link_fallback_attempted",
    "serp_link_fallback_succeeded",
    "serp_link_fallback_card_count",
    "layout_changed_hint",
)


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_keywords(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return []


def _optional_package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except Exception:
        return None


def runtime_diagnostics() -> dict:
    alert_channels = [
        item.strip().lower()
        for item in settings.alert_channels.split(",")
        if item.strip()
    ]
    llm_cfg = resolve_llm_runtime_config()
    if llm_cfg.provider == "off":
        llm_model_set = False
        llm_base_url_set = False
    elif llm_cfg.provider == "openai_compatible":
        llm_model_set = bool(settings.llm_model)
        llm_base_url_set = bool(settings.llm_base_url)
    else:
        llm_model_set = bool(llm_cfg.model)
        llm_base_url_set = bool(llm_cfg.base_url)

    return {
        "alert_channels": alert_channels,
        "scoring_enabled": settings.scoring_enabled,
        "llm_provider": llm_cfg.provider,
        "llm_model_set": llm_model_set,
        "llm_base_url_set": llm_base_url_set,
        "llm_api_key_set": bool(settings.llm_api_key),
        "llm_timeout_sec": llm_cfg.timeout_sec,
        "llm_max_retries": llm_cfg.max_retries,
        "llm_shadow_mode": settings.llm_shadow_mode,
        "llm_prompt_version": llm_cfg.prompt_version,
        "python_version": platform.python_version(),
        "playwright_version": _optional_package_version("playwright"),
        "camoufox_version": _optional_package_version("camoufox"),
        "scrape_preferred_engine": settings.scrape_preferred_engine,
        "scrape_allowed_engines": settings.scrape_allowed_engines,
        "scrape_camoufox_retry_on_driver_crash": settings.scrape_camoufox_retry_on_driver_crash,
        "scrape_timeout_retry_once": settings.scrape_timeout_retry_once,
        "scrape_timeout_retry_delay_ms": settings.scrape_timeout_retry_delay_ms,
        "scrape_headless": settings.scrape_headless,
        "scrape_max_pages": settings.scrape_max_pages,
        "scrape_cards_per_page_limit": settings.scrape_cards_per_page_limit,
        "scrape_stop_on_duplicate_page": settings.scrape_stop_on_duplicate_page,
        "scrape_page_delay_ms": settings.scrape_page_delay_ms,
        "scrape_page_jitter_ms": settings.scrape_page_jitter_ms,
        "scrape_enrich_missing_published_at": settings.scrape_enrich_missing_published_at,
        "scrape_enrich_item_page_details": settings.scrape_enrich_item_page_details,
        "scrape_item_page_delay_ms": settings.scrape_item_page_delay_ms,
        "scrape_item_page_jitter_ms": settings.scrape_item_page_jitter_ms,
        "scrape_item_page_limit_per_run": settings.scrape_item_page_limit_per_run,
        "proxy_urls_set": bool(settings.proxy_urls.strip()),
        "scrape_debug_dump_html": settings.scrape_debug_dump_html,
        "scrape_debug_dump_dir": settings.scrape_debug_dump_dir,
        "scrape_debug_dump_max_bytes": settings.scrape_debug_dump_max_bytes,
    }


def passes_rule_filters(card: ListingCard, filters: dict | None) -> bool:
    return not explain_rule_filter_failures(card, filters)


def explain_rule_filter_failures(card: ListingCard, filters: dict | None) -> list[str]:
    filters = filters or {}
    failures: list[str] = []

    min_price = _as_float(filters.get("min_price"))
    if min_price is not None and (card.price is None or card.price < min_price):
        failures.append("min_price")

    max_price = _as_float(filters.get("max_price"))
    if max_price is not None and (card.price is None or card.price > max_price):
        failures.append("max_price")

    min_area = _as_float(filters.get("min_area") or filters.get("min_area_m2"))
    if min_area is not None and (card.area_m2 is None or card.area_m2 < min_area):
        failures.append("min_area")

    max_area = _as_float(filters.get("max_area") or filters.get("max_area_m2"))
    if max_area is not None and (card.area_m2 is None or card.area_m2 > max_area):
        failures.append("max_area")

    text = " ".join(
        part
        for part in (
            card.title,
            card.address,
            str(card.raw.get("text", "")),
            str(card.raw.get("description", "")),
        )
        if part
    ).lower()

    include_keywords = _as_keywords(filters.get("include_keywords"))
    if include_keywords and not any(keyword in text for keyword in include_keywords):
        failures.append("include_keywords")

    exclude_keywords = _as_keywords(filters.get("exclude_keywords"))
    if exclude_keywords and any(keyword in text for keyword in exclude_keywords):
        failures.append("exclude_keywords")

    location_text = " ".join(
        part
        for part in (
            card.address,
            str(card.raw.get("address", "")),
            str(card.raw.get("location", "")),
        )
        if part
    ).lower()
    location_keywords = _as_keywords(filters.get("location_keywords"))
    if location_keywords and not any(
        keyword in location_text for keyword in location_keywords
    ):
        failures.append("location_keywords")

    return failures




def _filtered_sample(
    card: ListingCard,
    reason: str,
    rule_failures: list[str] | None = None,
    publication_date_failures: list[str] | None = None,
    publication_date_warnings: list[str] | None = None,
) -> dict:
    sample = {
        "external_id": card.external_id,
        "title": card.title,
        "price": card.price,
        "area_m2": card.area_m2,
        "address": card.address,
        "published_label": card.published_label,
        "url": card.url,
        "reason": reason,
    }
    if rule_failures:
        sample["rule_failures"] = rule_failures
    if publication_date_failures:
        sample["publication_date_failures"] = publication_date_failures
    if publication_date_warnings:
        sample["publication_date_warnings"] = publication_date_warnings
    return sample

def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return _as_utc_naive(datetime.fromisoformat(text))
    except ValueError:
        return None


def _missing_published_at_policy(filters: dict | None) -> str:
    if not isinstance(filters, dict):
        return "reject"
    policy = filters.get("missing_published_at_policy")
    if policy in {"reject", "allow", "allow_when_date_sorted"}:
        return policy
    return "reject"


def _is_missing_published_at_allowed(filters: dict | None) -> bool:
    policy = _missing_published_at_policy(filters)
    if policy == "allow":
        return True
    if policy != "allow_when_date_sorted":
        return False
    if not isinstance(filters, dict):
        return False
    return filters.get("source_sort") == "date"


def passes_publication_filters(
    card: ListingCard,
    filters: dict | None,
    now: datetime | None = None,
) -> bool:
    return not explain_publication_filter_failures(card, filters, now)


def explain_publication_filter_failures(
    card: ListingCard,
    filters: dict | None,
    now: datetime | None = None,
    include_non_blocking: bool = False,
) -> list[str]:
    filters = filters or {}
    failures: list[str] = []
    published_at = card.published_at

    if (
        filters.get("require_published_at") is True
        and published_at is None
        and not _is_missing_published_at_allowed(filters)
    ):
        failures.append("missing_published_at")

    if published_at is None:
        return failures

    published_at = _as_utc_naive(published_at)
    now = _as_utc_naive(now or _utcnow())

    max_age_hours = _as_float(filters.get("max_age_hours"))
    if max_age_hours is not None and published_at < now - timedelta(hours=max_age_hours):
        failures.append("older_than_max_age_hours")

    published_after = _parse_iso_datetime(filters.get("published_after"))
    if published_after is not None and published_at <= published_after:
        failures.append("before_or_equal_published_after")

    published_on_date = filters.get("published_on_date")
    if isinstance(published_on_date, str) and published_on_date.strip():
        try:
            expected_date = datetime.strptime(
                published_on_date.strip(), "%Y-%m-%d"
            ).date()
        except ValueError:
            expected_date = None
            if include_non_blocking:
                failures.append("invalid_published_on_date")
        if expected_date is not None:
            # Publication dates are stored as naive UTC; convert to Moscow local date for date-only filters.
            local_date = published_at.replace(tzinfo=UTC).astimezone(MOSCOW_TZ).date()
            if local_date != expected_date:
                failures.append("published_on_date_mismatch")

    return failures


def _source_url_preview(url: str, limit: int = 220) -> str:
    safe_url = (url or "").strip()
    return safe_url[:limit]


class MonitorService:
    def __init__(
        self,
        parser: AvitoParser | None = None,
        scorer: ListingScorer | None = None,
        notifier: CompositeNotifier | None = None,
        now_func=None,
    ) -> None:
        self.parser = parser or AvitoParser()
        self.scorer = scorer or ListingScorer()
        self.notifier = notifier or self._build_notifier()
        self.now_func = now_func or _utcnow
        self._publication_enrichment_cache: dict[str, tuple[str, datetime]] = {}
        self._item_page_details_cache: dict[str, dict] = {}

    def _now(self) -> datetime:
        return _as_utc_naive(self.now_func())

    def _build_notifier(self) -> CompositeNotifier:
        configured = [item.strip().lower() for item in settings.alert_channels.split(",") if item.strip()]
        channels = []
        for name in configured:
            if name == "telegram":
                channels.append(TelegramNotifier())
            elif name == "email":
                channels.append(EmailNotifier())
            elif name == "jsonl":
                channels.append(JsonlOutboxNotifier())
            elif name == "google_sheets":
                channels.append(GoogleSheetsWebhookNotifier())
        return CompositeNotifier(channels)

    def _parser_stats_snapshot(self) -> dict:
        cycle_stats_fn = getattr(self.parser, "cycle_stats", None)
        if not callable(cycle_stats_fn):
            return {}
        stats = cycle_stats_fn()
        if not isinstance(stats, dict):
            return {}
        return {key: stats.get(key) for key in PARSER_DIAGNOSTIC_KEYS}


    def _build_alert_payload(
        self,
        card: ListingCard,
        search_name: str,
        summary: str,
        score,
        tags: list,
    ) -> dict:
        return {
            "search_name": search_name,
            "external_id": card.external_id,
            "title": card.title,
            "price": card.price,
            "area_m2": card.area_m2,
            "rooms": card.rooms,
            "address": card.address,
            "published_label": card.published_label,
            "published_at": card.published_at.isoformat() if card.published_at else None,
            "url": card.url,
            "summary": summary,
            "score": score,
            "tags": tags,
        }

    def _retry_context_from_snapshot(
        self, db: Session, card: ListingCard, search_name: str
    ) -> tuple[str, dict] | None:
        snapshots = (
            db.query(ListingSnapshot)
            .filter(ListingSnapshot.external_id == card.external_id)
            .order_by(ListingSnapshot.id.desc())
            .all()
        )
        llm = None
        for snapshot in snapshots:
            if not isinstance(snapshot.payload_json, dict):
                continue
            candidate = snapshot.payload_json.get("llm_score")
            if isinstance(candidate, dict):
                llm = candidate
                break
        if llm is None:
            return None

        use_llm = not settings.llm_shadow_mode and llm.get("status") == "success"
        summary = llm.get("summary", "") if use_llm else ""
        payload = self._build_alert_payload(
            card=card,
            search_name=search_name,
            summary=summary,
            score=llm.get("score") if use_llm else None,
            tags=llm.get("tags", []) if use_llm else [],
        )
        message = build_listing_message(
            {
                "title": card.title,
                "price": card.price,
                "address": card.address,
                "area_m2": card.area_m2,
                "rooms": card.rooms,
                "published_label": card.published_label,
                "url": card.url,
            },
            summary,
        )
        return message, payload

    async def _deliver_pending_alerts(
        self,
        alert_repo: AlertRepository,
        card: ListingCard,
        message: str,
        payload: dict,
    ) -> dict[str, list[str]]:
        notifier_channels = getattr(self.notifier, "channels", [self.notifier])
        channel_names = [ch.channel_name for ch in notifier_channels]
        pending_channels = []
        for channel_name in channel_names:
            dedupe_key = f"{channel_name}:new:{card.external_id}"
            if not alert_repo.exists_by_dedupe_key(dedupe_key):
                pending_channels.append(channel_name)

        if not pending_channels:
            return {
                "attempted": [],
                "successful": [],
                "skipped": [],
                "failed": [],
                "unknown": [],
            }

        channels = getattr(self.notifier, "channels", [self.notifier])
        if all(hasattr(channel, "send_listing_alert") for channel in channels):
            channel_map = {
                channel.channel_name: channel
                for channel in channels
                if channel.channel_name in pending_channels
            }
            successful = []
            skipped = []
            failed = []
            unknown = []
            for channel_name in pending_channels:
                channel = channel_map.get(channel_name)
                if channel is None:
                    unknown.append(channel_name)
                    continue
                try:
                    delivered = await channel.send_listing_alert(message, payload)
                except Exception:
                    logger.exception("Alert channel failed", extra={"channel": channel_name})
                    failed.append(channel_name)
                    continue
                if delivered is True:
                    successful.append(channel_name)
                    continue
                if delivered is False:
                    skipped.append(channel_name)
                    continue
                logger.debug(
                    "Alert channel returned unexpected delivery result type",
                    extra={"channel": channel_name, "result_type": type(delivered).__name__},
                )
                unknown.append(channel_name)
        else:
            skipped = []
            failed = []
            unknown = []
            try:
                successful = await self.notifier.send_listing_alert(message, payload)
            except Exception:
                logger.exception("Alert notifier failed")
                successful = []
                failed = list(pending_channels)
            successful = [name for name in successful if name in pending_channels]
            if not failed:
                successful_set = set(successful)
                unknown = [name for name in pending_channels if name not in successful_set]
        for channel_name in successful:
            alert_repo.create(
                listing_external_id=card.external_id,
                dedupe_key=f"{channel_name}:new:{card.external_id}",
                channel=channel_name,
            )

        return {
            "attempted": pending_channels,
            "successful": successful,
            "skipped": skipped,
            "failed": failed,
            "unknown": unknown,
        }

    async def process_search(self, db: Session, search: SearchJob) -> dict:
        search_repo = SearchRepository(db)
        checked_at = self._now()
        baseline_run = not search.baseline_initialized
        started_at = time.perf_counter()

        try:
            pagination = None
            if hasattr(self.parser, "fetch_search_cards_paginated"):
                pagination = await self.parser.fetch_search_cards_paginated(search.source_url)
                cards = pagination["cards"]
            else:
                cards = await self.parser.fetch_search_cards(search.source_url)
            result = await self._process_cards(
                db, cards, baseline_run, search.filters_json, search.name
            )

            if baseline_run:
                search_repo.mark_baseline_initialized(search, checked_at)
            search_repo.record_successful_check(search, checked_at)
            db.commit()

            result["baseline_initialized"] = search.baseline_initialized
            result["baseline_run"] = baseline_run
            result["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
            result["parser_stats"] = self._parser_stats_snapshot()
            result["runtime"] = runtime_diagnostics()
            if pagination is None:
                pagination = {
                    "pages_seen": 1,
                    "pages_attempted": 1,
                    "cards_processed_before_dedupe": len(cards),
                    "cards_seen_before_dedupe": len(cards),
                    "cards_seen_after_dedupe": len(cards),
                    "duplicate_cards_skipped": 0,
                    "pagination_stopped_reason": "single_page",
                    "page_errors": [],
                }
            pagination_diagnostics = {
                key: value for key, value in pagination.items() if key != "cards"
            }
            result.update(pagination_diagnostics)
            return result
        except Exception as exc:
            db.rollback()
            failed_at = self._now()
            persistent_search = (
                search_repo.get(search.id) if search.id is not None else search
            )
            if persistent_search is None:
                raise
            search_repo.record_failed_check(persistent_search, failed_at, str(exc))
            db.commit()
            raise

    async def process_search_by_id(self, db: Session, search_job_id: int) -> dict:
        repo = SearchRepository(db)
        search = repo.get(search_job_id)
        if search is None:
            raise ValueError(f"Search job {search_job_id} not found")
        return await self.process_search(db, search)

    async def _enrich_item_page(self, cards: list[ListingCard], filters: dict | None) -> dict[str, int]:
        attempted = 0
        succeeded = 0
        failed = 0
        skipped_limit = 0
        cache_hits = 0
        details_attempted = 0
        details_succeeded = 0
        details_failed = 0
        details_skipped_limit = 0
        details_cache_hits = 0
        if not settings.scrape_enrich_missing_published_at and not settings.scrape_enrich_item_page_details:
            return {
                "item_page_publication_enrichment_attempted": attempted,
                "item_page_publication_enrichment_succeeded": succeeded,
                "item_page_publication_enrichment_failed": failed,
                "item_page_publication_enrichment_skipped_limit": skipped_limit,
                "item_page_publication_enrichment_cache_hits": cache_hits,
                "item_page_details_enrichment_attempted": details_attempted,
                "item_page_details_enrichment_succeeded": details_succeeded,
                "item_page_details_enrichment_failed": details_failed,
                "item_page_details_enrichment_skipped_limit": details_skipped_limit,
                "item_page_details_enrichment_cache_hits": details_cache_hits,
            }

        limit = max(int(settings.scrape_item_page_limit_per_run), 0)
        delay_ms = max(int(settings.scrape_item_page_delay_ms), 0)
        jitter_ms = max(int(settings.scrape_item_page_jitter_ms), 0)
        remaining_limit = limit
        fetched_details_by_external_id: dict[str, dict] = {}

        async def _fetch_details(card: ListingCard, is_first_fetch: bool) -> dict:
            if not is_first_fetch:
                sleep_ms = delay_ms + (random.randint(0, jitter_ms) if jitter_ms > 0 else 0)
                if sleep_ms > 0:
                    await asyncio.sleep(sleep_ms / 1000.0)
            return await self.parser.fetch_item_details(card.url)

        publication_targets: list[ListingCard] = []
        if settings.scrape_enrich_missing_published_at:
            for card in cards:
                if card.published_at is not None:
                    continue
                cached = self._publication_enrichment_cache.get(card.external_id)
                if cached is None:
                    publication_targets.append(card)
                    continue
                card.published_label, card.published_at = cached
                cache_hits += 1

            skipped_limit = max(len(publication_targets) - remaining_limit, 0)
            fetch_count = min(len(publication_targets), remaining_limit)
            for idx, card in enumerate(publication_targets[:fetch_count]):
                attempted += 1
                try:
                    details = await _fetch_details(card, is_first_fetch=(attempted + details_attempted == 1 and idx == 0))
                except Exception:
                    failed += 1
                    continue
                fetched_details_by_external_id[card.external_id] = details
                label = str(details.get("published_label") or "")
                if not label:
                    failed += 1
                    continue
                published_at = AvitoParser._parse_published_at(label, self._now())
                if published_at is None:
                    failed += 1
                    continue
                card.published_label = label
                card.published_at = published_at
                self._publication_enrichment_cache[card.external_id] = (label, published_at)
                succeeded += 1
            remaining_limit -= fetch_count

        if settings.scrape_enrich_item_page_details:
            now = self._now()
            details_targets: list[ListingCard] = []
            for card in cards:
                if not passes_rule_filters(card, filters):
                    continue
                if explain_publication_filter_failures(card, filters, now):
                    continue
                details_targets.append(card)

            uncached_targets: list[ListingCard] = []
            for card in details_targets:
                cached_details = self._item_page_details_cache.get(card.external_id)
                if cached_details is not None:
                    card.raw["item_page"] = {**cached_details, "enriched_at": self._now().isoformat(), "version": "v2"}
                    details_cache_hits += 1
                    continue
                uncached_targets.append(card)

            reused_targets = [
                card for card in uncached_targets if card.external_id in fetched_details_by_external_id
            ]
            network_candidates = [
                card for card in uncached_targets if card.external_id not in fetched_details_by_external_id
            ]
            network_targets = network_candidates[:remaining_limit]
            details_skipped_limit = max(len(network_candidates) - len(network_targets), 0)

            for card in reused_targets:
                details = fetched_details_by_external_id[card.external_id]
                details_attempted += 1
                card.raw["item_page"] = {**details, "enriched_at": self._now().isoformat(), "version": "v2"}
                self._item_page_details_cache[card.external_id] = details
                details_succeeded += 1

            for idx, card in enumerate(network_targets):
                details_attempted += 1
                try:
                    details = await _fetch_details(card, is_first_fetch=(attempted + details_attempted == 1 and idx == 0 and not reused_targets))
                except Exception:
                    details_failed += 1
                    continue
                card.raw["item_page"] = {**details, "enriched_at": self._now().isoformat(), "version": "v2"}
                self._item_page_details_cache[card.external_id] = details
                details_succeeded += 1

        return {
            "item_page_publication_enrichment_attempted": attempted,
            "item_page_publication_enrichment_succeeded": succeeded,
            "item_page_publication_enrichment_failed": failed,
            "item_page_publication_enrichment_skipped_limit": skipped_limit,
            "item_page_publication_enrichment_cache_hits": cache_hits,
            "item_page_details_enrichment_attempted": details_attempted,
            "item_page_details_enrichment_succeeded": details_succeeded,
            "item_page_details_enrichment_failed": details_failed,
            "item_page_details_enrichment_skipped_limit": details_skipped_limit,
            "item_page_details_enrichment_cache_hits": details_cache_hits,
        }

    async def _process_cards(
        self,
        db: Session,
        cards: list[ListingCard],
        baseline_run: bool,
        filters: dict | None,
        search_name: str,
    ) -> dict:
        listing_repo = ListingRepository(db)
        existing_by_external_id = {
            card.external_id: listing_repo.get_by_external_id(card.external_id)
            for card in cards
        }
        enrichment_candidates = [
            card
            for card in cards
            if card.published_at is None and existing_by_external_id.get(card.external_id) is None
        ]
        if settings.scrape_enrich_item_page_details:
            enrichment_candidates = [
                card for card in cards if existing_by_external_id.get(card.external_id) is None
            ]
        enrichment_stats = await self._enrich_item_page(enrichment_candidates, filters)
        alert_repo = AlertRepository(db)

        created = 0
        alerted = 0
        price_changed = 0
        filtered_by_rules = 0
        filtered_by_publication_date = 0
        scored = 0
        llm_attempted = 0
        llm_succeeded = 0
        llm_failed = 0
        llm_skipped = 0
        filtered_samples: list[dict] = []
        publication_missing_allowed_count = 0
        publication_missing_rejected_count = 0
        configured_channels = [
            ch.channel_name for ch in getattr(self.notifier, "channels", [self.notifier])
        ]
        delivery_attempted_by_channel = {channel: 0 for channel in configured_channels}
        delivery_success_by_channel = {channel: 0 for channel in configured_channels}
        delivery_skipped_by_channel = {channel: 0 for channel in configured_channels}
        delivery_failed_by_channel = {channel: 0 for channel in configured_channels}
        delivery_unknown_by_channel = {channel: 0 for channel in configured_channels}

        for card in cards:
            now = self._now()
            existing = existing_by_external_id.get(card.external_id)

            if existing:
                old_price = existing.price
                listing_repo.update_listing_from_card(existing, card, now)

                if not baseline_run and old_price != card.price:
                    existing.price = card.price
                    listing_repo.create_snapshot(
                        external_id=card.external_id,
                        title=card.title,
                        price=card.price,
                        published_label=card.published_label,
                        published_at=card.published_at,
                        payload_json=card.raw,
                        screenshot_path="",
                        observed_at=now,
                    )
                    price_changed += 1

                if baseline_run:
                    continue

                retry_context = self._retry_context_from_snapshot(db, card, search_name)
                if retry_context is None:
                    continue

                message, payload = retry_context
                delivery = await self._deliver_pending_alerts(alert_repo, card, message, payload)
                for channel in delivery["attempted"]:
                    delivery_attempted_by_channel[channel] = (
                        delivery_attempted_by_channel.get(channel, 0) + 1
                    )
                for channel in delivery["successful"]:
                    delivery_success_by_channel[channel] = (
                        delivery_success_by_channel.get(channel, 0) + 1
                    )
                for channel in delivery["skipped"]:
                    delivery_skipped_by_channel[channel] = (
                        delivery_skipped_by_channel.get(channel, 0) + 1
                    )
                for channel in delivery["failed"]:
                    delivery_failed_by_channel[channel] = (
                        delivery_failed_by_channel.get(channel, 0) + 1
                    )
                for channel in delivery["unknown"]:
                    delivery_unknown_by_channel[channel] = (
                        delivery_unknown_by_channel.get(channel, 0) + 1
                    )
                if delivery["successful"]:
                    alerted += 1

                continue

            if baseline_run:
                listing, was_created = listing_repo.create_listing_safe(
                    external_id=card.external_id,
                    url=card.url,
                    title=card.title,
                    price=card.price,
                    address=card.address,
                    area_m2=card.area_m2,
                    rooms=card.rooms,
                    published_label=card.published_label,
                    published_at=card.published_at,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                if was_created:
                    created += 1
                    existing_by_external_id[card.external_id] = listing
                else:
                    listing_repo.update_listing_from_card(listing, card, now)
                    existing_by_external_id[card.external_id] = listing
                continue

            rule_failures = explain_rule_filter_failures(card, filters)
            if rule_failures:
                filtered_by_rules += 1
                if len(filtered_samples) < 10:
                    filtered_samples.append(
                        _filtered_sample(
                            card,
                            reason="rules",
                            rule_failures=rule_failures,
                        )
                    )
                continue

            publication_date_failures = explain_publication_filter_failures(card, filters, now)
            if filters.get("require_published_at") is True and card.published_at is None:
                if "missing_published_at" in publication_date_failures:
                    publication_missing_rejected_count += 1
                elif _is_missing_published_at_allowed(filters):
                    publication_missing_allowed_count += 1
            if publication_date_failures:
                publication_date_all_diagnostics = explain_publication_filter_failures(
                    card, filters, now, include_non_blocking=True
                )
                publication_date_warnings = [
                    item
                    for item in publication_date_all_diagnostics
                    if item not in publication_date_failures
                ]
                filtered_by_publication_date += 1
                if len(filtered_samples) < 10:
                    if (
                        filters.get("require_published_at") is True
                        and card.published_at is None
                        and _is_missing_published_at_allowed(filters)
                    ):
                        publication_date_warnings = [
                            *publication_date_warnings,
                            "missing_published_at_allowed",
                        ]
                    filtered_samples.append(
                        _filtered_sample(
                            card,
                            reason="publication_date",
                            publication_date_failures=publication_date_failures,
                            publication_date_warnings=publication_date_warnings,
                        )
                    )
                continue

            listing, was_created = listing_repo.create_listing_safe(
                external_id=card.external_id,
                url=card.url,
                title=card.title,
                price=card.price,
                address=card.address,
                area_m2=card.area_m2,
                rooms=card.rooms,
                published_label=card.published_label,
                published_at=card.published_at,
                first_seen_at=now,
                last_seen_at=now,
            )
            if not was_created:
                old_price = listing.price
                listing_repo.update_listing_from_card(listing, card, now)
                if old_price != card.price:
                    listing.price = card.price
                    listing_repo.create_snapshot(
                        external_id=card.external_id,
                        title=card.title,
                        price=card.price,
                        published_label=card.published_label,
                        published_at=card.published_at,
                        payload_json=card.raw,
                        screenshot_path="",
                        observed_at=now,
                    )
                    price_changed += 1
                retry_context = self._retry_context_from_snapshot(db, card, search_name)
                if retry_context is not None:
                    message, payload = retry_context
                    delivery = await self._deliver_pending_alerts(alert_repo, card, message, payload)
                    for channel in delivery["attempted"]:
                        delivery_attempted_by_channel[channel] = (
                            delivery_attempted_by_channel.get(channel, 0) + 1
                        )
                    for channel in delivery["successful"]:
                        delivery_success_by_channel[channel] = (
                            delivery_success_by_channel.get(channel, 0) + 1
                        )
                    for channel in delivery["skipped"]:
                        delivery_skipped_by_channel[channel] = (
                            delivery_skipped_by_channel.get(channel, 0) + 1
                        )
                    for channel in delivery["failed"]:
                        delivery_failed_by_channel[channel] = (
                            delivery_failed_by_channel.get(channel, 0) + 1
                        )
                    for channel in delivery["unknown"]:
                        delivery_unknown_by_channel[channel] = (
                            delivery_unknown_by_channel.get(channel, 0) + 1
                        )
                    if delivery["successful"]:
                        alerted += 1
                existing_by_external_id[card.external_id] = listing
                continue

            created += 1
            existing_by_external_id[card.external_id] = listing

            if settings.scoring_enabled:
                scored += 1
                llm_attempted += 1
                llm_cfg = resolve_llm_runtime_config()
                try:
                    llm = await self.scorer.score(card)
                    if not isinstance(llm, dict):
                        llm = {"score": None, "summary": "", "tags": [], "status": "failed", "provider": llm_cfg.provider, "model": llm_cfg.model, "prompt_version": llm_cfg.prompt_version, "error_type": "invalid_result"}
                    elif "status" not in llm:
                        llm = {
                            "score": llm.get("score"),
                            "summary": llm.get("summary", ""),
                            "tags": llm.get("tags", []),
                            "status": "success",
                            "provider": llm_cfg.provider,
                            "model": llm_cfg.model,
                            "prompt_version": llm_cfg.prompt_version,
                            "error_type": None,
                        }
                except Exception as exc:
                    llm = {
                        "score": None,
                        "summary": "",
                        "tags": [],
                        "status": "failed",
                        "provider": llm_cfg.provider,
                        "model": llm_cfg.model,
                        "prompt_version": llm_cfg.prompt_version,
                        "error_type": exc.__class__.__name__,
                    }
                if llm.get("status") == "success":
                    llm_succeeded += 1
                elif llm.get("status") == "skipped":
                    llm_skipped += 1
                else:
                    llm_failed += 1
            else:
                llm_skipped += 1
                llm = {"score": None, "summary": "", "tags": [], "status": "skipped", "provider": "off", "model": "", "prompt_version": settings.llm_prompt_version, "error_type": None}

            listing_repo.create_snapshot(
                external_id=card.external_id,
                title=card.title,
                price=card.price,
                published_label=card.published_label,
                published_at=card.published_at,
                payload_json={**card.raw, "llm_score": llm},
                screenshot_path="",
                observed_at=now,
            )

            use_llm_for_alert = (not settings.llm_shadow_mode) and llm.get("status") == "success"
            alert_summary = llm.get("summary", "") if use_llm_for_alert else ""
            alert_score = llm.get("score") if use_llm_for_alert else None
            alert_tags = llm.get("tags", []) if use_llm_for_alert else []

            message = build_listing_message(
                {
                    "title": card.title,
                    "price": card.price,
                    "address": card.address,
                    "area_m2": card.area_m2,
                    "rooms": card.rooms,
                    "published_label": card.published_label,
                    "url": card.url,
                },
                alert_summary,
            )
            payload = self._build_alert_payload(
                card=card,
                search_name=search_name,
                summary=alert_summary,
                score=alert_score,
                tags=alert_tags,
            )

            delivery = await self._deliver_pending_alerts(alert_repo, card, message, payload)
            for channel in delivery["attempted"]:
                delivery_attempted_by_channel[channel] = (
                    delivery_attempted_by_channel.get(channel, 0) + 1
                )
            for channel in delivery["successful"]:
                delivery_success_by_channel[channel] = (
                    delivery_success_by_channel.get(channel, 0) + 1
                )
            for channel in delivery["skipped"]:
                delivery_skipped_by_channel[channel] = (
                    delivery_skipped_by_channel.get(channel, 0) + 1
                )
            for channel in delivery["failed"]:
                delivery_failed_by_channel[channel] = (
                    delivery_failed_by_channel.get(channel, 0) + 1
                )
            for channel in delivery["unknown"]:
                delivery_unknown_by_channel[channel] = (
                    delivery_unknown_by_channel.get(channel, 0) + 1
                )
            if delivery["successful"]:
                alerted += 1

        filtered = filtered_by_rules + filtered_by_publication_date
        delivery_unsuccessful_by_channel = {
            channel: (
                delivery_skipped_by_channel.get(channel, 0)
                + delivery_failed_by_channel.get(channel, 0)
                + delivery_unknown_by_channel.get(channel, 0)
            )
            for channel in configured_channels
        }
        return {
            **enrichment_stats,
            "created": created,
            "alerted": alerted,
            "price_changed": price_changed,
            "filtered": filtered,
            "filtered_by_rules": filtered_by_rules,
            "filtered_by_publication_date": filtered_by_publication_date,
            "scored": scored,
            "llm_attempted": llm_attempted,
            "llm_succeeded": llm_succeeded,
            "llm_failed": llm_failed,
            "llm_skipped": llm_skipped,
            "llm_shadow_mode": settings.llm_shadow_mode,
            "total_seen": len(cards),
            "filtered_samples": filtered_samples,
            "publication_missing_allowed_count": publication_missing_allowed_count,
            "publication_missing_rejected_count": publication_missing_rejected_count,
            "delivery_attempted_by_channel": delivery_attempted_by_channel,
            "delivery_success_by_channel": delivery_success_by_channel,
            "delivery_skipped_by_channel": delivery_skipped_by_channel,
            "delivery_failed_by_channel": delivery_failed_by_channel,
            "delivery_unknown_by_channel": delivery_unknown_by_channel,
            "delivery_unsuccessful_by_channel": delivery_unsuccessful_by_channel,
        }

    def run_once(self, search_job_id: int) -> dict:
        """Run a single search job synchronously (one event loop, one browser session)."""
        loop = asyncio.new_event_loop()
        try:
            with SessionLocal() as db:
                return loop.run_until_complete(self.process_search_by_id(db, search_job_id))
        finally:
            loop.close()

    def run_all_searches(self) -> list[dict]:
        """Run all due searches sequentially in a single event loop.

        Using a single loop (instead of one runner per search) executes
        searches sequentially in one asyncio cycle and avoids running them
        concurrently from this service method.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._run_all_searches_async())
        finally:
            loop.close()

    async def _run_all_searches_async(self) -> list[dict]:
        """Async core of run_all_searches — runs all due searches sequentially."""
        begin_cycle = getattr(self.parser, "begin_cycle", None)
        end_cycle = getattr(self.parser, "end_cycle", None)
        cycle_started = False
        searches_processed = 0
        if begin_cycle is not None:
            await begin_cycle()
            cycle_started = True
        try:
            with SessionLocal() as db:
                repo = SearchRepository(db)
                searches = repo.list_due_active(_utcnow())
                results = []
                for search in searches:
                    started_at = time.perf_counter()
                    try:
                        result = await self.process_search(db, search)
                    except Exception as exc:
                        error_type = getattr(exc, "error_type", None)
                        logger.exception(
                            "search check failed",
                            extra={
                                "search": search.name,
                                "search_id": search.id,
                                "search_name": search.name,
                                "source_url_preview": _source_url_preview(search.source_url),
                                "last_error": str(exc),
                                "error_type": (
                                    error_type.value if hasattr(error_type, "value") else error_type
                                ),
                            },
                        )
                        result = {
                            "search": search.name,
                            "error": str(exc),
                            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
                            "parser_stats": self._parser_stats_snapshot(),
                        }
                    else:
                        result["search"] = search.name
                    results.append(result)
                    searches_processed += 1
                return results
        finally:
            if cycle_started and end_cycle is not None:
                await end_cycle()
            parser_cycle_stats = {}
            cycle_stats_fn = getattr(self.parser, "cycle_stats", None)
            if callable(cycle_stats_fn):
                parser_cycle_stats = cycle_stats_fn()
            logger.info(
                "monitor_service.cycle_summary searches_processed=%s preferred_engine=%s selected_first_engine=%s fallback_used=%s engine_skip_recent_failure_count=%s sessions_opened=%s sessions_reused=%s fallbacks=%s blocks=%s engine_errors=%s browser_driver_crashes=%s browser_driver_crash_retry_attempts=%s browser_driver_crash_retry_successes=%s proxy_failures=%s evictions=%s close_failures=%s close_failures_after_driver_crash=%s",
                searches_processed,
                parser_cycle_stats.get("preferred_engine"),
                parser_cycle_stats.get("selected_first_engine"),
                parser_cycle_stats.get("fallback_used"),
                parser_cycle_stats.get("engine_skip_recent_failure_count", 0),
                parser_cycle_stats.get("session_open_count", 0),
                parser_cycle_stats.get("session_reuse_count", 0),
                parser_cycle_stats.get("engine_fallback_count", 0),
                parser_cycle_stats.get("block_detected_count", 0),
                parser_cycle_stats.get("engine_error_count", 0),
                parser_cycle_stats.get("browser_driver_crash_count", 0),
                parser_cycle_stats.get("browser_driver_crash_retry_attempt_count", 0),
                parser_cycle_stats.get("browser_driver_crash_retry_success_count", 0),
                parser_cycle_stats.get("proxy_failure_count", 0),
                parser_cycle_stats.get("session_evict_count", 0),
                parser_cycle_stats.get("session_close_failure_count", 0),
                parser_cycle_stats.get("close_failure_after_driver_crash_count", 0),
            )
