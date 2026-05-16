from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot


class ListingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_external_id(self, external_id: str) -> Listing | None:
        return self.db.scalar(select(Listing).where(Listing.external_id == external_id))

    def create_listing(self, **kwargs) -> Listing:
        listing = Listing(**kwargs)
        self.db.add(listing)
        self.db.flush()
        return listing

    def create_snapshot(self, **kwargs) -> ListingSnapshot:
        snapshot = ListingSnapshot(**kwargs)
        self.db.add(snapshot)
        self.db.flush()
        return snapshot
