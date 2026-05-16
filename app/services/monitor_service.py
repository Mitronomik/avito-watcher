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
            result = await self._process_cards(db, cards, baseline_run)

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
            persistent_search = search_repo.get(search.id) if search.id is not None else search
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

    async def _process_cards(self, db: Session, cards: list[ListingCard], baseline_run: bool) -> dict:
        listing_repo = ListingRepository(db)
        alert_repo = AlertRepository(db)

        created = 0
        alerted = 0
        price_changed = 0

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
            alert_repo.create(listing_external_id=card.external_id, dedupe_key=dedupe_key)
            alerted += 1

        return {
            "created": created,
            "alerted": alerted,
            "price_changed": price_changed,
            "total_seen": len(cards),
        }

    def run_once(self, search_job_id: int) -> dict:
        with SessionLocal() as db:
            return asyncio.run(self.process_search_by_id(db, search_job_id))

    def run_all_searches(self) -> list[dict]:
        with SessionLocal() as db:
            repo = SearchRepository(db)
            searches = repo.list_active()
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
