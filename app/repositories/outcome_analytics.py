from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.human_review import HumanReview, InvestmentDecision
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis


class OutcomeAnalyticsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def count_reviews_total(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(HumanReview)) or 0)

    def count_decisions_total(self) -> int:
        return int(
            self.db.scalar(select(func.count()).select_from(InvestmentDecision)) or 0
        )

    def get_reviews_for_period(
        self,
        *,
        period_start: datetime,
        period_end: datetime,
        date_basis: str,
        filters: dict[str, Any],
    ) -> list[Any]:
        event_at = self._review_event_expr(date_basis)
        stmt = (
            select(HumanReview, ListingAnalysis, Listing.title)
            .outerjoin(
                ListingAnalysis, HumanReview.listing_analysis_id == ListingAnalysis.id
            )
            .outerjoin(Listing, HumanReview.listing_id == Listing.id)
            .where(
                event_at.is_not(None),
                event_at >= period_start.replace(tzinfo=None),
                event_at <= period_end.replace(tzinfo=None),
            )
        )
        stmt = self._apply_review_filters(stmt, filters)
        stmt = stmt.order_by(
            HumanReview.reviewed_at.desc().nullslast(),
            HumanReview.updated_at.desc(),
            HumanReview.id.desc(),
        )
        return list(self.db.execute(stmt))

    def get_decisions_for_period(
        self, *, period_start: datetime, period_end: datetime, filters: dict[str, Any]
    ) -> list[InvestmentDecision]:
        event_at = func.coalesce(
            InvestmentDecision.decided_at,
            InvestmentDecision.updated_at,
            InvestmentDecision.created_at,
        )
        stmt = select(InvestmentDecision).where(
            event_at >= period_start.replace(tzinfo=None),
            event_at <= period_end.replace(tzinfo=None),
        )
        if listing_external_ids := filters.get("listing_external_ids"):
            stmt = stmt.where(
                InvestmentDecision.listing_external_id.in_(listing_external_ids)
            )

        review_filter_names = (
            "search_job_ids",
            "review_statuses",
            "human_verdicts",
            "outcome_statuses",
        )
        if any(filters.get(name) for name in review_filter_names):
            stmt = stmt.join(
                HumanReview, InvestmentDecision.human_review_id == HumanReview.id
            )
            stmt = self._apply_decision_review_filters(stmt, filters)

        stmt = stmt.order_by(InvestmentDecision.id)
        return list(self.db.scalars(stmt))

    @staticmethod
    def _review_event_expr(date_basis: str):
        if date_basis == "coalesced":
            return func.coalesce(
                HumanReview.reviewed_at, HumanReview.updated_at, HumanReview.created_at
            )
        return getattr(HumanReview, date_basis)

    @staticmethod
    def _apply_review_filters(stmt, filters: dict[str, Any]):
        mapping = {
            "search_job_ids": HumanReview.search_job_id,
            "listing_external_ids": HumanReview.listing_external_id,
            "review_statuses": HumanReview.review_status,
            "human_verdicts": HumanReview.human_verdict,
            "outcome_statuses": HumanReview.outcome_status,
        }
        for name, column in mapping.items():
            values = filters.get(name)
            if values:
                stmt = stmt.where(column.in_(values))
        return stmt

    @staticmethod
    def _apply_decision_review_filters(stmt, filters: dict[str, Any]):
        mapping = {
            "search_job_ids": HumanReview.search_job_id,
            "review_statuses": HumanReview.review_status,
            "human_verdicts": HumanReview.human_verdict,
            "outcome_statuses": HumanReview.outcome_status,
        }
        for name, column in mapping.items():
            values = filters.get(name)
            if values:
                stmt = stmt.where(column.in_(values))
        return stmt
