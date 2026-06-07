from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.listing_search_match import ListingSearchMatch


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ListingSearchMatchRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_match(
        self,
        search_job_id: int,
        listing_external_id: str,
        snapshot_id: int | None = None,
        seen_at: datetime | None = None,
    ) -> ListingSearchMatch:
        seen_at = seen_at or _now()
        existing = self.get_latest_match(search_job_id, listing_external_id)
        if existing is not None:
            return self._update_seen(existing, snapshot_id=snapshot_id, seen_at=seen_at)

        try:
            with self.db.begin_nested():
                created = ListingSearchMatch(
                    search_job_id=search_job_id,
                    listing_external_id=listing_external_id,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    last_snapshot_id=snapshot_id,
                    created_at=seen_at,
                    updated_at=seen_at,
                )
                self.db.add(created)
                self.db.flush()
            return created
        except IntegrityError:
            existing = self.get_latest_match(search_job_id, listing_external_id)
            if existing is None:
                raise
            return self._update_seen(existing, snapshot_id=snapshot_id, seen_at=seen_at)

    def _update_seen(
        self,
        match: ListingSearchMatch,
        *,
        snapshot_id: int | None,
        seen_at: datetime,
    ) -> ListingSearchMatch:
        match.last_seen_at = seen_at
        if snapshot_id is not None:
            match.last_snapshot_id = snapshot_id
        match.updated_at = _now()
        self.db.flush()
        return match

    def list_matches_for_search(self, search_job_id: int) -> list[ListingSearchMatch]:
        stmt = (
            select(ListingSearchMatch)
            .where(ListingSearchMatch.search_job_id == search_job_id)
            .order_by(ListingSearchMatch.first_seen_at.asc(), ListingSearchMatch.id.asc())
        )
        return list(self.db.scalars(stmt).all())

    def delete_match(self, search_job_id: int, listing_external_id: str) -> bool:
        match = self.get_latest_match(search_job_id, listing_external_id)
        if match is None:
            return False
        self.db.delete(match)
        self.db.flush()
        return True

    def get_latest_match(
        self, search_job_id: int, listing_external_id: str
    ) -> ListingSearchMatch | None:
        return self.db.scalar(
            select(ListingSearchMatch).where(
                ListingSearchMatch.search_job_id == search_job_id,
                ListingSearchMatch.listing_external_id == listing_external_id,
            )
        )
