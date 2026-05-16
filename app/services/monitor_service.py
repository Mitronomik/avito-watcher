import asyncio
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

        for card in cards:
            existing = listing_repo.get_by_external_id(card.external_id)
            if existing:
                continue

            listing_repo.create_listing(
                external_id=card.external_id,
                url=card.url,
                title=card.title,
                price=card.price,
                address=card.address,
                area_m2=card.area_m2,
                rooms=card.rooms,
            )
            listing_repo.create_snapshot(
                external_id=card.external_id,
                title=card.title,
                price=card.price,
                payload_json=card.raw,
                screenshot_path="",
            )
            created += 1

            llm = await self.scorer.score(card)
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
        return {"created": created, "alerted": alerted, "total_seen": len(cards)}

    def run_once(self, search_url: str) -> dict:
        with SessionLocal() as db:
            return asyncio.run(self.process_search(db, search_url))

    def run_all_searches(self) -> list[dict]:
        with SessionLocal() as db:
            repo = SearchRepository(db)
            searches = repo.list_all()
            if not searches:
                searches = [repo.create(name="default", source_url="https://www.avito.ru/all/kvartiry/prodam-ASgBAgICAUSSA8YQ")]
                db.commit()
            results = []
            for search in searches:
                result = asyncio.run(self.process_search(db, search.source_url))
                result["search"] = search.name
                results.append(result)
            return results
