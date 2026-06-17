from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
SECRET_KEYS = {
    "api_key", "apikey", "key", "token", "access_token", "refresh_token", "secret", "signature", "sig",
    "password", "authorization", "cookie", "x-api-key", "admin_ui_read_key", "admin_ui_write_key",
    "admin_ui_technical_write_key", "webhook_url", "google_sheets_webhook_url", "google_sheets_webhook_secret",
    "smtp_password", "llm_api_key", "openai_api_key", "telegram_bot_token", "user_content_key",
}
URL_SECRET_PARAMS = {"token", "secret", "key", "api_key", "signature", "sig", "user_content_key"}


def _is_secret_key(key: object) -> bool:
    return str(key).lower() in SECRET_KEYS


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
        return _redact_url(value)
    return value
