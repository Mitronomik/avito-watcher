from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ListingSearchMatch(Base):
    __tablename__ = "listing_search_matches"
    __table_args__ = (
        UniqueConstraint(
            "search_job_id",
            "listing_external_id",
            name="uq_listing_search_matches_search_listing",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_job_id: Mapped[int] = mapped_column(Integer, index=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    last_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
