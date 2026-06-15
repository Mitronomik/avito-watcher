from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

ADMIN_READ_HEADER_NAME = "X-API-Key"
ADMIN_TECHNICAL_WRITE_FORM_FIELD = "admin_technical_write_key"

admin_api_key_header = APIKeyHeader(name=ADMIN_READ_HEADER_NAME, auto_error=False)


@dataclass(frozen=True)
class AdminTechnicalAccessResult:
    read_key_valid: bool
    technical_key_valid: bool
    technical_ops_enabled: bool


def configured_read_key() -> str:
    """Return the explicit admin read key; empty means fail closed."""
    return settings.admin_ui_read_key or ""


def configured_technical_write_key() -> str:
    """Return the explicit admin technical write key; empty means fail closed."""
    return settings.admin_ui_technical_write_key or ""


def constant_time_equals(submitted: str | None, expected: str | None) -> bool:
    if not submitted or not expected:
        return False
    return secrets.compare_digest(str(submitted), str(expected))


def is_valid_admin_read_key(key_header: str | None, api_key_qs: str | None = None) -> bool:
    expected = configured_read_key()
    if not expected:
        return False
    if constant_time_equals(key_header, expected):
        return True
    return bool(settings.admin_ui_allow_query_api_key and constant_time_equals(api_key_qs, expected))


def require_admin_read_access(
    key_header: str | None = Security(admin_api_key_header),
    api_key_qs: str | None = Query(default=None, alias="api_key"),
) -> None:
    if is_valid_admin_read_key(key_header, api_key_qs):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key")


def validate_admin_technical_access(
    *,
    read_key_header: str | None,
    read_key_query: str | None,
    technical_write_key: str | None,
    technical_field_count: int | None = None,
) -> AdminTechnicalAccessResult:
    read_key_valid = is_valid_admin_read_key(read_key_header, read_key_query)
    technical_ops_enabled = bool(settings.admin_ui_technical_ops_enabled)
    expected = configured_technical_write_key()
    technical_key_valid = bool(
        expected
        and (technical_field_count in (None, 1))
        and constant_time_equals(technical_write_key, expected)
    )
    return AdminTechnicalAccessResult(
        read_key_valid=read_key_valid,
        technical_key_valid=technical_key_valid,
        technical_ops_enabled=technical_ops_enabled,
    )


def require_admin_technical_access(
    *,
    read_key_header: str | None,
    read_key_query: str | None,
    technical_write_key: str | None,
    technical_field_count: int | None = None,
) -> None:
    result = validate_admin_technical_access(
        read_key_header=read_key_header,
        read_key_query=read_key_query,
        technical_write_key=technical_write_key,
        technical_field_count=technical_field_count,
    )
    if not result.read_key_valid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key")
    if not result.technical_ops_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technical operations are disabled")
    if not configured_technical_write_key():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technical write key is not configured")
    if technical_field_count not in (None, 1) or not result.technical_key_valid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid technical admin key")
