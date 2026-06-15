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
    "key",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "signature",
    "sig",
    "user_content_key",
    "authorization",
    "auth",
    "password",
    "passwd",
    "cookie",
}

_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "authorization",
    "auth",
    "password",
    "passwd",
    "cookie",
    "webhook",
    "smtp",
    "telegram",
    "provider_key",
    "access_key",
    "bearer",
    "proxy",
)

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

_KEY_PATTERN = "|".join(re.escape(key) for key in sorted(_SENSITIVE_KEY_FRAGMENTS, key=len, reverse=True))
_QUOTED_KV_RE = re.compile(
    rf"(?i)(?P<prefix>['\"]?[A-Za-z0-9_\-]*(?:{_KEY_PATTERN})[A-Za-z0-9_\-]*['\"]?\s*[:=]\s*)(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)
_BARE_KV_RE = re.compile(
    rf"(?i)(?P<prefix>\b[A-Za-z0-9_\-]*(?:{_KEY_PATTERN})[A-Za-z0-9_\-]*\s*[:=]\s*)(?P<value>[^\s,;&\]}}]+)"
)



def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)

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
    return f"{match.group('prefix')}{match.group('quote')}{REDACTED}{match.group('quote')}"


def _redact_bare_kv(match: re.Match[str]) -> str:
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
    """Formatter that sanitizes the final formatted log line including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - logging API name
        return sanitize_log_text(super().format(record))


def _sanitize_arg(value: object) -> object:
    sanitized = sanitize_log_text(value)
    return sanitized if sanitized != _safe_string(value) else value


class RedactingFilter(logging.Filter):
    """Best-effort record-level redaction without mutating original args objects."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_log_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: (REDACTED if _is_sensitive_key(str(key)) else _sanitize_arg(value))
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(_sanitize_arg(value) for value in record.args)
            else:
                record.args = _sanitize_arg(record.args)
        return True


def install_log_redaction() -> None:
    """Install redaction on existing root handlers used by app and worker logs."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(RedactingFilter())
        formatter = handler.formatter
        if not isinstance(formatter, RedactingFormatter):
            if formatter is None:
                handler.setFormatter(RedactingFormatter())
            else:
                handler.setFormatter(
                    RedactingFormatter(formatter._fmt, formatter.datefmt)  # noqa: SLF001
                )
