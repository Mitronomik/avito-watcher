from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview, InvestmentDecision
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

DEFAULT_REVIEW_QUEUE_LIMIT = 50
MAX_REVIEW_QUEUE_LIMIT = 200


@dataclass(frozen=True)
class HumanReviewQueueRow:
    listing_id: int
    external_id: str
    title: str
    price: float | None
    address: str
    area_m2: float | None
    url: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    analysis_id: int | None
    analysis_profile: str | None
    analysis_status: str | None
    analysis_score: float | None
    analysis_verdict: str | None
    analysis_created_at: datetime | None
    analysis_updated_at: datetime | None
    risk_flags: tuple[str, ...] | None
    alert_sent_count: int
    latest_attempt_status: str | None
    latest_attempt_at: datetime | None
    human_review_count: int
    latest_review_status: str | None
    latest_human_verdict: str | None
    latest_outcome_status: str | None
    latest_review_at: datetime | None
    latest_decision_type: str | None
    latest_decision_status: str | None
    latest_decision_at: datetime | None


def normalize_review_queue_limit(value: int | str | None) -> int:
    if value in (None, ""):
        return DEFAULT_REVIEW_QUEUE_LIMIT
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_QUEUE_LIMIT
    return max(1, min(parsed, MAX_REVIEW_QUEUE_LIMIT))


