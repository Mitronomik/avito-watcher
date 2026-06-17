from __future__ import annotations

from fastapi import HTTPException, Security, status

from app.services.admin_auth import admin_api_key_header, is_valid_admin_read_key


def require_admin_api_read_access(key_header: str | None = Security(admin_api_key_header)) -> None:
    """Require the centralized admin read key for Admin API v1.

    Query-string auth is intentionally not passed through for this JSON API.
    """
    if is_valid_admin_read_key(key_header, api_key_qs=None):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key")
