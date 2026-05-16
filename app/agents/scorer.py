import json
import httpx
from app.core.config import settings
from app.parsers.schemas import ListingCard


class ListingScorer:
    async def score(self, card: ListingCard) -> dict:
        prompt = f'''Оцени объявление недвижимости по релевантности от 0 до 100.
Верни строго JSON формата {{"score": int, "summary": str, "tags": [str]}}.
Объявление: title={card.title}, price={card.price}, address={card.address}, area={card.area_m2}, rooms={card.rooms}
'''
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "Ты аналитик недвижимости. Отвечай строго валидным JSON без markdown."},
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
            return parsed
