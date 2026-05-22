import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

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
    "proxy_success_count",
    "proxy_failure_count",
    "session_open_count",
    "session_reuse_count",
    "session_evict_count",
    "session_close_failure_count",
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


def runtime_diagnostics() -> dict:
    alert_channels = [
        item.strip().lower()
        for item in settings.alert_channels.split(",")
        if item.strip()
    ]
    return {
        "alert_channels": alert_channels,
        "scoring_enabled": settings.scoring_enabled,
        "scrape_preferred_engine": settings.scrape_preferred_engine,
        "scrape_headless": settings.scrape_headless,
    }


def passes_rule_filters(card: ListingCard, filters: dict | None) -> bool:
    filters = filters or {}

    min_price = _as_float(filters.get("min_price"))
    if min_price is not None and (card.price is None or card.price < min_price):
        return False

    max_price = _as_float(filters.get("max_price"))
    if max_price is not None and (card.price is None or card.price > max_price):
        return False

    min_area = _as_float(filters.get("min_area") or filters.get("min_area_m2"))
    if min_area is not None and (card.area_m2 is None or card.area_m2 < min_area):
        return False

    max_area = _as_float(filters.get("max_area") or filters.get("max_area_m2"))
    if max_area is not None and (card.area_m2 is None or card.area_m2 > max_area):
        return False

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
        return False

    exclude_keywords = _as_keywords(filters.get("exclude_keywords"))
    if exclude_keywords and any(keyword in text for keyword in exclude_keywords):
        return False

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
        return False

    return True




def _filtered_sample(card: ListingCard, reason: str) -> dict:
    return {
        "external_id": card.external_id,
        "title": card.title,
        "price": card.price,
        "area_m2": card.area_m2,
        "address": card.address,
        "published_label": card.published_label,
        "url": card.url,
        "reason": reason,
    }

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


