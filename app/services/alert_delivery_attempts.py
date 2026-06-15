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
    "api-key",
    "apikey",
    "x_api_key",
    "x-api-key",
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
_URL_REDACTED = "[REDACTED_URL]"


def compute_alert_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_name(name: str) -> str:
    return name.lower().replace("-", "_")


def _is_sensitive_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return any(_normalize_name(fragment) in normalized for fragment in SENSITIVE_FRAGMENTS)


def _is_sensitive_url(host: str, path: str) -> bool:
    combined = f"{host}{path}".lower()
    return bool(
        "telegram" in combined
        or "webhook" in combined
        or "hooks" in combined
        or re.search(r"(^|[./_-])hook([./_-]|$)", combined)
        or re.search(r"/bot[^/?#]+", path, flags=re.IGNORECASE)
    )


def _redact_url(match: re.Match[str]) -> str:
    url = match.group(0)
    parts = urlsplit(url)
    if parts.netloc.lower() == "script.google.com" and parts.path.lower().startswith("/macros/s/"):
        return "https://script.google.com/.../exec"
    if _is_sensitive_url(parts.netloc, parts.path):
        return _URL_REDACTED
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
    text = re.sub(
        r"(?i)(\bauthorization\s*[:=]\s*)(?:bearer|basic|token)?\s*[^\s,;&]+",
        rf"\1{_REDACTED}",
        text,
    )
    text = re.sub(r"https?://[^\s]+", _redact_url, text)
    text = re.sub(r"(?i)(\bBearer\s+)([^\s,;&]+)", rf"\1{_REDACTED}", text)
    sensitive_key = (
        r"(?:x[-_]?api[-_]?key|api[-_]?key|apikey|secret|token|authorization|auth|"
        r"password|passwd|cookie|webhook|smtp|telegram|provider[-_]?key|"
        r"access[-_]?key|refresh[-_]?token|bearer)"
    )
    text = re.sub(
        rf"(?i)(?<![?&])\b({sensitive_key}[\w.-]*\s*[:=]\s*)([^\s,;]+)",
        rf"\1{_REDACTED}",
        text,
    )
    return text[:MAX_ERROR_LENGTH]
