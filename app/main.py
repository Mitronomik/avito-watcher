from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.scheduler import scheduler_service
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler_service.start()
    yield
    scheduler_service.stop()


app = FastAPI(title="Avito Watcher", lifespan=lifespan)
app.include_router(router)
