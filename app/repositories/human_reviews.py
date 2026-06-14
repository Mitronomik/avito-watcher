from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.human_review import HumanReview, HumanReviewAction, InvestmentDecision


class HumanReviewRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_review(self, review_id: int) -> HumanReview | None:
        return self.db.get(HumanReview, review_id)

    def get_by_context_key(self, context_key: str) -> HumanReview | None:
        return self.db.scalar(select(HumanReview).where(HumanReview.review_context_key == context_key))

    def add_review(self, review: HumanReview) -> HumanReview:
        self.db.add(review)
        self.db.flush()
        return review

    def add_action(self, action: HumanReviewAction) -> HumanReviewAction:
        self.db.add(action)
        self.db.flush()
        return action

    def add_decision(self, decision: InvestmentDecision) -> InvestmentDecision:
        self.db.add(decision)
        self.db.flush()
        return decision

    def list_reviews(self, **filters) -> list[HumanReview]:
        stmt = select(HumanReview)
        for name, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(HumanReview, name) == value)
        return list(self.db.scalars(stmt.order_by(HumanReview.updated_at.desc(), HumanReview.id.desc())))

    def latest_for_listing(self, listing_external_id: str, review_context_key: str | None = None) -> HumanReview | None:
        stmt = select(HumanReview).where(HumanReview.listing_external_id == listing_external_id)
        if review_context_key:
            stmt = stmt.where(HumanReview.review_context_key == review_context_key)
        return self.db.scalar(stmt.order_by(HumanReview.updated_at.desc(), HumanReview.id.desc()))

    def decisions(self, decision_type: str | None = None, decision_status: str | None = None) -> list[InvestmentDecision]:
        stmt = select(InvestmentDecision)
        if decision_type:
            stmt = stmt.where(InvestmentDecision.decision_type == decision_type)
        if decision_status:
            stmt = stmt.where(InvestmentDecision.decision_status == decision_status)
        return list(self.db.scalars(stmt.order_by(InvestmentDecision.created_at.desc(), InvestmentDecision.id.desc())))
