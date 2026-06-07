from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.listing_analysis import ListingAnalysis
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
        if existing is None:
            existing = ListingSearchMatch(
                search_job_id=search_job_id,
                listing_external_id=listing_external_id,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                last_snapshot_id=snapshot_id,
                created_at=seen_at,
                updated_at=seen_at,
            )
            self.db.add(existing)
        else:
            existing.last_seen_at = seen_at
            if snapshot_id is not None:
                existing.last_snapshot_id = snapshot_id
            existing.updated_at = _now()
        self.db.flush()
        return existing

    def list_matches_without_analysis(
        self, search_job_id: int, profile: str, limit: int
    ) -> list[ListingSearchMatch]:
        if limit <= 0:
            return []
        context_key = f"search:{search_job_id}"
        analyzed_external_ids = (
            select(ListingAnalysis.listing_external_id)
            .where(
                ListingAnalysis.profile == profile,
                ListingAnalysis.context_key == context_key,
            )
            .distinct()
        )
        stmt = (
            select(ListingSearchMatch)
            .where(
                ListingSearchMatch.search_job_id == search_job_id,
                ListingSearchMatch.listing_external_id.not_in(analyzed_external_ids),
            )
            .order_by(ListingSearchMatch.first_seen_at.asc(), ListingSearchMatch.id.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def get_latest_match(
        self, search_job_id: int, listing_external_id: str
    ) -> ListingSearchMatch | None:
        return self.db.scalar(
            select(ListingSearchMatch).where(
                ListingSearchMatch.search_job_id == search_job_id,
                ListingSearchMatch.listing_external_id == listing_external_id,
            )
        )
