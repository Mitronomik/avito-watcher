import asyncio
import logging

from app.agents.llm_providers import make_provider, resolve_llm_runtime_config
from app.parsers.schemas import ListingCard

logger = logging.getLogger(__name__)


class ListingScorer:
    async def score(self, card: ListingCard) -> dict:
        cfg = resolve_llm_runtime_config()
        provider = make_provider(cfg)
        if cfg.provider == "off":
            result = await provider.score(card)
            logger.info("[scorer] provider=%s model=%s status=%s", cfg.provider, cfg.model, result.get("status"))
            return result

        max_attempts = cfg.max_retries + 1
        last_error_type: str | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await provider.score(card)
                logger.info(
                    "[scorer] provider=%s model=%s attempt=%d/%d status=%s",
                    cfg.provider,
                    cfg.model,
                    attempt,
                    max_attempts,
                    result.get("status"),
                )
                return result
            except Exception as exc:
                last_error_type = exc.__class__.__name__
                logger.warning(
                    "[scorer] provider=%s model=%s attempt=%d/%d status=failed error_type=%s",
                    cfg.provider,
                    cfg.model,
                    attempt,
                    max_attempts,
                    last_error_type,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(cfg.retry_delay_sec)

        return {
            "score": None,
            "summary": "",
            "tags": [],
            "status": "failed",
            "provider": cfg.provider,
            "model": cfg.model,
            "prompt_version": cfg.prompt_version,
            "error_type": last_error_type or "unknown_error",
        }
