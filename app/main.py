from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.routes import router
from app.core.scheduler import scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_service.start()
    yield
    scheduler_service.stop()


app = FastAPI(title="Avito Watcher", lifespan=lifespan)
app.include_router(router)
