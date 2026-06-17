from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.admin_v1.listing_dtos import REVIEW_QUEUE_DTO_VERSION, iso
from app.api.admin_v1.pagination import parse_pagination
from app.api.admin_v1.redaction import redact_api_response
from app.api.admin_v1.schemas import api_meta, success_response
from app.db.session import get_db
from app.services.human_review_queue import get_human_review_queue_rows

router = APIRouter(tags=["Admin API v1"])

ALLOWED = {"limit", "offset", "order_by", "order_dir", "verdict", "min_score", "max_score", "profile"}
ORDER_FIELDS = {"analysis_created_at", "score", "verdict", "listing_id", "published_at", "price", "area_m2"}


def _reject_unknown(request: Request) -> None:
    unknown = set(request.query_params) - ALLOWED
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown query parameter: {sorted(unknown)[0]}")


def _item(row) -> dict[str, Any]:
    return redact_api_response({
        "schema_version": REVIEW_QUEUE_DTO_VERSION,
        "listing": {
            "id": row.listing_id,
            "external_id": row.external_id,
            "url": row.url,
            "title": row.title or None,
            "price": row.price,
            "area_m2": row.area_m2,
            "address": row.address or None,
            "published_at": iso(row.published_at),
            "last_seen_at": iso(row.last_seen_at),
        },
        "analysis": None if row.analysis_id is None else {
            "id": row.analysis_id,
            "profile": row.analysis_profile,
            "status": row.analysis_status,
            "score": row.analysis_score,
            "verdict": row.analysis_verdict,
            "created_at": iso(row.analysis_created_at),
        },
        "review": {
            "queue_status": "needs_review",
            "latest_human_verdict": row.latest_human_verdict,
            "reviewed_at": iso(row.latest_review_at),
        },
    })


@router.get("/review-queue")
def review_queue(
    request: Request,
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None),
    offset: int | None = Query(default=None),
    order_by: str | None = Query(default=None),
    order_dir: str | None = Query(default=None),
    verdict: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    max_score: float | None = Query(default=None),
    profile: str | None = Query(default=None),
) -> dict[str, Any]:
    _reject_unknown(request)
    if order_by is not None and order_by not in ORDER_FIELDS:
        raise HTTPException(status_code=422, detail="unknown order field")
    if order_dir is not None and order_dir.lower() not in {"asc", "desc"}:
        raise HTTPException(status_code=422, detail="invalid order direction")
    pagination = parse_pagination(limit, offset)
    rows = get_human_review_queue_rows(
        db,
        limit=pagination.limit + 1,
        offset=pagination.offset,
        profile=profile,
        order_by=order_by,
        order_dir=order_dir,
        verdict=verdict,
        min_score=min_score,
        max_score=max_score,
    )
    has_more = len(rows) > pagination.limit
    items = [_item(row) for row in rows[: pagination.limit]]
    return success_response({"schema_version": "review-queue-v1", "items": items}, meta={**api_meta(), **pagination.meta(has_more=has_more)})
