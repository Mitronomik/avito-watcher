from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from app.models.admin_audit_event import AdminAuditEvent

logger = logging.getLogger(__name__)

AUDIT_STATUSES = {"success", "failed", "blocked"}
ALERT_DELIVERY_RETRY_METADATA_KEYS = {
    "reason",
    "retry_result_status",
    "source_attempt_id",
    "created_attempt_id",
    "alert_sent_created",
    "channel",
    "listing_external_id",
    "target_attempt_status",
}
_ACTION_METADATA_KEYS = {"alert_delivery_retry": ALERT_DELIVERY_RETRY_METADATA_KEYS}
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|cookie|token|secret|password|passwd|webhook|telegram|admin_technical_write_key|confirm_action|script\.google\.com)"
)
_ALLOWED_SCALAR = (str, int, bool, float, type(None))


def _safe_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _SECRET_RE.sub("[redacted]", text)
    text = re.sub(r"https?://\S+", "[redacted-url]", text)
    return text[:limit]


def _safe_metadata(action: str, metadata: Mapping[str, object] | None) -> dict[str, object]:
    allowed = _ACTION_METADATA_KEYS.get(action, set())
    result: dict[str, object] = {}
    for key, value in (metadata or {}).items():
        key = str(key)
        if key not in allowed or not isinstance(value, _ALLOWED_SCALAR):
            continue
        if isinstance(value, str):
            result[key] = _safe_text(value, 128) or ""
        else:
            result[key] = value
    return result


def record_admin_audit_event(
    db: Session,
    *,
    action: str,
    status: str,
    target_type: str | None = None,
    target_id: str | None = None,
    request: Request | None = None,
    metadata: Mapping[str, object] | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    """Best-effort isolated admin audit insert.

    Uses the current session bind only to create a separate transaction; never commits
    or rolls back the caller's session.
    """
    if status not in AUDIT_STATUSES:
        status = "failed"
    bind = db.get_bind()
    AuditSession = sessionmaker(bind=bind, autoflush=False, autocommit=False)
    try:
        with AuditSession() as audit_db:
            audit_db.add(
                AdminAuditEvent(
                    actor_kind="admin_technical_key",
                    actor_label="technical_admin",
                    action=_safe_text(action, 128) or "unknown",
                    status=status,
                    target_type=_safe_text(target_type, 128),
                    target_id=_safe_text(target_id, 128),
                    request_method=_safe_text(request.method, 16) if request else None,
                    request_path=_safe_text(request.url.path, 255) if request else None,
                    ip_hash=None,
                    user_agent_hash=None,
                    metadata_json=_safe_metadata(action, metadata),
                    error_type=_safe_text(error_type, 128),
                    error_message=_safe_text(error_message, 500),
                )
            )
            audit_db.commit()
    except Exception as exc:  # pragma: no cover - exercised by behavior tests via log only
        logger.warning("admin audit event write failed: %s", _safe_text(type(exc).__name__, 80))
