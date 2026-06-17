from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin_v1.dependencies import require_admin_api_read_access
from app.api.admin_v1.meta_contract import build_meta_contract
from app.api.admin_v1.schemas import API_VERSION, success_response
from app.api.admin_v1.listings import router as listings_router
from app.api.admin_v1.review_queue import router as review_queue_router

router = APIRouter(
    prefix="/api/admin/v1",
    tags=["Admin API v1"],
    dependencies=[Depends(require_admin_api_read_access)],
)


@router.get("/status")
def status() -> dict[str, object]:
    return success_response({"status": "ok", "service": "avito-watcher", "api": API_VERSION})


@router.get("/meta")
def meta() -> dict[str, object]:
    return success_response(build_meta_contract())


router.include_router(listings_router)
router.include_router(review_queue_router)