def passes_publication_filters(
    card: ListingCard,
    filters: dict | None,
    now: datetime | None = None,
) -> bool:
    filters = filters or {}
    published_at = card.published_at

    if filters.get("require_published_at") is True and published_at is None:
        return False

    if published_at is None:
        return True

    published_at = _as_utc_naive(published_at)
    now = _as_utc_naive(now or _utcnow())

    max_age_hours = _as_float(filters.get("max_age_hours"))
    if max_age_hours is not None and published_at < now - timedelta(hours=max_age_hours):
        return False

    published_after = _parse_iso_datetime(filters.get("published_after"))
    if published_after is not None and published_at <= published_after:
        return False

    published_on_date = filters.get("published_on_date")
    if isinstance(published_on_date, str) and published_on_date.strip():
        try:
            expected_date = datetime.strptime(
                published_on_date.strip(), "%Y-%m-%d"
            ).date()
        except ValueError:
            expected_date = None
        if expected_date is not None:
            # Publication dates are stored as naive UTC; convert to Moscow local date for date-only filters.
            local_date = published_at.replace(tzinfo=UTC).astimezone(MOSCOW_TZ).date()
            if local_date != expected_date:
                return False

    return True


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

        summary = llm.get("summary", "")
        payload = self._build_alert_payload(
            card=card,
            search_name=search_name,
            summary=summary,
            score=llm.get("score"),
            tags=llm.get("tags", []),
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
    ) -> bool:
        notifier_channels = getattr(self.notifier, "channels", [self.notifier])
        channel_names = [ch.channel_name for ch in notifier_channels]
        pending_channels = []
        for channel_name in channel_names:
            dedupe_key = f"{channel_name}:new:{card.external_id}"
            if not alert_repo.exists_by_dedupe_key(dedupe_key):
                pending_channels.append(channel_name)

        if not pending_channels:
            return False

        channels = getattr(self.notifier, "channels", [self.notifier])
        if all(hasattr(channel, "send_listing_alert") for channel in channels):
            channel_map = {
                channel.channel_name: channel
                for channel in channels
                if channel.channel_name in pending_channels
            }
            successful = []
            for channel_name in pending_channels:
                channel = channel_map.get(channel_name)
                if channel is None:
                    continue
                try:
                    delivered = await channel.send_listing_alert(message, payload)
                except Exception:
                    logger.exception("Alert channel failed", extra={"channel": channel_name})
                    continue
                if delivered is False:
                    continue
                successful.append(channel_name)
        else:
            successful = await self.notifier.send_listing_alert(message, payload)
            successful = [name for name in successful if name in pending_channels]
        for channel_name in successful:
            alert_repo.create(
                listing_external_id=card.external_id,
                dedupe_key=f"{channel_name}:new:{card.external_id}",
                channel=channel_name,
            )

        return bool(successful)

    async def process_search(self, db: Session, search: SearchJob) -> dict:
        search_repo = SearchRepository(db)
        checked_at = self._now()
        baseline_run = not search.baseline_initialized
        started_at = time.perf_counter()

        try:
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

    async def _process_cards(
        self,
        db: Session,
        cards: list[ListingCard],
        baseline_run: bool,
        filters: dict | None,
        search_name: str,
    ) -> dict:
        listing_repo = ListingRepository(db)
        alert_repo = AlertRepository(db)

        created = 0
        alerted = 0
        price_changed = 0
        filtered_by_rules = 0
        filtered_by_publication_date = 0
        scored = 0
        filtered_samples: list[dict] = []

        for card in cards:
            now = self._now()
            existing = listing_repo.get_by_external_id(card.external_id)

            if existing:
                old_price = existing.price
                existing.last_seen_at = now
                existing.url = card.url or existing.url
                existing.title = card.title or existing.title
                existing.address = card.address or existing.address
                existing.area_m2 = card.area_m2
                existing.rooms = card.rooms or existing.rooms
                if card.published_label:
                    existing.published_label = card.published_label
                if card.published_at is not None:
                    existing.published_at = card.published_at

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
                sent = await self._deliver_pending_alerts(alert_repo, card, message, payload)
                if sent:
                    alerted += 1

                continue

            if baseline_run:
                listing_repo.create_listing(
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
                created += 1
                continue

            if not passes_rule_filters(card, filters):
                filtered_by_rules += 1
                if len(filtered_samples) < 10:
                    filtered_samples.append(_filtered_sample(card, reason="rules"))
                continue

            if not passes_publication_filters(card, filters, now):
                filtered_by_publication_date += 1
                if len(filtered_samples) < 10:
                    filtered_samples.append(
                        _filtered_sample(card, reason="publication_date")
                    )
                continue

            listing_repo.create_listing(
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
            created += 1

            if settings.scoring_enabled:
                scored += 1
                try:
                    llm = await self.scorer.score(card)
                except Exception as exc:
                    llm = {
                        "score": 0,
                        "summary": f"LLM scoring unavailable: {exc}",
                        "tags": ["llm_error"],
                    }
            else:
                llm = {"score": None, "summary": "", "tags": []}

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
                llm.get("summary", ""),
            )
            payload = self._build_alert_payload(
                card=card,
                search_name=search_name,
                summary=llm.get("summary", ""),
                score=llm.get("score"),
                tags=llm.get("tags", []),
            )

            sent = await self._deliver_pending_alerts(alert_repo, card, message, payload)
            if sent:
                alerted += 1

        filtered = filtered_by_rules + filtered_by_publication_date
        return {
            "created": created,
            "alerted": alerted,
            "price_changed": price_changed,
            "filtered": filtered,
            "filtered_by_rules": filtered_by_rules,
            "filtered_by_publication_date": filtered_by_publication_date,
            "scored": scored,
            "total_seen": len(cards),
            "filtered_samples": filtered_samples,
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
                        logger.exception("search check failed", extra={"search": search.name})
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
                "monitor_service.cycle_summary searches_processed=%s preferred_engine=%s selected_first_engine=%s fallback_used=%s engine_skip_recent_failure_count=%s sessions_opened=%s sessions_reused=%s fallbacks=%s blocks=%s engine_errors=%s proxy_failures=%s evictions=%s close_failures=%s",
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
                parser_cycle_stats.get("proxy_failure_count", 0),
                parser_cycle_stats.get("session_evict_count", 0),
                parser_cycle_stats.get("session_close_failure_count", 0),
            )
