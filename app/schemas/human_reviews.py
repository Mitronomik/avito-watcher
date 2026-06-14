from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class HumanReviewCreate(BaseModel):
    listing_external_id: str
    listing_id: int | None = None
    search_job_id: int | None = None
    listing_analysis_id: int | None = None
    review_context_key: str | None = None
    context_type: str = "listing"
    review_status: str = "new"
    human_verdict: str | None = None
    next_action: str | None = None
    rejected_reason: str | None = None
    outcome_status: str | None = None
    watchlist: bool = False
    false_positive: bool | None = None
    false_negative: bool | None = None
    confirmed_purchase_price_rub: Decimal | None = None
    confirmed_monthly_rent_rub: Decimal | None = None
    confirmed_area_m2: Decimal | None = None
    confirmed_opex_monthly_rub: Decimal | None = None
    confirmed_opex_ratio: Decimal | None = None
    confirmed_capex_initial_rub: Decimal | None = None
    confirmed_vacancy_rate: Decimal | None = None
    confirmed_source: str | None = None
    reviewer: str | None = None
    notes: str | None = None
    payload_json: dict[str, Any] | None = None


class HumanReviewUpdate(BaseModel):
    review_status: str | None = None
    human_verdict: str | None = None
    next_action: str | None = None
    rejected_reason: str | None = None
    outcome_status: str | None = None
    watchlist: bool | None = None
    false_positive: bool | None = None
    false_negative: bool | None = None
    confirmed_purchase_price_rub: Decimal | None = None
    confirmed_monthly_rent_rub: Decimal | None = None
    confirmed_area_m2: Decimal | None = None
    confirmed_opex_monthly_rub: Decimal | None = None
    confirmed_opex_ratio: Decimal | None = None
    confirmed_capex_initial_rub: Decimal | None = None
    confirmed_vacancy_rate: Decimal | None = None
    confirmed_source: str | None = None
    reviewer: str | None = None
    notes: str | None = None
    payload_json: dict[str, Any] | None = None


class InvestmentDecisionCreate(BaseModel):
    decision_type: str
    decision_status: str
    decision_reason: str | None = None
    amount_rub: Decimal | None = None
    expected_monthly_rent_rub: Decimal | None = None
    actual_monthly_rent_rub: Decimal | None = None
    actual_purchase_price_rub: Decimal | None = None
    actor: str | None = None
    note: str | None = None
    payload_json: dict[str, Any] | None = None
    decided_at: datetime | None = None


class HumanReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    listing_external_id: str
    review_context_key: str
    review_status: str
    human_verdict: str | None = None
    next_action: str | None = None
    outcome_status: str | None = None
    watchlist: bool
    false_positive: bool | None = None
    false_negative: bool | None = None
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
