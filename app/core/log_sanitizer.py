"""Runtime log redaction helpers.

This module sanitizes rendered log text only.  It must never mutate configured
URLs, request arguments, exception objects, or other runtime state.
"""

from __future__ import annotations

import logging
import re

REDACTED = "<redacted>"
SANITIZER_ERROR = "<log redaction failed>"

_SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "signature",
    "user_content_key",
    "authorization",
    "password",
}

_SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "signature",
    "authorization",
    "bearer",
    "user_content_key",
}

_APPS_SCRIPT_RE = re.compile(
    r"https://script\.google\.com/macros/s/[^\s/?#]+/(exec|dev)(?:\?[^\s#]*)?",
    re.IGNORECASE,
)
_GOOGLE_ECHO_RE = re.compile(
    r"https://script\.googleusercontent\.com/macros/echo(?:\?[^\s#]*)?",
    re.IGNORECASE,
)
_QUERY_PARAM_RE = re.compile(
    r"(?P<prefix>[?&](?P<key>[A-Za-z0-9_\-]+)=)(?P<value>[^\s&#]+)",
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+([^\s,;\]}')\"]+)")
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(Authorization\s*[:=]\s*Bearer\s+)([^\s,;\]}')\"]+)"
)
_API_KEY_HEADER_RE = re.compile(
    r"(?i)\b(X-API-Key\s*[:=]\s*)([^\s,;\]}')\"]+)"
)
_QUOTED_KV_RE = re.compile(
    r"(?P<prefix>(?P<key_quote>['\"]?)(?P<key>[A-Za-z0-9_\-]+)(?P=key_quote)\s*[:=]\s*)"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)
_BARE_KV_RE = re.compile(
    r"(?P<prefix>\b(?P<key>[A-Za-z0-9_\-]+)\s*[:=]\s*)(?P<value>[^\s,;&\]}}]+)"
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.strip("'\"").lower()
    if lowered in _SENSITIVE_KEY_NAMES:
        return True
    parts = [part for part in re.split(r"[_\-]+", lowered) if part]
    return any(part in {"token", "password", "secret", "signature", "authorization", "bearer"} for part in parts)


def _safe_string(value: object) -> str:
    try:
        return value if isinstance(value, str) else str(value)
    except Exception:
        return "<unprintable>"


def _redact_apps_script(match: re.Match[str]) -> str:
    return f"https://script.google.com/.../{match.group(1).lower()}"


def _redact_echo(match: re.Match[str]) -> str:
    return "https://script.googleusercontent.com/macros/echo?<redacted>"


def _redact_query_param(match: re.Match[str]) -> str:
    key = match.group("key").lower()
    if key in _SENSITIVE_QUERY_KEYS:
        return f"{match.group('prefix')}{REDACTED}"
    return match.group(0)


def _redact_quoted_kv(match: re.Match[str]) -> str:
    if not _is_sensitive_key(match.group("key")):
        return match.group(0)
    return f"{match.group('prefix')}{match.group('quote')}{REDACTED}{match.group('quote')}"


def _redact_bare_kv(match: re.Match[str]) -> str:
    if not _is_sensitive_key(match.group("key")):
        return match.group(0)
    prefix = match.group("prefix")
    if prefix.strip().lower().startswith("authorization"):
        return match.group(0)
    return f"{prefix}{REDACTED}"


def sanitize_log_text(value: object) -> str:
    """Return a deterministic, idempotent redacted string for log rendering."""
    try:
        text = _safe_string(value)
        text = _APPS_SCRIPT_RE.sub(_redact_apps_script, text)
        text = _GOOGLE_ECHO_RE.sub(_redact_echo, text)
        text = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
        text = _API_KEY_HEADER_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
        text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
        text = _QUERY_PARAM_RE.sub(_redact_query_param, text)
        text = _QUOTED_KV_RE.sub(_redact_quoted_kv, text)
        text = _BARE_KV_RE.sub(_redact_bare_kv, text)
        return text
    except Exception:
        return SANITIZER_ERROR


class RedactingFormatter(logging.Formatter):
    """Formatter wrapper that sanitizes fully rendered log output.

    The wrapped formatter remains responsible for message formatting, time
    formatting, exception rendering, formatting style, and any custom formatter
    behavior.  This wrapper only redacts the final string returned by it.
    """

    def __init__(self, wrapped: logging.Formatter | None = None):
        super().__init__()
        self.wrapped = wrapped or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - logging API name
        return sanitize_log_text(self.wrapped.format(record))


class RedactingFilter(logging.Filter):
    """Compatibility no-op filter.

    Redaction must happen after %-style interpolation, so this filter never
    changes ``record.msg`` or ``record.args``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return True


def install_log_redaction() -> None:
    """Install rendered-output redaction on existing root handlers."""
    root = logging.getLogger()
    for handler in root.handlers:
        formatter = handler.formatter
        if not isinstance(formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(formatter))
