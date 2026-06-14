from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.human_review import (
    DECISION_STATUSES,
    DECISION_TYPES,
    HUMAN_VERDICTS,
    OUTCOME_STATUSES,
    REVIEW_STATUSES,
)

DateBasis = Literal["coalesced", "reviewed_at", "updated_at", "created_at"]


class OutcomeAnalyticsRequest(BaseModel):
    period_days: int = Field(default=30, ge=1, le=365)
    as_of: datetime | None = None
    date_basis: DateBasis = "coalesced"
    search_job_ids: list[int] | None = None
    listing_external_ids: list[str] | None = None
    review_statuses: list[str] | None = None
    human_verdicts: list[str] | None = None
    outcome_statuses: list[str] | None = None
    include_risk_flag_stats: bool = True
    include_score_bucket_stats: bool = True
    include_search_stats: bool = True
    include_decision_stats: bool = True
    include_examples: bool = True
    max_examples_per_section: int = Field(default=10, ge=0, le=50)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator(
        "search_job_ids",
        "listing_external_ids",
        "review_statuses",
        "human_verdicts",
        "outcome_statuses",
    )
    @classmethod
    def bounded_filters(cls, value: list | None) -> list | None:
        if value is not None and len(value) > 100:
            raise ValueError("filters are limited to 100 values")
        return value

    @field_validator("review_statuses")
    @classmethod
    def validate_review_statuses(cls, value: list[str] | None) -> list[str] | None:
        if value and (unknown := set(value) - REVIEW_STATUSES):
            raise ValueError(f"unknown review statuses: {sorted(unknown)}")
        return value

    @field_validator("human_verdicts")
    @classmethod
    def validate_human_verdicts(cls, value: list[str] | None) -> list[str] | None:
        if value and (unknown := set(value) - HUMAN_VERDICTS):
            raise ValueError(f"unknown human verdicts: {sorted(unknown)}")
        return value

    @field_validator("outcome_statuses")
    @classmethod
    def validate_outcome_statuses(cls, value: list[str] | None) -> list[str] | None:
        if value and (unknown := set(value) - OUTCOME_STATUSES):
            raise ValueError(f"unknown outcome statuses: {sorted(unknown)}")
        return value


class OutcomePeriod(BaseModel):
    as_of: datetime
    period_start: datetime
    period_end: datetime
    period_days: int
    date_basis: str


class OutcomeTotals(BaseModel):
    human_reviews_total: int = 0
    human_reviews_in_period: int = 0
    review_context_count: int = 0
    reviewed_listing_count: int = 0
    linked_analysis_count: int = 0
    unlinked_review_count: int = 0
    investment_decisions_total: int = 0
    investment_decisions_in_period: int = 0


class SignalCounts(BaseModel):
    positive_signals: dict[str, int] = Field(default_factory=dict)
    negative_signals: dict[str, int] = Field(default_factory=dict)
    human_positive_signal_count: int = 0
    human_negative_signal_count: int = 0


class OutcomeBucketStats(BaseModel):
    total_reviews: int = 0
    interesting_count: int = 0
    not_interesting_count: int = 0
    false_positive_count: int = 0
    false_negative_count: int = 0
    watchlist_count: int = 0
    sent_to_expert_count: int = 0
    deal_candidate_count: int = 0
    deal_done_count: int = 0
    human_positive_signal_count: int = 0
    human_negative_signal_count: int = 0


class SearchOutcomeStats(OutcomeBucketStats):
    search_job_id: int | None = None
    reviews_count: int = 0
    reviewed_listing_count: int = 0
    linked_analysis_count: int = 0
    average_score: float | None = None
    score_bucket_distribution: dict[str, int] = Field(default_factory=dict)
    top_risk_flags: dict[str, int] = Field(default_factory=dict)


class OutcomeExample(BaseModel):
    listing_external_id: str
    listing_id: int | None = None
    search_job_id: int | None = None
    listing_analysis_id: int | None = None
    review_context_key: str
    score: float | None = None
    verdict: str | None = None
    review_status: str
    human_verdict: str | None = None
    outcome_status: str | None = None
    watchlist: bool
    false_positive: bool | None = None
    false_negative: bool | None = None
    reviewed_at: datetime | None = None
    updated_at: datetime
    short_title: str | None = None


class OutcomeExamples(BaseModel):
    false_positive_examples: list[OutcomeExample] = Field(default_factory=list)
    false_negative_examples: list[OutcomeExample] = Field(default_factory=list)
    high_score_rejected_examples: list[OutcomeExample] = Field(default_factory=list)
    low_score_interesting_examples: list[OutcomeExample] = Field(default_factory=list)
    sent_to_expert_examples: list[OutcomeExample] = Field(default_factory=list)
    deal_candidate_examples: list[OutcomeExample] = Field(default_factory=list)
    deal_done_examples: list[OutcomeExample] = Field(default_factory=list)


class DecisionCounts(BaseModel):
    by_type: dict[str, int] = Field(
        default_factory=lambda: {k: 0 for k in sorted(DECISION_TYPES)}
    )
    by_status: dict[str, int] = Field(
        default_factory=lambda: {k: 0 for k in sorted(DECISION_STATUSES)}
    )


class OutcomeAnalyticsReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    request_hash: str
    stats_snapshot_hash: str
    period: OutcomePeriod
    totals: OutcomeTotals
    review_status_counts: dict[str, int]
    human_verdict_counts: dict[str, int]
    outcome_status_counts: dict[str, int]
    watchlist_counts: dict[str, int]
    false_positive_counts: dict[str, int]
    false_negative_counts: dict[str, int]
    signal_counts: SignalCounts
    decision_counts: DecisionCounts
    analysis_alignment: dict[str, dict[str, int]]
    score_bucket_stats: dict[str, OutcomeBucketStats]
    risk_flag_stats: dict[str, OutcomeBucketStats]
    search_stats: list[SearchOutcomeStats]
    examples: OutcomeExamples
    limitations: list[str]
