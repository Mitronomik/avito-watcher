import asyncio
from datetime import datetime

from sqlalchemy.orm import Session

from app.agents.scorer import ListingScorer
from app.db.session import SessionLocal
from app.notifiers.telegram import TelegramNotifier
from app.parsers.avito_parser import AvitoParser
from app.repositories.alert_repository import AlertRepository
from app.repositories.listing_repository import ListingRepository
from app.repositories.search_repository import SearchRepository
from app.utils.formatting import build_listing_message


class MonitorService:
    def __init__(self) -> None:
        self.parser = AvitoParser()
        self.scorer = ListingScorer()
        self.notifier = TelegramNotifier()

    async def process_search(self, db: Session, search_url: str) -> dict:
        listing_repo = ListingRepository(db)
        alert_repo = AlertRepository(db)
        cards = await self.parser.fetch_search_cards(search_url)

        created = 0
        alerted = 0
        price_changed = 0

        for card in cards:
            now = datetime.utcnow()
            existing = listing_repo.get_by_external_id(card.external_id)

            if existing:
                old_price = existing.price
                existing.last_seen_at = now
                existing.url = card.url or existing.url
                existing.title = card.title or existing.title
                existing.address = card.address or existing.address
                existing.area_m2 = card.area_m2
                existing.rooms = card.rooms or existing.rooms

                if old_price != card.price:
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

        db.commit()
        return {
            "created": created,
            "alerted": alerted,
            "price_changed": price_changed,
            "total_seen": len(cards),
        }

    def run_once(self, search_url: str) -> dict:
        with SessionLocal() as db:
            return asyncio.run(self.process_search(db, search_url))

    def run_all_searches(self) -> list[dict]:
        with SessionLocal() as db:
            repo = SearchRepository(db)
            searches = repo.list_all()
            if not searches:
                searches = [
                    repo.create(
                        name="default",
                        source_url="https://www.avito.ru/all/kvartiry/prodam-ASgBAgICAUSSA8YQ",
                    )
                ]
                db.commit()

            results = []
            for search in searches:
                result = asyncio.run(self.process_search(db, search.source_url))
                result["search"] = search.name
                results.append(result)

            return results
