from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_task import AgentTask
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.search_job import SearchJob
from app.services.agent_task_runner import AgentTaskHandlerResult

REVIEW_COPILOT_TASK_TYPE = "review_copilot"
DEFAULT_REVIEW_COPILOT_PROMPT_VERSION = "review-copilot-v1"

ReviewNextAction = Literal[
    "open_listing",
    "call_owner",
    "needs_more_data",
    "ready_for_manual_review",
    "likely_not_interesting",
]


class ReviewCopilotError(ValueError):
    """Base user-visible ReviewCopilot task failure."""


class ReviewCopilotPayloadError(ReviewCopilotError):
    """Task payload cannot safely identify a listing analysis."""


class ReviewCopilotResolutionError(ReviewCopilotError):
    """Task payload points to missing, mismatched, or ambiguous stored data."""


class ReviewCopilotProviderError(ReviewCopilotError):
    """Provider call or provider response failed."""


class ReviewCopilotResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=1500)
    recommended_next_action: ReviewNextAction
    questions: list[str] = Field(default_factory=list, max_length=10)
    risk_explanation: list[str] = Field(default_factory=list, max_length=10)
    positive_factors: list[str] = Field(default_factory=list, max_length=10)
    missing_data: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)
    model: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)

    @field_validator("summary", "model", "prompt_version", mode="before")
    @classmethod
    def _trim_required_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("questions", "risk_explanation", "positive_factors", "missing_data", mode="before")
    @classmethod
    def _trim_string_list(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [item.strip() if isinstance(item, str) else item for item in value]

    @field_validator("questions", "risk_explanation", "positive_factors", "missing_data")
    @classmethod
    def _validate_string_list(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str):
                raise ValueError("all list items must be strings")
            if not item.strip():
                raise ValueError("list items must be non-empty")
            if len(item) > 500:
                raise ValueError("list items must be <= 500 characters")
        return value


@dataclass(frozen=True)
class ReviewCopilotRuntimeConfig:
    enabled: bool
    provider: str
    base_url: str
    api_key: str
    model: str
    prompt_version: str
    timeout_sec: int
    max_retries: int


def resolve_review_copilot_config() -> ReviewCopilotRuntimeConfig:
    provider = settings.llm_review_copilot_provider
    return ReviewCopilotRuntimeConfig(
        enabled=bool(settings.llm_review_copilot_enabled),
        provider=provider,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_review_copilot_model or settings.llm_model,
        prompt_version=settings.llm_review_copilot_prompt_version or DEFAULT_REVIEW_COPILOT_PROMPT_VERSION,
        timeout_sec=max(int(settings.llm_review_copilot_timeout_sec), 1),
        max_retries=max(int(settings.llm_review_copilot_max_retries), 0),
    )


class OpenAICompatibleReviewCopilotClient:
    def __init__(self, config: ReviewCopilotRuntimeConfig) -> None:
        self.config = config

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        if not self.config.base_url:
            raise ReviewCopilotProviderError("LLM base URL is required for ReviewCopilot")
        if not self.config.model:
            raise ReviewCopilotProviderError("LLM model is required for ReviewCopilot")

        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        last_error: Exception | None = None
        for _attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(timeout=self.config.timeout_sec) as client:
                    response = client.post(
                        f"{self.config.base_url}/v1/chat/completions",
                        headers=headers,
                        json={
                            "model": self.config.model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "response_format": {"type": "json_object"},
                        },
                    )
                    response.raise_for_status()
                    content = response.json().get("choices", [{}])[0].get("message", {}).get("content")
                    if not isinstance(content, str) or not content.strip():
                        raise ReviewCopilotProviderError("Provider returned empty content")
                    return content
            except Exception as exc:  # noqa: BLE001 - provider errors become task failures.
                last_error = exc
        raise ReviewCopilotProviderError(str(last_error) if last_error else "Provider call failed")


class ReviewCopilotAgentTaskHandler:
    def __init__(
        self,
        db: Session,
        *,
        config: ReviewCopilotRuntimeConfig | None = None,
        client: OpenAICompatibleReviewCopilotClient | None = None,
    ) -> None:
        self.db = db
        self.config = config or resolve_review_copilot_config()
        self.client = client

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        if task.task_type != REVIEW_COPILOT_TASK_TYPE:
            raise ReviewCopilotPayloadError(f"Unsupported task type for ReviewCopilot: {task.task_type}")
        if not self.config.enabled:
            return AgentTaskHandlerResult(
                status="skipped",
                result_json={
                    "reason": "review_copilot_disabled",
                    "task_type": task.task_type,
                    "prompt_version": self.config.prompt_version,
                },
            )
        self._preflight_config()

        listing, analysis = self._resolve_listing_and_analysis(task)
        system_prompt = build_review_copilot_system_prompt()
        user_prompt = build_review_copilot_user_prompt(
            listing=listing,
            analysis=analysis,
            search_job=self._get_search_job(analysis.search_job_id),
            prompt_version=self.config.prompt_version,
        )
        client = self.client or self._make_client()
        content = client.complete_json(system_prompt=system_prompt, user_prompt=user_prompt)
        result = validate_review_copilot_json(
            content,
            model_name=self.config.model,
            prompt_version=self.config.prompt_version,
        )
        return AgentTaskHandlerResult(status="success", result_json=result.model_dump())

    def _preflight_config(self) -> None:
        if self.config.provider != "openai_compatible":
            raise ReviewCopilotProviderError(
                f"Unsupported ReviewCopilot provider: {self.config.provider}"
            )
        if not self.config.base_url.strip():
            raise ReviewCopilotProviderError("LLM base URL is required for ReviewCopilot")
        if not self.config.model.strip():
            raise ReviewCopilotProviderError("LLM model is required for ReviewCopilot")
        if not self.config.prompt_version.strip():
            raise ReviewCopilotProviderError("LLM prompt version is required for ReviewCopilot")

    def _make_client(self) -> OpenAICompatibleReviewCopilotClient:
        return OpenAICompatibleReviewCopilotClient(self.config)

    def _resolve_listing_and_analysis(self, task: AgentTask) -> tuple[Listing, ListingAnalysis]:
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        analysis_id = _optional_int(payload.get("analysis_id") or payload.get("listing_analysis_id") or task.listing_analysis_id)
        listing_external_id = _optional_str(payload.get("listing_external_id") or task.listing_external_id)

        if analysis_id is None and listing_external_id is None:
            raise ReviewCopilotPayloadError(
                "ReviewCopilot payload requires analysis_id/listing_analysis_id or listing_external_id"
            )

        if analysis_id is not None:
            analysis = self.db.get(ListingAnalysis, analysis_id)
            if analysis is None:
                raise ReviewCopilotResolutionError(f"Listing analysis not found: {analysis_id}")
            if listing_external_id is not None and analysis.listing_external_id != listing_external_id:
                raise ReviewCopilotResolutionError(
                    "Listing analysis does not belong to requested listing_external_id"
                )
            listing_external_id = analysis.listing_external_id
        else:
            analysis = self._resolve_latest_analysis(task, listing_external_id or "")

        listing = self.db.scalar(select(Listing).where(Listing.external_id == listing_external_id))
        if listing is None:
            raise ReviewCopilotResolutionError(f"Listing not found: {listing_external_id}")
        if analysis.listing_external_id != listing.external_id:
            raise ReviewCopilotResolutionError("Resolved analysis/listing mismatch")
        return listing, analysis

    def _resolve_latest_analysis(self, task: AgentTask, listing_external_id: str) -> ListingAnalysis:
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        profile = _optional_str(payload.get("analysis_profile") or payload.get("profile"))
        search_id = _optional_int(payload.get("search_id") or task.search_job_id)
        context_key = _optional_str(payload.get("context_key") or task.context_key)

        stmt = select(ListingAnalysis).where(
            ListingAnalysis.listing_external_id == listing_external_id,
            ListingAnalysis.status == "success",
        )
        if profile is not None:
            stmt = stmt.where(ListingAnalysis.profile == profile)
        if search_id is not None:
            stmt = stmt.where(ListingAnalysis.search_job_id == search_id)
        if context_key is not None:
            stmt = stmt.where(ListingAnalysis.context_key == context_key)

        matches = list(
            self.db.scalars(
                stmt.order_by(ListingAnalysis.created_at.desc(), ListingAnalysis.id.desc()).limit(2)
            ).all()
        )
        if not matches:
            raise ReviewCopilotResolutionError(
                f"Successful listing analysis not found for listing_external_id={listing_external_id}"
            )
        if len(matches) > 1 and profile is None and search_id is None and context_key is None:
            raise ReviewCopilotResolutionError(
                "Ambiguous listing analysis selection; provide analysis_id, analysis_profile, search_id, or context_key"
            )
        return matches[0]

    def _get_search_job(self, search_job_id: int | None) -> SearchJob | None:
        if search_job_id is None:
            return None
        return self.db.get(SearchJob, search_job_id)


def validate_review_copilot_json(content: str, *, model_name: str, prompt_version: str) -> ReviewCopilotResult:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReviewCopilotProviderError(f"Provider returned invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ReviewCopilotProviderError("Provider JSON must be an object")
    parsed.setdefault("model", model_name)
    parsed.setdefault("prompt_version", prompt_version)
    try:
        return ReviewCopilotResult.model_validate(parsed)
    except ValidationError as exc:
        raise ReviewCopilotProviderError(f"Provider result failed schema validation: {exc}") from exc


def build_review_copilot_system_prompt() -> str:
    return (
        "You are ReviewCopilot for a real estate monitoring system.\n"
        "You do not decide the final score or verdict.\n"
        "You do not create alerts.\n"
        "You do not change filters.\n"
        "You do not recommend automatic system actions.\n"
        "You explain existing deterministic and stored analysis for a human reviewer.\n"
        "Use only the provided data.\n"
        "Do not invent missing facts.\n"
        "If data is missing, say so in missing_data or questions.\n"
        "Do not make a final investment decision.\n"
        "Do not claim legal certainty.\n"
        "Do not claim guaranteed profitability.\n"
        "Do not make market claims without supporting data.\n"
        "Do not change system state.\n"
        "Return only valid JSON matching the schema."
    )


def build_review_copilot_user_prompt(
    *,
    listing: Listing,
    analysis: ListingAnalysis,
    search_job: SearchJob | None,
    prompt_version: str,
) -> str:
    payload = {
        "prompt_version": prompt_version,
        "output_schema": {
            "summary": "non-empty string, max 1500 chars",
            "recommended_next_action": [
                "open_listing",
                "call_owner",
                "needs_more_data",
                "ready_for_manual_review",
                "likely_not_interesting",
            ],
            "questions": "list[str], max 10, item max 500 chars",
            "risk_explanation": "list[str], max 10, item max 500 chars",
            "positive_factors": "list[str], max 10, item max 500 chars",
            "missing_data": "list[str], max 10, item max 500 chars",
            "confidence": "float from 0.0 to 1.0",
            "model": "non-empty string",
            "prompt_version": "non-empty string",
        },
        "listing": {
            "external_id": listing.external_id,
            "title": _bounded(listing.title, 300),
            "price": listing.price,
            "area_m2": listing.area_m2,
            "address": _bounded(listing.address, 300),
            "rooms": _bounded(listing.rooms, 64),
            "published_label": _bounded(listing.published_label, 160),
            "published_at": listing.published_at.isoformat() if listing.published_at else None,
        },
        "analysis": {
            "id": analysis.id,
            "profile": analysis.profile,
            "status": analysis.status,
            "score": analysis.score,
            "verdict": analysis.verdict,
            "analysis_version": analysis.analysis_version,
            "context_key": analysis.context_key,
            "search_job_id": analysis.search_job_id,
            "model_provider": analysis.model_provider,
            "model_name": analysis.model_name,
            "facts_json": _bounded_json(analysis.facts_json, 4000),
            "risks_json": _bounded_json(analysis.risks_json, 4000),
            "questions_json": _bounded_json(analysis.questions_json, 4000),
            "report_md": _bounded(analysis.report_md, 4000),
        },
        "search_context": None if search_job is None else {
            "id": search_job.id,
            "name": _bounded(search_job.name, 200),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _bounded(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _bounded_json(value: object, limit: int) -> object:
    if not isinstance(value, dict):
        return {}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= limit:
        return value
    return {"truncated_json": encoded[:limit]}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ReviewCopilotPayloadError(f"Invalid integer identifier: {value}") from exc
