from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.repositories.search_repository import SearchRepository
from app.schemas.search import SearchCreate
from app.services.monitor_service import MonitorService

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
async def run_monitor_once(db: Session = Depends(get_db)):
    service = MonitorService()
    repo = SearchRepository(db)
    searches = repo.list_active()
    if not searches:
        return {"message": "no searches configured"}
    result = await service.process_search(db, searches[0])
    return result
