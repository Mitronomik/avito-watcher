from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.listing_detail_snapshot import ListingDetailSnapshot


class ListingDetailSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_external_id_and_hash(self, listing_external_id: str, content_hash: str) -> ListingDetailSnapshot | None:
        return self.db.scalar(
            select(ListingDetailSnapshot).where(
                ListingDetailSnapshot.listing_external_id == listing_external_id,
                ListingDetailSnapshot.content_hash == content_hash,
            )
        )

    def create_or_get_snapshot(self, **kwargs) -> tuple[ListingDetailSnapshot, bool]:
        existing = self.get_by_external_id_and_hash(kwargs["listing_external_id"], kwargs["content_hash"])
        if existing is not None:
            return existing, False
        try:
            with self.db.begin_nested():
                snapshot = ListingDetailSnapshot(**kwargs)
                self.db.add(snapshot)
                self.db.flush()
            return snapshot, True
        except IntegrityError:
            existing = self.get_by_external_id_and_hash(kwargs["listing_external_id"], kwargs["content_hash"])
            if existing is None:
                raise
            return existing, False

    def get_latest_successful_snapshot(self, listing_external_id: str) -> ListingDetailSnapshot | None:
        return self.db.scalar(
            select(ListingDetailSnapshot)
            .where(
                ListingDetailSnapshot.listing_external_id == listing_external_id,
                ListingDetailSnapshot.parse_status.in_(("success", "partial")),
            )
            .order_by(ListingDetailSnapshot.parsed_at.desc(), ListingDetailSnapshot.id.desc())
        )

    def list_snapshots_for_listing(self, listing_external_id: str, limit: int = 20) -> list[ListingDetailSnapshot]:
        safe_limit = max(1, min(limit, 100))
        return list(
            self.db.scalars(
                select(ListingDetailSnapshot)
                .where(ListingDetailSnapshot.listing_external_id == listing_external_id)
                .order_by(ListingDetailSnapshot.parsed_at.desc(), ListingDetailSnapshot.id.desc())
                .limit(safe_limit)
            )
        )
