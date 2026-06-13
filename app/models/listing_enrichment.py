from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ListingEnrichment(Base):
    __tablename__ = "listing_enrichments"
    __table_args__ = (
        UniqueConstraint(
            "enrichment_type",
            "source_type",
            "source_id",
            "model",
            "prompt_version",
            "schema_version",
            "extraction_profile",
            "input_hash",
            name="uq_listing_enrichments_success_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), index=True)
    listing_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    enrichment_type: Mapped[str] = mapped_column(String(100), index=True)
    source_type: Mapped[str] = mapped_column(String(100), index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    validation_status: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(200), default="")
    provider: Mapped[str] = mapped_column(String(100), default="")
    prompt_version: Mapped[str] = mapped_column(String(100), index=True)
    schema_version: Mapped[str] = mapped_column(String(100), index=True)
    extraction_profile: Mapped[str] = mapped_column(String(100), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    output_hash: Mapped[str] = mapped_column(String(64), index=True)
    structured_facts_json: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    field_confidence_json: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    evidence_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    missing_fields_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    uncertain_fields_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    contradictions_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    warnings_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, server_default=func.now()
    )
