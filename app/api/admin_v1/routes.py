from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.admin_v1.dependencies import require_admin_api_read_access
from app.api.admin_v1.meta_contract import build_meta_contract
from app.api.admin_v1.schemas import API_VERSION, success_response

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
