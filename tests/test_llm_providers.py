import json

import pytest

from app.agents import llm_providers as providers
from app.agents.llm_providers import LLMRuntimeConfig, OffProvider, OpenAICompatibleProvider, OllamaProvider, normalize_llm_result
from app.parsers.schemas import ListingCard


def _card() -> ListingCard:
    return ListingCard(external_id="1", url="u", title="t", price=1, address="a", raw={})


class _Resp:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class _Client:
    def __init__(self, response_payload, sink):
        self.response_payload = response_payload
        self.sink = sink
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def post(self, url, **kwargs):
        self.sink.append((url, kwargs))
        return _Resp(self.response_payload)


@pytest.mark.asyncio
async def test_off_provider_returns_skipped():
    cfg = LLMRuntimeConfig("off", "", "", "", 10, 0, 0.0, "v1")
    result = await OffProvider(cfg).score(_card())
    assert result["status"] == "skipped"


def test_normalize_clamps_and_bounds():
    out = normalize_llm_result({"score": 999, "summary": "x" * 1000, "tags": list(range(30))}, "ollama", "m", "v")
    assert out["score"] == 100
    assert len(out["summary"]) == 700
    assert len(out["tags"]) == 10


@pytest.mark.asyncio
async def test_ollama_provider_parses_json(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda timeout: _Client({"message": {"content": json.dumps({"score": 88, "summary": "ok", "tags": ["a"]})}}, calls))
    cfg = LLMRuntimeConfig("ollama", "http://x", "m", "", 10, 0, 0.0, "v1")
    out = await OllamaProvider(cfg).score(_card())
    assert out["status"] == "success"
    assert out["score"] == 88


@pytest.mark.asyncio
async def test_openai_compatible_authorization_header_optional(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda timeout: _Client({"choices": [{"message": {"content": '{"score": 10, "summary": "s", "tags": []}'}}]}, calls))
    cfg = LLMRuntimeConfig("openai_compatible", "http://x", "m", "secret", 10, 0, 0.0, "v1")
    await OpenAICompatibleProvider(cfg).score(_card())
    assert calls[0][1]["headers"]["Authorization"] == "Bearer secret"

    calls.clear()
    cfg2 = LLMRuntimeConfig("openai_compatible", "http://x", "m", "", 10, 0, 0.0, "v1")
    await OpenAICompatibleProvider(cfg2).score(_card())
    assert "Authorization" not in calls[0][1]["headers"]


def test_prompt_requests_decision_oriented_commercial_summary():
    prompt = providers.build_llm_user_prompt(_card(), "v-decision")
    assert "decision-oriented mini-analysis" in prompt
    assert "verdict: strong / medium / weak" in prompt
    assert "почему объект может быть интересен" in prompt
    assert "основные риски" in prompt
    assert "что проверить перед звонком" in prompt
    assert "подходящие типы арендаторов/бизнесов" in prompt
    assert "stale publication" in prompt
    assert "ambiguity" in prompt
    assert "субаренда 16-38 м² внутри помещения 92 м²" in prompt
    assert "80-100 strong lead" in prompt
    assert "60-79 worth checking" in prompt
    assert "30-59 weak/unclear" in prompt
    assert "0-29 likely low priority or mismatch" in prompt


def test_prompt_payload_includes_published_at_for_staleness_analysis():
    from datetime import datetime

    card = ListingCard(external_id="1", url="u", title="t", published_at=datetime(2026, 5, 31, 10, 30, 0))
    payload = providers.build_llm_prompt_payload(card)
    assert payload["published_at"] == "2026-05-31T10:30:00"


def test_normalize_result_keeps_llm_summary_schema_compatible():
    out = normalize_llm_result({"score": 75, "summary": "medium: worth checking", "tags": ["rent"]}, "openai_compatible", "deepseek", "v2")
    assert set(out) == {"score", "summary", "tags", "status", "provider", "model", "prompt_version", "error_type"}
    assert out["score"] == 75
    assert out["summary"] == "medium: worth checking"
    assert out["tags"] == ["rent"]
    assert out["status"] == "success"
    assert out["provider"] == "openai_compatible"
    assert out["model"] == "deepseek"
    assert out["prompt_version"] == "v2"
