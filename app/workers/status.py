from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

PARSER_STATUS_FIELDS = (
    "preferred_engine",
    "selected_first_engine",
    "engine_used",
    "fallback_used",
    "engine_fallback_count",
    "engine_skip_recent_failure_count",
    "block_detected_count",
    "engine_error_count",
    "timeout_failure_count",
    "timeout_retry_attempt_count",
    "timeout_retry_success_count",
    "browser_driver_crash_count",
    "browser_driver_crash_retry_attempt_count",
    "browser_driver_crash_retry_success_count",
    "close_failure_after_driver_crash_count",
    "proxy_failure_count",
    "proxy_quarantine_on_failure_count",
    "session_open_count",
    "session_reuse_count",
    "session_evict_count",
    "session_close_failure_count",
    "layout_changed_hint",
)

_SECRET_KEY_RE = re.compile(
    r"(secret|token|api[_-]?key|password|passwd|authorization|cookie|proxy[_-]?urls?|webhook[_-]?url)",
    re.IGNORECASE,
)
_URL_WITH_CREDENTIALS_RE = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(secret|token|api[_-]?key|password|passwd)=([^\s;&]+)"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact_text(value: str, *, max_chars: int | None = None) -> str:
    redacted = _URL_WITH_CREDENTIALS_RE.sub(r"\1***:***@", value)
    redacted = _URL_RE.sub("[redacted-url]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1=[redacted]", redacted)
    if max_chars is not None and len(redacted) > max_chars:
        redacted = redacted[:max_chars]
    return redacted


def _sanitize_value(key: str, value: Any) -> Any:
    if _SECRET_KEY_RE.search(key):
        return "[redacted]" if value else ""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(k): _sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(key, item) for item in value]
    return value


def _parser_status(parser_stats: dict[str, Any] | None) -> dict[str, Any]:
    stats = parser_stats or {}
    return {field: _sanitize_value(field, stats.get(field)) for field in PARSER_STATUS_FIELDS}


def build_worker_status(
    *,
    cycle_started_at: datetime,
    cycle_finished_at: datetime | None = None,
    cycle_ok: bool,
    searches_processed: int = 0,
    result_count: int | None = None,
    parser_stats: dict[str, Any] | None = None,
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    finished_at = cycle_finished_at or utc_now()
    error_type: str | None = None
    error_text = ""
    if error is not None:
        error_type = error.__class__.__name__ if isinstance(error, BaseException) else "Error"
        error_text = _redact_text(str(error), max_chars=500)

    payload: dict[str, Any] = {
        "cycle_error": error_text,
        "cycle_error_type": error_type,
        "cycle_finished_at": utc_iso(finished_at),
        "cycle_ok": bool(cycle_ok),
        "cycle_started_at": utc_iso(cycle_started_at),
        "result_count": int(result_count if result_count is not None else searches_processed),
        "searches_processed": int(searches_processed),
        "updated_at": utc_iso(finished_at),
    }
    payload.update(_parser_status(parser_stats))
    return {str(k): _sanitize_value(str(k), v) for k, v in payload.items()}


def write_worker_status_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            temp_name = tmp.name
            json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temp_name, target)
        try:
            target.chmod(0o644)
        except OSError:
            pass
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def read_worker_status(path: str | Path) -> dict[str, Any]:
    status_path = Path(path)
    if not status_path.exists():
        return {"state": "missing", "path": str(status_path), "payload": None, "error": ""}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"state": "corrupt", "path": str(status_path), "payload": None, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"state": "corrupt", "path": str(status_path), "payload": None, "error": "status payload is not an object"}
    return {"state": "exists", "path": str(status_path), "payload": payload, "error": ""}


def _parse_utc_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summarize_worker_status(
    status: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 180,
) -> dict[str, Any]:
    current_time = now or utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    state = status.get("state") or "missing"
    payload = status.get("payload") if isinstance(status.get("payload"), dict) else {}
    updated_at = payload.get("updated_at")
    parsed_updated_at = _parse_utc_iso(updated_at)
    age_seconds = None
    if parsed_updated_at is not None:
        age_seconds = max(0, int((current_time - parsed_updated_at).total_seconds()))
    stale = state == "exists" and (age_seconds is None or age_seconds > stale_after_seconds)
    cycle_ok = payload.get("cycle_ok") if state == "exists" else None

    if state == "corrupt":
        badge = {"label": "Corrupt status file", "color": "red"}
    elif state != "exists":
        badge = {"label": "Missing status file", "color": "yellow"}
    elif cycle_ok is False:
        badge = {"label": "Last cycle failed", "color": "red"}
    elif stale:
        badge = {"label": "Stale", "color": "yellow"}
    else:
        badge = {"label": "Fresh", "color": "green"}

    return {
        "state": state,
        "path": status.get("path", ""),
        "payload": payload,
        "error": status.get("error", ""),
        "updated_at": updated_at if isinstance(updated_at, str) else "",
        "age_seconds": age_seconds,
        "stale": stale,
        "cycle_ok": cycle_ok,
        "badge": badge,
        "stale_after_seconds": stale_after_seconds,
    }
