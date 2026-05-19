import asyncio
import logging
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


    def _build_alert_payload(self, card: ListingCard, summary: str, score, tags: list) -> dict:
        return {
            "search_name": card.raw.get("search_name", ""),
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

    def _retry_context_from_snapshot(self, db: Session, card: ListingCard) -> tuple[str, dict] | None:
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

        try:
            cards = await self.parser.fetch_search_cards(search.source_url)
            result = await self._process_cards(
                db, cards, baseline_run, search.filters_json
            )

            if baseline_run:
                search_repo.mark_baseline_initialized(search, checked_at)
            search_repo.record_successful_check(search, checked_at)
            db.commit()

            result["baseline_initialized"] = search.baseline_initialized
            result["baseline_run"] = baseline_run
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
    ) -> dict:
        listing_repo = ListingRepository(db)
        alert_repo = AlertRepository(db)

        created = 0
        alerted = 0
        price_changed = 0
        filtered_by_rules = 0
        filtered_by_publication_date = 0
        scored = 0

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

                retry_context = self._retry_context_from_snapshot(db, card)
                if retry_context is None:
                    continue

                message, payload = retry_context
                sent = await self._deliver_pending_alerts(alert_repo, card, message, payload)
                if sent:
                    alerted += 1

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

            if baseline_run:
                continue

            if not passes_rule_filters(card, filters):
                filtered_by_rules += 1
                continue

            if not passes_publication_filters(card, filters, now):
                filtered_by_publication_date += 1
                continue

            scored += 1
            try:
                llm = await self.scorer.score(card)
            except Exception as exc:
                llm = {
                    "score": 0,
                    "summary": f"LLM scoring unavailable: {exc}",
                    "tags": ["llm_error"],
                }

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

        Using a single loop (instead of one runner per search) means the
        AvitoParser reuses the same browser session across searches in one cycle,
        avoiding N concurrent Chromium processes.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._run_all_searches_async())
        finally:
            loop.close()

    async def _run_all_searches_async(self) -> list[dict]:
        """Async core of run_all_searches — runs all due searches sequentially."""
        with SessionLocal() as db:
            repo = SearchRepository(db)
            searches = repo.list_due_active(_utcnow())
            results = []
            for search in searches:
                try:
                    result = await self.process_search(db, search)
                except Exception as exc:
                    logger.exception("search check failed", extra={"search": search.name})
                    result = {"search": search.name, "error": str(exc)}
                else:
                    result["search"] = search.name
                results.append(result)
            return results
