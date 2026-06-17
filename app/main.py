from contextlib import asynccontextmanager

import logging

from fastapi import FastAPI, HTTPException, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.admin import router as admin_router
from app.api.routes import router
from app.api.admin_v1.routes import router as admin_api_v1_router
from app.api.admin_v1.redaction import redact_api_response
from app.api.admin_v1.schemas import error_response
from app.core.config import settings
from app.core.log_sanitizer import install_log_redaction
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def _is_admin_api_v1_request(request: Request) -> bool:
    return request.url.path.startswith("/api/admin/v1")


def _admin_api_error_code(status_code: int, detail: object) -> str:
    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code == 400 and str(detail).lower() == "pagination limit exceeded":
        return "pagination_limit_exceeded"
    if status_code == 422:
        return "validation_error"
    if status_code >= 500:
        return "internal_error"
    return "validation_error"


def _safe_admin_api_message(status_code: int, detail: object) -> str:
    if status_code >= 500:
        return "Internal error"
    if isinstance(detail, str) and detail:
        return detail
    return "Request failed"


def install_admin_api_v1_error_handlers(app_instance: FastAPI) -> None:
    async def _handle_admin_api_http_exception(request: Request, exc: HTTPException):
        if not _is_admin_api_v1_request(request):
            return await http_exception_handler(request, exc)
        code = _admin_api_error_code(exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(code, _safe_admin_api_message(exc.status_code, exc.detail)),
            headers=exc.headers,
        )

    app_instance.add_exception_handler(HTTPException, _handle_admin_api_http_exception)
    app_instance.add_exception_handler(StarletteHTTPException, _handle_admin_api_http_exception)

    @app_instance.exception_handler(RequestValidationError)
    async def admin_api_validation_exception_handler(request: Request, exc: RequestValidationError):
        if not _is_admin_api_v1_request(request):
            return await request_validation_exception_handler(request, exc)
        safe_errors = redact_api_response(exc.errors())
        return JSONResponse(
            status_code=422,
            content=error_response("validation_error", "Validation error", details=safe_errors),
        )

    @app_instance.exception_handler(Exception)
    async def admin_api_unhandled_exception_handler(request: Request, exc: Exception):
        if not _is_admin_api_v1_request(request):
            raise exc
        return JSONResponse(status_code=500, content=error_response("internal_error", "Internal error"))


def create_app(admin_ui_enabled: bool | None = None) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    install_log_redaction()
    app_instance = FastAPI(title="Avito Watcher", lifespan=lifespan)
    app_instance.include_router(router)
    app_instance.include_router(admin_api_v1_router)
    install_admin_api_v1_error_handlers(app_instance)
    enabled = settings.admin_ui_enabled if admin_ui_enabled is None else admin_ui_enabled
    if enabled:
        app_instance.include_router(admin_router)
    return app_instance


app = create_app()
