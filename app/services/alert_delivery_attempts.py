import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_FRAGMENTS = (
    "secret",
    "token",
    "api_key",
    "apikey",
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
    "refresh_token",
    "bearer",
)
MAX_ERROR_LENGTH = 1000
_REDACTED = "[REDACTED]"


def compute_alert_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS)


def _redact_url(match: re.Match[str]) -> str:
    url = match.group(0)
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = urlencode(
        [
            (key, _REDACTED if _is_sensitive_name(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def sanitize_alert_delivery_error(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        text = f"{error.__class__.__name__}: {error}"
    else:
        text = str(error)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    text = re.sub(r"https?://[^\s]+", _redact_url, text)
    text = re.sub(r"(?i)(Authorization\s*:\s*Bearer\s+)([^\s,;&]+)", rf"\1{_REDACTED}", text)
    text = re.sub(r"(?i)(Bearer\s+)([^\s,;&]+)", rf"\1{_REDACTED}", text)
    for fragment in SENSITIVE_FRAGMENTS:
        text = re.sub(
            rf"(?i)({re.escape(fragment)}[\w.-]*\s*[:=]\s*)([^\s,;&]+)",
            rf"\1{_REDACTED}",
            text,
        )
    return text[:MAX_ERROR_LENGTH]
