from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ListingDetailSnapshot(Base):
    __tablename__ = "listing_detail_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_external_id", "content_hash", name="uq_listing_detail_snapshots_external_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), index=True)
    listing_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(64), index=True)
    fetch_status: Mapped[str] = mapped_column(String(32), default="not_applicable", server_default="not_applicable", index=True)
    parse_status: Mapped[str] = mapped_column(String(32), default="skipped", server_default="skipped", index=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    parser_version: Mapped[str] = mapped_column(String(64), default="listing-detail-v1", server_default="listing-detail-v1")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    description_text: Mapped[str] = mapped_column(Text, default="")
    address_text: Mapped[str] = mapped_column(String(500), default="")
    metro_text: Mapped[str] = mapped_column(String(300), default="")
    price_text: Mapped[str] = mapped_column(String(200), default="")
    area_text: Mapped[str] = mapped_column(String(100), default="")
    published_label: Mapped[str] = mapped_column(String(200), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    seller_name: Mapped[str] = mapped_column(String(300), default="")
    seller_type: Mapped[str] = mapped_column(String(100), default="unknown")
    category: Mapped[str] = mapped_column(String(300), default="")
    attributes_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    facts_json: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    photos_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text_excerpt: Mapped[str] = mapped_column(String(2000), default="")
    extracted_fields_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    truncated_fields_json: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    warnings_json: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, server_default=func.now())
