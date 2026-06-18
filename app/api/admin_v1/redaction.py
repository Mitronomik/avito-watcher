from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
SECRET_KEYS = {
    "api_key", "apikey", "key", "token", "access_token", "refresh_token", "secret", "signature", "sig",
    "password", "authorization", "cookie", "x-api-key", "admin_ui_read_key", "admin_ui_write_key",
    "admin_ui_technical_write_key", "webhook_url", "google_sheets_webhook_url", "google_sheets_webhook_secret",
    "smtp_password", "llm_api_key", "openai_api_key", "telegram_bot_token", "user_content_key",
    "set-cookie", "database_url", "provider_raw_payload", "provider_payload", "raw_payload",
}
URL_SECRET_PARAMS = {"token", "secret", "key", "api_key", "signature", "sig", "user_content_key"}
SECRET_KEY_FRAGMENTS = (
    "api_key", "apikey", "token", "secret", "password", "authorization", "cookie",
    "set-cookie", "webhook", "smtp_password", "telegram_bot_token", "openai_api_key",
    "database_url",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bAuthorization:\s*.+"),
    re.compile(r"(?i)\bCookie:\s*.+"),
    re.compile(r"(?i)\bX-API-Key:\s*.+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"),
)


def _is_secret_key(key: object) -> bool:
    normalized = str(key).lower()
    return normalized in SECRET_KEYS or any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS)


def _redact_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc or not parts.query:
        return value
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    changed = False
    redacted = []
    for key, param_value in pairs:
        if key.lower() in URL_SECRET_PARAMS:
            redacted.append((key, REDACTED))
            changed = True
        else:
            redacted.append((key, param_value))
    if not changed:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted), parts.fragment))


def redact_api_response(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: (REDACTED if _is_secret_key(key) else redact_api_response(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_api_response(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_api_response(item) for item in value)
    if isinstance(value, str):
        redacted = _redact_url(value)
        for pattern in SECRET_VALUE_PATTERNS:
            redacted = pattern.sub(REDACTED, redacted)
        return redacted
    return value
