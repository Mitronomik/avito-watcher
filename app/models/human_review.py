from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

REVIEW_STATUSES = {"new", "needs_review", "reviewed", "closed"}
HUMAN_VERDICTS = {"interesting", "neutral", "not_interesting", "false_positive", "false_negative", "needs_more_data"}
NEXT_ACTIONS = {"open_listing", "call_owner", "request_documents", "run_market_research", "run_data_quality_review", "send_to_expert", "add_to_watchlist", "reject", "do_nothing"}
REJECTED_REASONS = {"bad_price", "bad_location", "bad_area", "bad_condition", "stale_listing", "wrong_object_type", "duplicate", "bad_market_evidence", "low_yield", "legal_risk", "data_quality_issue", "not_relevant", "other"}
OUTCOME_STATUSES = {"not_started", "contacted_owner", "waiting_response", "documents_requested", "sent_to_expert", "under_review", "rejected_after_call", "watchlist", "deal_candidate", "offer_made", "deal_lost", "deal_done", "closed"}
ACTION_TYPES = {"created", "updated", "status_changed", "verdict_set", "next_action_set", "rejected", "watchlisted", "sent_to_expert", "confirmed_facts_updated", "notes_added", "outcome_updated", "investment_decision_recorded", "closed"}
DECISION_TYPES = {"watchlist", "reject", "send_to_expert", "call_owner", "deal_candidate", "offer", "deal_done", "deal_lost"}
DECISION_STATUSES = {"proposed", "approved", "rejected", "done", "cancelled"}


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class HumanReview(Base):
    __tablename__ = "human_reviews"
    __table_args__ = (UniqueConstraint("review_context_key", name="uq_human_reviews_review_context_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("listings.id"), nullable=True, index=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    search_job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("search_jobs.id"), nullable=True, index=True)
    listing_analysis_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("listing_analyses.id"), nullable=True, index=True)
    review_context_key: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    human_verdict: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    next_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    watchlist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    false_positive: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    false_negative: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    confirmed_purchase_price_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    confirmed_monthly_rent_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    confirmed_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    confirmed_opex_monthly_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    confirmed_opex_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    confirmed_capex_initial_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    confirmed_vacancy_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    confirmed_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    actions: Mapped[list["HumanReviewAction"]] = relationship(back_populates="review", cascade="all, delete-orphan")
    decisions: Mapped[list["InvestmentDecision"]] = relationship(back_populates="review", cascade="all, delete-orphan")


class HumanReviewAction(Base):
    __tablename__ = "human_review_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    human_review_id: Mapped[int] = mapped_column(Integer, ForeignKey("human_reviews.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    review: Mapped[HumanReview] = relationship(back_populates="actions")


class InvestmentDecision(Base):
    __tablename__ = "investment_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    human_review_id: Mapped[int] = mapped_column(Integer, ForeignKey("human_reviews.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    decision_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    decision_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    expected_monthly_rent_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    actual_monthly_rent_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    actual_purchase_price_rub: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    review: Mapped[HumanReview] = relationship(back_populates="decisions")
