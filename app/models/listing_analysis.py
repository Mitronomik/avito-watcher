from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


ALLOWED_ANALYSIS_STATUSES = {"pending", "running", "success", "failed", "skipped", "stale"}


class ListingAnalysis(Base):
    __tablename__ = "listing_analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'skipped', 'stale')",
            name="ck_listing_analyses_status",
        ),
        UniqueConstraint(
            "listing_external_id",
            "profile",
            "analysis_version",
            "input_hash",
            "context_key",
            name="uq_listing_analyses_input_context",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    search_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    context_key: Mapped[str] = mapped_column(String(160), default="global", index=True)
    profile: Mapped[str] = mapped_column(String(128), default="default", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    analysis_version: Mapped[str] = mapped_column(String(64), default="mock-v1")
    model_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(64), nullable=True)
    facts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    risks_json: Mapped[dict] = mapped_column(JSON, default=dict)
    questions_json: Mapped[dict] = mapped_column(JSON, default=dict)
    report_md: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )
