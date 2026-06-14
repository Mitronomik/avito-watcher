from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ALLOWED_MARKET_RESEARCH_STATUSES = {
    "success",
    "partial",
    "failed",
    "skipped",
    "invalid",
}
ALLOWED_EVIDENCE_TYPES = {
    "comparable_candidate",
    "finding",
    "assumption_to_verify",
    "risk",
    "opportunity",
}
ALLOWED_MARKET_ASSET_TYPES = {"commercial", "flat", "unknown", None}
ALLOWED_MARKET_DEAL_TYPES = {"sale", "rent", "unknown", None}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MarketResearchRun(Base):
    __tablename__ = "market_research_runs"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_market_research_runs_confidence",
        ),
        CheckConstraint(
            "status IN ('success', 'partial', 'failed', 'skipped', 'invalid')",
            name="ck_market_research_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True, index=True
    )
    listing_external_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    listing_analysis_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    research_profile: Mapped[str] = mapped_column(
        String(128), default="default", server_default="default", index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    query_plan_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    sources_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, server_default=func.now()
    )

    evidence_items: Mapped[list["MarketEvidenceItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MarketEvidenceItem(Base):
    __tablename__ = "market_evidence_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "evidence_type",
            "content_hash",
            name="uq_market_evidence_items_run_type_hash",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_market_evidence_items_confidence",
        ),
        CheckConstraint(
            "evidence_type IN ('comparable_candidate', 'finding', 'assumption_to_verify', 'risk', 'opportunity')",
            name="ck_market_evidence_items_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("market_research_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    listing_external_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    listing_analysis_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    research_profile: Mapped[str] = mapped_column(
        String(128), default="default", server_default="default", index=True
    )
    asset_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    deal_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_key: Mapped[str | None] = mapped_column(
        String(300), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_rub_per_month: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_per_m2_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    rent_per_m2_rub: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url_normalized: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True
    )
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_publisher: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_published_at: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_indexes_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    evidence_json: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    is_reusable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True
    )
    reuse_block_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, server_default=func.now()
    )

    run: Mapped[MarketResearchRun] = relationship(back_populates="evidence_items")
