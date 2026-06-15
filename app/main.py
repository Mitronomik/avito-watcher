from contextlib import asynccontextmanager

import logging

from fastapi import FastAPI

from app.admin import router as admin_router
from app.api.routes import router
from app.core.config import settings
from app.core.log_sanitizer import install_log_redaction
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app(admin_ui_enabled: bool | None = None) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    install_log_redaction()
    app_instance = FastAPI(title="Avito Watcher", lifespan=lifespan)
    app_instance.include_router(router)
    enabled = settings.admin_ui_enabled if admin_ui_enabled is None else admin_ui_enabled
    if enabled:
        app_instance.include_router(admin_router)
    return app_instance


app = create_app()
