"""Shared block/captcha detection signals for Avito parser and browser engines."""

BLOCK_SIGNALS = (
    "captcha",
    "капча",
    "подтвердите, что вы не робот",
    "проверка безопасности",
    "доступ ограничен",
    "доступ заблокирован",
    "слишком много запросов",
    "too many requests",
    "access denied",
    "verify you are human",
    "robot check",
    "проблема с ip",
    "blocked",
    "временно недоступен",
)


def looks_like_block_or_captcha(title: str, body_text: str, *, body_limit: int | None = None) -> bool:
    """Return True when title/body contains known block or captcha signals."""
    body = body_text if body_limit is None else body_text[:body_limit]
    content = f"{title} {body}".lower()
    return any(keyword in content for keyword in BLOCK_SIGNALS)
