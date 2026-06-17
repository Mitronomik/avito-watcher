from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.api.admin_v1.redaction import redact_api_response
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

LISTING_SUMMARY_DTO_VERSION = "listing-summary-v1"
LISTING_DETAIL_DTO_VERSION = "listing-detail-v1"
REVIEW_QUEUE_DTO_VERSION = "review-queue-item-v1"
DECISION_SOURCE_DTO_VERSION = "decision-source-v1"


def iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def latest_analysis_dto(analysis: ListingAnalysis | None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    return {
        "id": analysis.id,
        "status": analysis.status,
        "profile": analysis.profile,
        "score": analysis.score,
        "verdict": analysis.verdict,
        "created_at": iso(analysis.created_at),
    }


def listing_summary_dto(listing: Listing, analysis: ListingAnalysis | None) -> dict[str, Any]:
    return redact_api_response({
        "schema_version": LISTING_SUMMARY_DTO_VERSION,
        "id": listing.id,
        "external_id": listing.external_id,
        "url": listing.url,
        "title": listing.title or None,
        "price": listing.price,
        "area_m2": listing.area_m2,
        "address": listing.address or None,
        "rooms": listing.rooms or None,
        "is_active": listing.is_active,
        "published_label": listing.published_label or None,
        "published_at": iso(listing.published_at),
        "first_seen_at": iso(listing.first_seen_at),
        "last_seen_at": iso(listing.last_seen_at),
        "latest_analysis": latest_analysis_dto(analysis),
    })


def human_review_dto(review: HumanReview | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "id": review.id,
        "status": review.review_status,
        "human_verdict": review.human_verdict,
        "reviewed_at": iso(review.reviewed_at),
        "reviewed_by_label": review.reviewer or None,
    }


def listing_detail_dto(listing: Listing, analysis: ListingAnalysis | None, review: HumanReview | None) -> dict[str, Any]:
    data = listing_summary_dto(listing, analysis)
    data["schema_version"] = LISTING_DETAIL_DTO_VERSION
    data["latest_human_review"] = human_review_dto(review)
    data["alert_summary"] = None
    return redact_api_response(data)
