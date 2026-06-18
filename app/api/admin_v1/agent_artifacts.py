from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.admin_v1.pagination import parse_pagination
from app.api.admin_v1.schemas import success_response
from app.db.session import get_db
from app.models.agent_artifact import AGENT_ARTIFACT_TYPES
from app.services.agent_artifact_service import get_agent_artifact_by_id, list_agent_artifacts, serialize_agent_artifact

router = APIRouter(tags=["Admin API v1"])
ALLOWED_FILTERS = {"artifact_type", "listing_external_id", "listing_analysis_id", "search_job_id", "source_task_id", "orchestration_run_id", "context_key", "limit", "offset"}


def _reject_unknown(request: Request) -> None:
    unknown = set(request.query_params) - ALLOWED_FILTERS
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown query parameter: {sorted(unknown)[0]}")


@router.get("/agent-artifacts")
def list_agent_artifact_items(
    request: Request,
    db: Session = Depends(get_db),
    artifact_type: str | None = Query(default=None),
    listing_external_id: str | None = Query(default=None),
    listing_analysis_id: int | None = Query(default=None),
    search_job_id: int | None = Query(default=None),
    source_task_id: int | None = Query(default=None),
    orchestration_run_id: str | None = Query(default=None),
    context_key: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    offset: int | None = Query(default=None),
) -> dict[str, Any]:
    _reject_unknown(request)
    if artifact_type is not None and artifact_type not in AGENT_ARTIFACT_TYPES:
        raise HTTPException(status_code=422, detail="unknown artifact_type")
    pagination = parse_pagination(limit, offset)
    rows = list_agent_artifacts(db, artifact_type=artifact_type, listing_external_id=listing_external_id, listing_analysis_id=listing_analysis_id, search_job_id=search_job_id, source_task_id=source_task_id, orchestration_run_id=orchestration_run_id, context_key=context_key, limit=pagination.limit + 1, offset=pagination.offset)
    has_more = len(rows) > pagination.limit
    return success_response({"schema_version": "agent-artifact-list-v1", "items": [serialize_agent_artifact(item) for item in rows[: pagination.limit]]}, meta={**success_response({})["meta"], **pagination.meta(has_more=has_more)})


@router.get("/agent-artifacts/{artifact_id}")
def get_agent_artifact_item(artifact_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    artifact = get_agent_artifact_by_id(db, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Agent artifact not found")
    return success_response(serialize_agent_artifact(artifact, include_payload=True))
