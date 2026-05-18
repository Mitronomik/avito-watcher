from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.repositories.search_repository import SearchRepository
from app.schemas.search import SearchCreate
from app.services.monitor_service import MonitorService

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(key: str | None = Security(_api_key_header)) -> None:
    """Reject requests if API_KEY is configured and header doesn't match."""
    if not settings.api_key:
        return  # API_KEY not set — dev mode, allow all
    if key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-API-Key header",
        )


router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/searches")
async def create_search(payload: SearchCreate, db: Session = Depends(get_db)):
    repo = SearchRepository(db)
    item = repo.create(
        name=payload.name,
        source_url=str(payload.source_url),
        filters_json=payload.model_dump(),
    )
    db.commit()
    return {"id": item.id, "name": item.name, "source_url": item.source_url}


@router.post("/monitor/run")
async def run_monitor_once(
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_api_key),
):
    service = MonitorService()
    repo = SearchRepository(db)
    searches = repo.list_active()
    if not searches:
        return {"message": "no searches configured"}
    result = await service.process_search(db, searches[0])
    return result
