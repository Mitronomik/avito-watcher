import asyncio
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.agents.scorer import ListingScorer
from app.db.session import SessionLocal
from app.models.search_job import SearchJob
from app.notifiers.telegram import TelegramNotifier
from app.parsers.avito_parser import AvitoParser
from app.parsers.schemas import ListingCard
from app.repositories.alert_repository import AlertRepository
from app.repositories.listing_repository import ListingRepository
from app.repositories.search_repository import SearchRepository
from app.utils.formatting import build_listing_message


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


def _raw_text(raw: dict) -> str:
    parts = []
    for value in raw.values():
        if isinstance(value, (str, int, float)):
            parts.append(str(value))
    return " ".join(parts)


def _listing_text(card: ListingCard) -> str:
    return " ".join(
        part
        for part in (card.title, card.address, card.rooms, _raw_text(card.raw))
        if part
    ).lower()


def passes_rule_filters(card: ListingCard, filters: dict | None) -> bool:
    filters = filters or {}
    price = _as_float(card.price)
    area = _as_float(card.area_m2)

    min_price = _as_float(filters.get("min_price"))
    if min_price is not None and (price is None or price < min_price):
        return False

    max_price = _as_float(filters.get("max_price"))
    if max_price is not None and (price is None or price > max_price):
        return False

    min_area = _as_float(filters.get("min_area"))
    if min_area is not None and (area is None or area < min_area):
        return False

    max_area = _as_float(filters.get("max_area"))
    if max_area is not None and (area is None or area > max_area):
        return False

    text = _listing_text(card)
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


class MonitorService:
    def __init__(
        self,
        parser: AvitoParser | None = None,
        scorer: ListingScorer | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.parser = parser or AvitoParser()
        self.scorer = scorer or ListingScorer()
        self.notifier = notifier or TelegramNotifier()

    async def process_search(self, db: Session, search: SearchJob) -> dict:
        search_repo = SearchRepository(db)
        checked_at = _utcnow()
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
            failed_at = _utcnow()
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
        filtered = 0
        scored = 0

        for card in cards:
            now = _utcnow()
            existing = listing_repo.get_by_external_id(card.external_id)

            if existing:
                old_price = existing.price
                existing.last_seen_at = now
                existing.url = card.url or existing.url
                existing.title = card.title or existing.title
                existing.address = card.address or existing.address
                existing.area_m2 = card.area_m2
                existing.rooms = card.rooms or existing.rooms

                if not baseline_run and old_price != card.price:
                    existing.price = card.price
                    listing_repo.create_snapshot(
                        external_id=card.external_id,
                        title=card.title,
                        price=card.price,
                        payload_json=card.raw,
                        screenshot_path="",
                        observed_at=now,
                    )
                    price_changed += 1

                continue

            listing_repo.create_listing(
                external_id=card.external_id,
                url=card.url,
                title=card.title,
                price=card.price,
                address=card.address,
                area_m2=card.area_m2,
                rooms=card.rooms,
                first_seen_at=now,
                last_seen_at=now,
            )
            listing_repo.create_snapshot(
                external_id=card.external_id,
                title=card.title,
                price=card.price,
                payload_json=card.raw,
                screenshot_path="",
                observed_at=now,
            )
            created += 1

            if baseline_run:
                continue

            if not passes_rule_filters(card, filters):
                filtered += 1
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

            dedupe_key = f"telegram:new:{card.external_id}"
            if alert_repo.exists_by_dedupe_key(dedupe_key):
                continue

            message = build_listing_message(
                {
                    "title": card.title,
                    "price": card.price,
                    "address": card.address,
                    "area_m2": card.area_m2,
                    "rooms": card.rooms,
                    "url": card.url,
                },
                llm.get("summary", ""),
            )
            await self.notifier.send_listing_alert(message)
            alert_repo.create(
                listing_external_id=card.external_id, dedupe_key=dedupe_key
            )
            alerted += 1

        return {
            "created": created,
            "alerted": alerted,
            "price_changed": price_changed,
            "filtered": filtered,
            "scored": scored,
            "total_seen": len(cards),
        }

    def run_once(self, search_job_id: int) -> dict:
        with SessionLocal() as db:
            return asyncio.run(self.process_search_by_id(db, search_job_id))

    def run_all_searches(self) -> list[dict]:
        with SessionLocal() as db:
            repo = SearchRepository(db)
            searches = repo.list_due_active(_utcnow())
            results = []
            for search in searches:
                try:
                    result = asyncio.run(self.process_search(db, search))
                except Exception as exc:
                    result = {"search": search.name, "error": str(exc)}
                else:
                    result["search"] = search.name
                results.append(result)

            return results
