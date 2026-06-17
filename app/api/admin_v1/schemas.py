from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

API_VERSION = "admin-v1"


def api_meta(*, generated_at: bool = True) -> dict[str, Any]:
    meta: dict[str, Any] = {"api_version": API_VERSION}
    if generated_at:
        meta["generated_at"] = datetime.now(UTC).isoformat()
    return meta


def success_response(data: dict[str, Any], *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "data": data, "meta": meta or api_meta()}


def error_response(code: str, message: str, *, details: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "details": details},
        "meta": api_meta(generated_at=False),
    }
