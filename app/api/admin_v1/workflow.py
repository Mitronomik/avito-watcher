from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

WORKFLOW_STATE_DTO_VERSION = "workflow-state-v1"
WORKFLOW_STATES = (
    "new",
    "analysis_pending",
    "needs_review",
    "needs_data",
    "ready_for_work",
    "watchlist",
    "rejected",
    "report_ready",
    "closed",
)
WORKFLOW_ACTIONS = (
    "open_listing",
    "take_in_work",
    "request_data",
    "call_owner",
    "watchlist",
    "reject",
    "generate_memo",
    "generate_commercial_offer",
    "export_report",
    "close",
)

WRITE_ACTIONS = {"take_in_work", "request_data", "call_owner", "watchlist", "reject", "generate_memo", "generate_commercial_offer", "export_report", "close"}

BUSINESS_ACTIONS_BY_STATE = {
    "analysis_pending": {"open_listing"},
    "needs_data": {"open_listing", "request_data", "call_owner", "watchlist", "reject"},
    "needs_review": {"open_listing", "request_data", "call_owner", "watchlist", "reject"},
    "ready_for_work": {"open_listing", "take_in_work", "call_owner", "watchlist", "reject", "generate_memo", "generate_commercial_offer", "export_report"},
    "watchlist": {"open_listing", "call_owner", "reject", "close"},
    "rejected": {"open_listing"},
    "closed": {"open_listing"},
}


def latest_successful_analysis_subquery():
    ranked = select(
        ListingAnalysis.id.label("analysis_id"),
        ListingAnalysis.listing_external_id.label("external_id"),
        func.row_number().over(
            partition_by=ListingAnalysis.listing_external_id,
            order_by=(ListingAnalysis.created_at.desc(), ListingAnalysis.id.desc()),
        ).label("rn"),
    ).where(ListingAnalysis.status == "success").subquery()
    return select(ranked.c.analysis_id, ranked.c.external_id).where(ranked.c.rn == 1).subquery()


def latest_review_subquery():
    ranked = select(
        HumanReview.id.label("review_id"),
        HumanReview.listing_external_id.label("external_id"),
        func.row_number().over(
            partition_by=HumanReview.listing_external_id,
            order_by=(HumanReview.updated_at.desc(), HumanReview.id.desc()),
        ).label("rn"),
    ).subquery()
    return select(ranked.c.review_id, ranked.c.external_id).where(ranked.c.rn == 1).subquery()


def workflow_row_for_listing(db: Session, listing_id: int) -> tuple[Listing, ListingAnalysis | None, HumanReview | None] | None:
    latest = latest_successful_analysis_subquery()
    reviews = latest_review_subquery()
    row = db.execute(
        select(Listing, ListingAnalysis, HumanReview)
        .outerjoin(latest, latest.c.external_id == Listing.external_id)
        .outerjoin(ListingAnalysis, ListingAnalysis.id == latest.c.analysis_id)
        .outerjoin(reviews, reviews.c.external_id == Listing.external_id)
        .outerjoin(HumanReview, HumanReview.id == reviews.c.review_id)
        .where(Listing.id == listing_id)
    ).first()
    return row if row is None else (row[0], row[1], row[2])


def is_safe_public_listing_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in {"avito.ru", "www.avito.ru", "m.avito.ru"}


def _human_state(review: HumanReview | None) -> tuple[str | None, str | None]:
    if review is None:
        return None, None
    if review.review_status == "closed" or review.outcome_status == "closed":
        return "closed", "human_review_closed"
    if review.human_verdict == "not_interesting" or review.outcome_status in {"rejected_after_call", "deal_lost"} or review.rejected_reason:
        return "rejected", "human_review_rejected"
    if review.watchlist or review.outcome_status == "watchlist":
        return "watchlist", "human_review_watchlist"
    return None, None


def derive_workflow_state(listing: Listing, analysis: ListingAnalysis | None, review: HumanReview | None) -> tuple[str, list[str]]:
    human_state, human_reason = _human_state(review)
    if human_state and human_reason:
        return human_state, [human_reason]
    reasons: list[str] = []
    if listing.price is None:
        reasons.append("missing_price")
    if listing.area_m2 is None:
        reasons.append("missing_area_m2")
    if reasons:
        return "needs_data", reasons
    if analysis is None:
        return "analysis_pending", ["latest_analysis_missing"]
    if listing.published_at is None and not listing.published_label:
        return "needs_review", ["freshness_unknown"]
    if analysis.verdict == "strong":
        return "ready_for_work", ["latest_analysis_verdict_strong"]
    if analysis.verdict == "review":
        return "needs_review", ["latest_analysis_verdict_review"]
    return "needs_review", ["fallback_needs_review"]


def _action(id_: str, business_applicable: bool, *, safe_url: bool) -> dict[str, Any]:
    if id_ == "open_listing":
        return {
            "id": id_,
            "business_applicable": business_applicable and safe_url,
            "implemented": True,
            "available_now": business_applicable and safe_url,
            "requires_write_endpoint": False,
            "reason": "listing_url_available" if business_applicable and safe_url else "missing_listing_url",
        }
    return {
        "id": id_,
        "business_applicable": business_applicable,
        "implemented": False,
        "available_now": False,
        "requires_write_endpoint": True,
        "reason": "requires_write_endpoint" if business_applicable else "state_not_ready",
    }


def build_workflow_snapshot(listing: Listing, analysis: ListingAnalysis | None, review: HumanReview | None) -> dict[str, Any]:
    state, reasons = derive_workflow_state(listing, analysis, review)
    applicable = BUSINESS_ACTIONS_BY_STATE.get(state, {"open_listing"})
    safe_url = is_safe_public_listing_url(listing.url)
    actions = [_action(action_id, action_id in applicable, safe_url=safe_url) for action_id in WORKFLOW_ACTIONS]
    allowed = [action for action in actions if action["business_applicable"]]
    blocked = [action for action in actions if not action["business_applicable"]]
    return {
        "schema_version": WORKFLOW_STATE_DTO_VERSION,
        "listing_id": listing.id,
        "listing_external_id": listing.external_id,
        "workflow_state": state,
        "allowed_actions": allowed,
        "blocked_actions": blocked,
        "state_reasons": reasons,
        "source_refs": {
            "listing_id": listing.id,
            "listing_external_id": listing.external_id,
            "listing_analysis_id": analysis.id if analysis else None,
            "human_review_id": review.id if review else None,
        },
        "limitations": [
            "derived_read_only_state",
            "write_transitions_not_implemented_in_pr32",
            "decision_card_not_implemented_in_pr32",
        ],
    }
