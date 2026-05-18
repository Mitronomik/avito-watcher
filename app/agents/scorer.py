import json
import logging

import httpx

from app.core.config import settings
from app.parsers.schemas import ListingCard

logger = logging.getLogger(__name__)

_SCORER_MAX_RETRIES = 3
_SCORER_RETRY_DELAY_SEC = 2.0


def _validate_score_result(parsed: dict) -> dict:
    """Ensure score is int 0-100; coerce or default if invalid."""
    raw_score = parsed.get("score", 0)
    try:
        score = int(float(str(raw_score)))
    except (TypeError, ValueError):
        score = 0
    parsed["score"] = max(0, min(100, score))

    if not isinstance(parsed.get("summary"), str):
        parsed["summary"] = str(parsed.get("summary", ""))

    if not isinstance(parsed.get("tags"), list):
        parsed["tags"] = []

    return parsed


class ListingScorer:
    async def score(self, card: ListingCard) -> dict:
        prompt = f'''Оцени объявление недвижимости по релевантности от 0 до 100.
Верни строго JSON формата {{"score": int, "summary": str, "tags": [str]}}.
Объявление: title={card.title}, price={card.price}, address={card.address}, area={card.area_m2}, rooms={card.rooms}
'''
        last_exc: Exception | None = None
        for attempt in range(1, _SCORER_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{settings.ollama_base_url}/api/chat",
                        json={
                            "model": settings.ollama_model,
                            "stream": False,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": "Ты аналитик недвижимости. Отвечай строго валидным JSON без markdown.",
                                },
                                {"role": "user", "content": prompt},
                            ],
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    content = data.get("message", {}).get("content", "{}")
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        parsed = {"score": 0, "summary": content[:500], "tags": ["raw"]}
                    return _validate_score_result(parsed)

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[scorer] attempt %d/%d failed: %s",
                    attempt,
                    _SCORER_MAX_RETRIES,
                    exc,
                )
                if attempt < _SCORER_MAX_RETRIES:
                    import asyncio
                    await asyncio.sleep(_SCORER_RETRY_DELAY_SEC)

        raise RuntimeError(f"[scorer] all {_SCORER_MAX_RETRIES} attempts failed") from last_exc
