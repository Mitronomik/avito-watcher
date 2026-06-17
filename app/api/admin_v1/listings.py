from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.api.admin_v1.decision_card import DECISION_CARD_DTO_VERSION, build_decision_card
from app.api.admin_v1.price_position import PRICE_POSITION_DTO_VERSION, build_price_position
from app.api.admin_v1.risk_attention import RISK_ATTENTION_DTO_VERSION, build_risk_attention_from_card
from app.api.admin_v1.readiness_checklist import READINESS_CHECKLIST_DTO_VERSION, build_readiness_checklist
from app.api.admin_v1.listing_dtos import DECISION_SOURCE_DTO_VERSION, listing_detail_dto, listing_summary_dto, latest_analysis_dto, human_review_dto
from app.api.admin_v1.workflow import build_workflow_snapshot, latest_review_subquery, latest_successful_analysis_subquery, workflow_row_for_listing
from app.api.admin_v1.ordering import parse_ordering
from app.api.admin_v1.pagination import parse_pagination
from app.api.admin_v1.schemas import success_response
from app.db.session import get_db
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

router = APIRouter(tags=["Admin API v1"])

LISTING_FILTERS = {"limit", "offset", "order_by", "order_dir", "is_active", "external_id", "search_job_id", "min_price", "max_price", "min_area_m2", "max_area_m2"}


def _reject_unknown(request: Request, allowed: set[str]) -> None:
    unknown = set(request.query_params) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown query parameter: {sorted(unknown)[0]}")


def _latest_successful_analysis_subquery():
    return latest_successful_analysis_subquery()


def _latest_review_subquery():
    return latest_review_subquery()


def _order(expr: object, direction: str):
    return (desc(expr) if direction == "desc" else asc(expr)).nullslast()


@router.get("/listings")
def list_listings(
    request: Request,
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None),
    offset: int | None = Query(default=None),
    order_by: str | None = Query(default=None),
    order_dir: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    external_id: str | None = Query(default=None),
    search_job_id: int | None = Query(default=None),
    min_price: float | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_area_m2: float | None = Query(default=None),
    max_area_m2: float | None = Query(default=None),
) -> dict[str, Any]:
    _reject_unknown(request, LISTING_FILTERS)
    pagination = parse_pagination(limit, offset)
    latest = _latest_successful_analysis_subquery()
    allowed = {"id": Listing.id, "first_seen_at": Listing.first_seen_at, "last_seen_at": Listing.last_seen_at, "published_at": Listing.published_at, "price": Listing.price, "area_m2": Listing.area_m2}
    ordering = parse_ordering(order_by=order_by, order_dir=order_dir, allowed_fields=allowed, default_field="last_seen_at")
    stmt = select(Listing, ListingAnalysis).outerjoin(latest, latest.c.external_id == Listing.external_id).outerjoin(ListingAnalysis, ListingAnalysis.id == latest.c.analysis_id)
    if is_active is not None:
        stmt = stmt.where(Listing.is_active == is_active)
    if external_id:
        stmt = stmt.where(Listing.external_id == external_id)
    if search_job_id is not None:
        stmt = stmt.where(ListingAnalysis.search_job_id == search_job_id)
    if min_price is not None:
        stmt = stmt.where(Listing.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Listing.price <= max_price)
    if min_area_m2 is not None:
        stmt = stmt.where(Listing.area_m2 >= min_area_m2)
    if max_area_m2 is not None:
        stmt = stmt.where(Listing.area_m2 <= max_area_m2)
    id_tiebreaker = Listing.id.desc() if ordering.direction == "desc" else Listing.id.asc()
    stmt = stmt.order_by(_order(ordering.expression, ordering.direction), id_tiebreaker).offset(pagination.offset).limit(pagination.limit + 1)
    rows = db.execute(stmt).all()
    has_more = len(rows) > pagination.limit
    items = [listing_summary_dto(listing, analysis) for listing, analysis in rows[: pagination.limit]]
    return success_response({"schema_version": "listing-list-v1", "items": items}, meta={**success_response({})["meta"], **pagination.meta(has_more=has_more)})


