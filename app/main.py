from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.admin import router as admin_router
from app.api.routes import router
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Avito Watcher", lifespan=lifespan)
app.include_router(router)
app.include_router(admin_router)
