import json
import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.parsers.schemas import ListingCard

logger = logging.getLogger(__name__)

ProviderName = str


@dataclass(frozen=True)
class LLMRuntimeConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_sec: int
    max_retries: int
    retry_delay_sec: float
    prompt_version: str


def resolve_llm_runtime_config() -> LLMRuntimeConfig:
    provider = settings.llm_provider
    base_url = settings.llm_base_url
    model = settings.llm_model
    if provider == "ollama":
        base_url = base_url or settings.ollama_base_url
        model = model or settings.ollama_model
    return LLMRuntimeConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=settings.llm_api_key,
        timeout_sec=max(int(settings.llm_timeout_sec), 1),
        max_retries=max(int(settings.llm_max_retries), 0),
        retry_delay_sec=max(float(settings.llm_retry_delay_sec), 0.0),
        prompt_version=settings.llm_prompt_version,
    )


def _truncate(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _sanitize_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        item_text = str(item).strip()
        if not item_text:
            continue
        tags.append(_truncate(item_text, 80))
        if len(tags) >= 10:
            break
    return tags


def _safe_result(provider: str, model: str, prompt_version: str, status: str, error_type: str | None = None) -> dict:
    return {
        "score": None,
        "summary": "",
        "tags": [],
        "status": status,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "error_type": error_type,
    }


def normalize_llm_result(parsed: dict, provider: str, model: str, prompt_version: str, status: str = "success", error_type: str | None = None) -> dict:
    raw_score = parsed.get("score")
    score = None
    if raw_score is not None:
        try:
            score = max(0, min(100, int(float(str(raw_score)))))
        except (TypeError, ValueError):
            score = None
    return {
        "score": score,
        "summary": _truncate(parsed.get("summary", ""), 700),
        "tags": _sanitize_tags(parsed.get("tags", [])),
        "status": status,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "error_type": error_type,
    }


def build_llm_prompt_payload(card: ListingCard) -> dict:
    item_page = card.raw.get("item_page") if isinstance(card.raw, dict) else {}
    item_page = item_page if isinstance(item_page, dict) else {}
    badges = item_page.get("badges") if isinstance(item_page.get("badges"), list) else []
    return {
        "title": card.title,
        "price": card.price,
        "area_m2": card.area_m2,
        "rooms": card.rooms,
        "address": card.address,
        "published_label": card.published_label,
        "url": card.url,
        "item_page": {
            "description": _truncate(item_page.get("description", ""), 2000),
            "seller_type": item_page.get("seller_type", ""),
            "seller_name": item_page.get("seller_name", ""),
            "address_detail": item_page.get("address_detail", ""),
            "metro": item_page.get("metro", ""),
            "walking_time_label": item_page.get("walking_time_label", ""),
            "badges": [str(b)[:120] for b in badges[:20]],
            "image_count": item_page.get("image_count"),
        },
    }


def build_llm_user_prompt(card: ListingCard, prompt_version: str) -> str:
    payload = build_llm_prompt_payload(card)
    return (
        f"Версия промпта: {prompt_version}. Оцени объявление недвижимости. "
        "Верни строго JSON {\"score\": int|null, \"summary\": str, \"tags\": [str]} без markdown. "
        "Ограничения: summary <= 700 символов, tags <= 10. Данные:\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


class BaseProvider:
    name = "off"

    def __init__(self, config: LLMRuntimeConfig) -> None:
        self.config = config

    async def score(self, card: ListingCard) -> dict:
        raise NotImplementedError


class OffProvider(BaseProvider):
    name = "off"

    async def score(self, card: ListingCard) -> dict:
        return _safe_result(self.name, self.config.model, self.config.prompt_version, "skipped")


class OllamaProvider(BaseProvider):
    name = "ollama"

    async def score(self, card: ListingCard) -> dict:
        prompt = build_llm_user_prompt(card, self.config.prompt_version)
        async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
            response = await client.post(
                f"{self.config.base_url}/api/chat",
                json={
                    "model": self.config.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": "Ты аналитик недвижимости. Отвечай строго валидным JSON без markdown."},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "{}")
            parsed = json.loads(content)
            return normalize_llm_result(parsed, self.name, self.config.model, self.config.prompt_version)


class OpenAICompatibleProvider(BaseProvider):
    name = "openai_compatible"

    async def score(self, card: ListingCard) -> dict:
        prompt = build_llm_user_prompt(card, self.config.prompt_version)
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
            response = await client.post(
                f"{self.config.base_url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "system", "content": "You are a real-estate analyst. Return strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
            parsed = json.loads(content)
            return normalize_llm_result(parsed, self.name, self.config.model, self.config.prompt_version)


def make_provider(config: LLMRuntimeConfig) -> BaseProvider:
    if config.provider == "ollama":
        return OllamaProvider(config)
    if config.provider == "openai_compatible":
        return OpenAICompatibleProvider(config)
    return OffProvider(config)