@router.get("/listings/{listing_id}")
def get_listing(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    latest = _latest_successful_analysis_subquery()
    reviews = _latest_review_subquery()
    row = db.execute(
        select(Listing, ListingAnalysis, HumanReview)
        .outerjoin(latest, latest.c.external_id == Listing.external_id)
        .outerjoin(ListingAnalysis, ListingAnalysis.id == latest.c.analysis_id)
        .outerjoin(reviews, reviews.c.external_id == Listing.external_id)
        .outerjoin(HumanReview, HumanReview.id == reviews.c.review_id)
        .where(Listing.id == listing_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    listing, analysis, review = row
    return success_response(listing_detail_dto(listing, analysis, review))


@router.get("/listings/{listing_id}/workflow")
def get_listing_workflow(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = workflow_row_for_listing(db, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing, analysis, review = row
    return success_response(build_workflow_snapshot(listing, analysis, review))


@router.get("/listings/{listing_id}/risk-attention", name="admin_api_v1_risk_attention")
def get_listing_risk_attention(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = workflow_row_for_listing(db, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing, analysis, review = row
    workflow = build_workflow_snapshot(listing, analysis, review)
    card = build_decision_card(listing, analysis, review, workflow)
    return success_response(build_risk_attention_from_card(card))


@router.get("/listings/{listing_id}/readiness-checklist", name="admin_api_v1_readiness_checklist")
def get_listing_readiness_checklist(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = workflow_row_for_listing(db, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing, analysis, review = row
    workflow = build_workflow_snapshot(listing, analysis, review)
    card = build_decision_card(listing, analysis, review, workflow)
    return success_response(build_readiness_checklist(listing, analysis, review, workflow, card))


@router.get("/listings/{listing_id}/price-position", name="admin_api_v1_price_position")
def get_listing_price_position(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = workflow_row_for_listing(db, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing, analysis, _review = row
    return success_response(build_price_position(listing, analysis))


@router.get("/listings/{listing_id}/decision-card", name="admin_api_v1_decision_card")
def get_listing_decision_card(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = workflow_row_for_listing(db, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing, analysis, review = row
    workflow = build_workflow_snapshot(listing, analysis, review)
    return success_response(build_decision_card(listing, analysis, review, workflow))


@router.get("/listings/{listing_id}/decision-source")
def get_decision_source(listing_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = workflow_row_for_listing(db, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    listing, analysis, review = row
    workflow = build_workflow_snapshot(listing, analysis, review)
    data = {
        "schema_version": DECISION_SOURCE_DTO_VERSION,
        "listing": listing_summary_dto(listing, analysis),
        "latest_analysis": latest_analysis_dto(analysis),
        "human_review": human_review_dto(review),
        "workflow": workflow,
        "available_sections": {"listing": True, "analysis": analysis is not None, "market_facts": False, "human_review": review is not None, "alerts": False, "workflow": True, "decision_card": True, "risk_attention": True, "readiness_checklist": True, "price_position": True},
        "decision_card_ref": {"route_name": "admin_api_v1_decision_card", "listing_id": listing.id, "schema_version": DECISION_CARD_DTO_VERSION},
        "risk_attention_ref": {"route_name": "admin_api_v1_risk_attention", "listing_id": listing.id, "schema_version": RISK_ATTENTION_DTO_VERSION},
        "readiness_checklist_ref": {"route_name": "admin_api_v1_readiness_checklist", "listing_id": listing.id, "schema_version": READINESS_CHECKLIST_DTO_VERSION},
        "price_position_ref": {"route_name": "admin_api_v1_price_position", "listing_id": listing.id, "schema_version": PRICE_POSITION_DTO_VERSION},
        "source_refs": {"listing_id": listing.id, "listing_external_id": listing.external_id, "listing_analysis_id": analysis.id if analysis else None, "human_review_id": review.id if review else None},
        "limitations": ["decision_card_available_in_pr33", "write_transitions_not_implemented_in_pr32", "action_execution_not_implemented_in_pr32"],
    }
    return success_response(data)
