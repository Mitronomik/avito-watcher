from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot
from app.parsers.schemas import ListingCard


class ListingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_external_id(self, external_id: str) -> Listing | None:
        return self.db.scalar(select(Listing).where(Listing.external_id == external_id))

    def update_listing_from_card(
        self, listing: Listing, card: ListingCard, seen_at: datetime
    ) -> Listing:
        listing.last_seen_at = seen_at
        listing.url = card.url or listing.url
        listing.title = card.title or listing.title
        listing.address = card.address or listing.address
        listing.area_m2 = card.area_m2
        listing.rooms = card.rooms or listing.rooms
        if card.published_label:
            listing.published_label = card.published_label
        if card.published_at is not None:
            listing.published_at = card.published_at
        return listing

    def create_listing(self, **kwargs) -> Listing:
        listing = Listing(**kwargs)
        self.db.add(listing)
        self.db.flush()
        return listing

    def create_listing_safe(self, **kwargs) -> tuple[Listing, bool]:
        try:
            with self.db.begin_nested():
                listing = Listing(**kwargs)
                self.db.add(listing)
                self.db.flush()
            return listing, True
        except IntegrityError:
            existing = self.get_by_external_id(kwargs["external_id"])
            if existing is None:
                raise
            return existing, False

    def create_snapshot(self, **kwargs) -> ListingSnapshot:
        snapshot = ListingSnapshot(**kwargs)
        self.db.add(snapshot)
        self.db.flush()
        return snapshot