def parse_unreviewed_only(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _risk_flags(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, dict):
        return None
    flags = value.get("flags")
    if not isinstance(flags, list):
        return None
    safe = [str(item) for item in flags if isinstance(item, str) and len(item) <= 80]
    return tuple(safe[:8])


def get_human_review_queue_rows(
    db: Session,
    *,
    limit: int | str | None = None,
    profile: str | None = None,
    unreviewed_only: bool | str | None = None,
) -> list[HumanReviewQueueRow]:
    """Return a bounded, read-only, one-row-per-listing review queue.

    Latest analysis policy: latest persisted analysis by created_at/id across all
    profiles, or within the requested profile when a profile filter is provided.
    Ordering is display-only and does not persist or compute a new priority score.
    """
    effective_limit = normalize_review_queue_limit(limit)
    normalized_profile = (profile or "").strip() or None
    only_unreviewed = parse_unreviewed_only(unreviewed_only)

    analysis_base = select(
        ListingAnalysis.id.label("analysis_id"),
        ListingAnalysis.listing_external_id.label("external_id"),
        func.row_number().over(
            partition_by=ListingAnalysis.listing_external_id,
            order_by=(ListingAnalysis.created_at.desc(), ListingAnalysis.id.desc()),
        ).label("rn"),
    )
    if normalized_profile:
        analysis_base = analysis_base.where(ListingAnalysis.profile == normalized_profile)
    latest_analysis_ids = analysis_base.subquery()
    latest_analysis = select(latest_analysis_ids.c.analysis_id, latest_analysis_ids.c.external_id).where(latest_analysis_ids.c.rn == 1).subquery()

    alert_counts = (
        select(AlertSent.listing_external_id.label("external_id"), func.count(AlertSent.id).label("alert_sent_count"))
        .group_by(AlertSent.listing_external_id)
        .subquery()
    )

    attempt_ranked = select(
        AlertDeliveryAttempt.id.label("attempt_id"),
        AlertDeliveryAttempt.listing_external_id.label("external_id"),
        func.row_number().over(
            partition_by=AlertDeliveryAttempt.listing_external_id,
            order_by=(AlertDeliveryAttempt.created_at.desc(), AlertDeliveryAttempt.id.desc()),
        ).label("rn"),
    ).subquery()
    latest_attempt_ids = select(attempt_ranked.c.attempt_id, attempt_ranked.c.external_id).where(attempt_ranked.c.rn == 1).subquery()

    review_counts = (
        select(HumanReview.listing_external_id.label("external_id"), func.count(HumanReview.id).label("human_review_count"))
        .group_by(HumanReview.listing_external_id)
        .subquery()
    )
    review_ranked = select(
        HumanReview.id.label("review_id"),
        HumanReview.listing_external_id.label("external_id"),
        func.row_number().over(
            partition_by=HumanReview.listing_external_id,
            order_by=(HumanReview.updated_at.desc(), HumanReview.id.desc()),
        ).label("rn"),
    ).subquery()
    latest_review_ids = select(review_ranked.c.review_id, review_ranked.c.external_id).where(review_ranked.c.rn == 1).subquery()

    decision_ranked = select(
        InvestmentDecision.id.label("decision_id"),
        InvestmentDecision.listing_external_id.label("external_id"),
        func.row_number().over(
            partition_by=InvestmentDecision.listing_external_id,
            order_by=(InvestmentDecision.created_at.desc(), InvestmentDecision.id.desc()),
        ).label("rn"),
    ).subquery()
    latest_decision_ids = select(decision_ranked.c.decision_id, decision_ranked.c.external_id).where(decision_ranked.c.rn == 1).subquery()

    alert_count = func.coalesce(alert_counts.c.alert_sent_count, 0)
    review_count = func.coalesce(review_counts.c.human_review_count, 0)
    verdict_rank = case((ListingAnalysis.verdict.in_(["strong", "review"]), 1), else_=0)

    stmt = (
        select(Listing, ListingAnalysis, alert_count, AlertDeliveryAttempt, review_count, HumanReview, InvestmentDecision)
        .outerjoin(latest_analysis, latest_analysis.c.external_id == Listing.external_id)
        .outerjoin(ListingAnalysis, ListingAnalysis.id == latest_analysis.c.analysis_id)
        .outerjoin(alert_counts, alert_counts.c.external_id == Listing.external_id)
        .outerjoin(latest_attempt_ids, latest_attempt_ids.c.external_id == Listing.external_id)
        .outerjoin(AlertDeliveryAttempt, AlertDeliveryAttempt.id == latest_attempt_ids.c.attempt_id)
        .outerjoin(review_counts, review_counts.c.external_id == Listing.external_id)
        .outerjoin(latest_review_ids, latest_review_ids.c.external_id == Listing.external_id)
        .outerjoin(HumanReview, HumanReview.id == latest_review_ids.c.review_id)
        .outerjoin(latest_decision_ids, latest_decision_ids.c.external_id == Listing.external_id)
        .outerjoin(InvestmentDecision, InvestmentDecision.id == latest_decision_ids.c.decision_id)
    )
    if normalized_profile:
        # A profile filter narrows the queue to listings that actually have a
        # latest persisted analysis for that profile; the no-profile queue keeps
        # listings without analyses and renders their analysis fields as unknown.
        stmt = stmt.where(latest_analysis.c.analysis_id.is_not(None))
    if only_unreviewed:
        # PR23c unreviewed_only means no human_reviews rows for this listing.
        # Investment decisions are summarized independently and are not a filter.
        stmt = stmt.where(review_count == 0)
    stmt = stmt.order_by(
        review_count.asc(),
        verdict_rank.desc(),
        ListingAnalysis.score.desc().nullslast(),
        alert_count.desc(),
        Listing.last_seen_at.desc(),
        Listing.id.desc(),
    ).limit(effective_limit)

    rows: list[HumanReviewQueueRow] = []
    for listing, analysis, sent_count, attempt, human_count, review, decision in db.execute(stmt).all():
        rows.append(
            HumanReviewQueueRow(
                listing_id=listing.id,
                external_id=listing.external_id,
                title=listing.title or "",
                price=listing.price,
                address=listing.address or "",
                area_m2=listing.area_m2,
                url=listing.url or "",
                first_seen_at=listing.first_seen_at,
                last_seen_at=listing.last_seen_at,
                analysis_id=analysis.id if analysis else None,
                analysis_profile=analysis.profile if analysis else None,
                analysis_status=analysis.status if analysis else None,
                analysis_score=analysis.score if analysis else None,
                analysis_verdict=analysis.verdict if analysis else None,
                analysis_created_at=analysis.created_at if analysis else None,
                analysis_updated_at=analysis.updated_at if analysis else None,
                risk_flags=_risk_flags(analysis.risks_json) if analysis else None,
                alert_sent_count=int(sent_count or 0),
                latest_attempt_status=attempt.status if attempt else None,
                latest_attempt_at=attempt.created_at if attempt else None,
                human_review_count=int(human_count or 0),
                latest_review_status=review.review_status if review else None,
                latest_human_verdict=review.human_verdict if review else None,
                latest_outcome_status=review.outcome_status if review else None,
                latest_review_at=review.updated_at if review else None,
                latest_decision_type=decision.decision_type if decision else None,
                latest_decision_status=decision.decision_status if decision else None,
                latest_decision_at=decision.created_at if decision else None,
            )
        )
    return rows
