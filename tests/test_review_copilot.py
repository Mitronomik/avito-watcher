import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.agents.review_copilot import (
    REVIEW_COPILOT_TASK_TYPE,
    ReviewCopilotAgentTaskHandler,
    ReviewCopilotRuntimeConfig,
)
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_search_match import ListingSearchMatch
from app.models.search_job import SearchJob
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import AgentTaskRunner, build_default_agent_task_handlers


class FakeReviewCopilotClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content or json.dumps(_valid_result())
        self.error = error
        self.calls = []

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.error is not None:
            raise self.error
        return self.content


def _config(enabled: bool = True) -> ReviewCopilotRuntimeConfig:
    return ReviewCopilotRuntimeConfig(
        enabled=enabled,
        provider="openai_compatible",
        base_url="http://llm.local",
        api_key="secret",
        model="review-model",
        prompt_version="review-copilot-v1",
        timeout_sec=5,
        max_retries=0,
    )


def _valid_result(**overrides) -> dict:
    result = {
        "summary": "Объект выглядит подходящим для ручной проверки по сохраненному анализу.",
        "recommended_next_action": "ready_for_manual_review",
        "questions": ["Уточнить условия договора."],
        "risk_explanation": ["Не хватает данных о состоянии помещения."],
        "positive_factors": ["Подходит по бюджету."],
        "missing_data": ["Фотографии"],
        "confidence": 0.72,
        "model": "review-model",
        "prompt_version": "review-copilot-v1",
    }
    result.update(overrides)
    return result


def _seed_listing_analysis(db_session, *, external_id: str = "8125374391", search: SearchJob | None = None):
    if search is None:
        search = SearchJob(
            name="Коммерция",
            source_url="https://www.avito.ru/search",
            filters_json={"max_price": 100000},
        )
        db_session.add(search)
        db_session.flush()
    listing = Listing(
        external_id=external_id,
        url=f"https://www.avito.ru/{external_id}",
        title="Помещение 80 м²",
        price=90000,
        address="Санкт-Петербург",
        area_m2=80,
        published_label="сегодня",
        published_at=datetime(2026, 6, 12, tzinfo=UTC).replace(tzinfo=None),
    )
    db_session.add(listing)
    analysis = ListingAnalysis(
        listing_external_id=external_id,
        search_job_id=search.id,
        context_key=f"search:{search.id}",
        profile="commercial_rent",
        status="success",
        analysis_version="det-v1",
        input_hash=f"hash-{external_id}",
        score=82,
        verdict="strong",
        facts_json={"area_ok": True, "budget_ok": True},
        risks_json={"flags": ["missing_photos"]},
        questions_json={"items": ["Есть ли отдельный вход?"]},
        report_md="Детерминированный отчет.",
    )
    db_session.add(analysis)
    db_session.flush()
    return listing, analysis, search


def _task(db_session, payload: dict, *, external_id: str | None = "8125374391") -> AgentTask:
    task = AgentTask(
        task_type=REVIEW_COPILOT_TASK_TYPE,
        status="pending",
        priority=50,
        listing_external_id=external_id,
        listing_analysis_id=payload.get("analysis_id") or payload.get("listing_analysis_id"),
        dedupe_key=f"review:{json.dumps(payload, sort_keys=True)}:{external_id}",
        payload_json=payload,
        result_json={},
    )
    db_session.add(task)
    db_session.flush()
    return task


def _run_task(
    db_session,
    task: AgentTask,
    client: FakeReviewCopilotClient,
    *,
    enabled: bool = True,
    config: ReviewCopilotRuntimeConfig | None = None,
) -> dict:
    repo = AgentTaskRepository(db_session)
    handler = ReviewCopilotAgentTaskHandler(
        db_session,
        config=config or _config(enabled),
        client=client,
    )
    return AgentTaskRunner(repo, handlers={REVIEW_COPILOT_TASK_TYPE: handler}).run_pending(limit=10)


def test_review_copilot_handler_is_registered(db_session):
    handlers = build_default_agent_task_handlers(db_session)

    assert REVIEW_COPILOT_TASK_TYPE in handlers


def test_unknown_handler_behavior_remains_skipped(db_session):
    repo = AgentTaskRepository(db_session)
    task = repo.create_or_get_task(
        task_type="unknown_task_type",
        dedupe_key="unknown:1",
        payload_json={},
    )

    result = AgentTaskRunner(repo).run_pending(limit=10)

    assert result["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json["reason"] == "unknown_agent_task_type"
    assert task.result_json["error_type"] == "unknown_agent_task_type"
    assert task.result_json["task_type"] == "unknown_task_type"


def test_review_copilot_disabled_mode_skips_without_provider_call(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id, "listing_external_id": analysis.listing_external_id})
    client = FakeReviewCopilotClient()

    result = _run_task(db_session, task, client, enabled=False)

    assert result["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json["reason"] == "review_copilot_disabled"
    assert "summary" not in task.result_json
    assert client.calls == []


def test_review_copilot_dry_run_does_not_mutate_or_call_provider(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id, "listing_external_id": analysis.listing_external_id})
    client = FakeReviewCopilotClient()
    repo = AgentTaskRepository(db_session)
    handler = ReviewCopilotAgentTaskHandler(db_session, config=_config(True), client=client)

    result = AgentTaskRunner(repo, handlers={REVIEW_COPILOT_TASK_TYPE: handler}).run_pending(limit=10, dry_run=True)

    assert result["pending"] == 1
    assert task.status == "pending"
    assert task.result_json == {}
    assert client.calls == []


def test_review_copilot_success_writes_result_json_only_and_has_no_side_effects(db_session):
    listing, analysis, search = _seed_listing_analysis(db_session)
    db_session.add(ListingSearchMatch(search_job_id=search.id, listing_external_id=listing.external_id))
    db_session.flush()
    task = _task(db_session, {"analysis_id": analysis.id, "listing_external_id": listing.external_id})
    client = FakeReviewCopilotClient(json.dumps(_valid_result()))
    original_score = analysis.score
    original_verdict = analysis.verdict
    original_filters = dict(search.filters_json)
    counts_before = _side_effect_counts(db_session)

    result = _run_task(db_session, task, client)

    db_session.refresh(analysis)
    db_session.refresh(listing)
    db_session.refresh(search)
    assert result["succeeded"] == 1
    assert task.status == "success"
    assert task.result_json["summary"]
    assert task.result_json["recommended_next_action"] == "ready_for_manual_review"
    assert task.result_json["questions"]
    assert task.result_json["risk_explanation"]
    assert task.result_json["confidence"] == 0.72
    assert task.result_json["prompt_version"] == "review-copilot-v1"
    assert analysis.score == original_score
    assert analysis.verdict == original_verdict
    assert search.filters_json == original_filters
    assert _side_effect_counts(db_session) == counts_before
    assert len(client.calls) == 1
    assert "api_key" not in client.calls[0]["user_prompt"]
    assert "secret" not in client.calls[0]["user_prompt"]



def test_review_copilot_preflight_missing_api_key_fails_without_provider_call(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    config = ReviewCopilotRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="http://llm.local",
        api_key="",
        model="review-model",
        prompt_version="review-copilot-v1",
        timeout_sec=5,
        max_retries=0,
    )

    result = _run_task(db_session, task, client, config=config)

    assert result["failed"] == 1
    assert task.status == "failed"
    assert task.error_message == "review_copilot_config_missing_api_key"
    assert "secret" not in task.error_message
    assert task.result_json == {}
    assert client.calls == []


def test_review_copilot_preflight_missing_model_fails_without_provider_call(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    config = ReviewCopilotRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="http://llm.local",
        api_key="secret",
        model="",
        prompt_version="review-copilot-v1",
        timeout_sec=5,
        max_retries=0,
    )

    result = _run_task(db_session, task, client, config=config)

    assert result["failed"] == 1
    assert task.status == "failed"
    assert "LLM model is required" in task.error_message
    assert task.result_json == {}
    assert client.calls == []


def test_review_copilot_preflight_missing_base_url_fails_without_provider_call(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    config = ReviewCopilotRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        base_url="",
        api_key="secret",
        model="review-model",
        prompt_version="review-copilot-v1",
        timeout_sec=5,
        max_retries=0,
    )

    result = _run_task(db_session, task, client, config=config)

    assert result["failed"] == 1
    assert task.status == "failed"
    assert "LLM base URL is required" in task.error_message
    assert task.result_json == {}
    assert client.calls == []


def test_review_copilot_invalid_json_fails_without_result(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})

    result = _run_task(db_session, task, FakeReviewCopilotClient("not-json"))

    assert result["failed"] == 1
    assert task.status == "failed"
    assert "invalid JSON" in task.error_message
    assert task.result_json == {}


def test_review_copilot_invalid_schema_fails_without_result(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    invalid = _valid_result(recommended_next_action="auto_change_verdict", extra_field="nope")

    result = _run_task(db_session, task, FakeReviewCopilotClient(json.dumps(invalid)))

    assert result["failed"] == 1
    assert task.status == "failed"
    assert "schema validation" in task.error_message
    assert task.result_json == {}


def test_review_copilot_missing_payload_identifiers_fails(db_session):
    task = _task(db_session, {}, external_id=None)

    result = _run_task(db_session, task, FakeReviewCopilotClient())

    assert result["failed"] == 1
    assert "requires analysis_id" in task.error_message


def test_review_copilot_missing_listing_fails(db_session):
    analysis = ListingAnalysis(
        listing_external_id="missing-listing",
        status="success",
        profile="commercial_rent",
        analysis_version="det-v1",
        input_hash="missing-listing-hash",
    )
    db_session.add(analysis)
    db_session.flush()
    task = _task(db_session, {"analysis_id": analysis.id}, external_id="missing-listing")

    result = _run_task(db_session, task, FakeReviewCopilotClient())

    assert result["failed"] == 1
    assert "Listing not found" in task.error_message


def test_review_copilot_missing_analysis_fails(db_session):
    _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": 999999})

    result = _run_task(db_session, task, FakeReviewCopilotClient())

    assert result["failed"] == 1
    assert "Listing analysis not found" in task.error_message


def test_review_copilot_analysis_listing_mismatch_fails(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    db_session.add(Listing(external_id="other", url="https://example.test/other"))
    db_session.flush()
    task = _task(db_session, {"analysis_id": analysis.id, "listing_external_id": "other"}, external_id="other")

    result = _run_task(db_session, task, FakeReviewCopilotClient())

    assert result["failed"] == 1
    assert "does not belong" in task.error_message


def test_review_copilot_ambiguous_analysis_selection_fails(db_session):
    _seed_listing_analysis(db_session)
    db_session.add(
        ListingAnalysis(
            listing_external_id="8125374391",
            search_job_id=None,
            context_key="global",
            profile="other_profile",
            status="success",
            analysis_version="det-v1",
            input_hash="second-hash",
            score=50,
            verdict="review",
        )
    )
    db_session.flush()
    task = _task(db_session, {"listing_external_id": "8125374391"})

    result = _run_task(db_session, task, FakeReviewCopilotClient())

    assert result["failed"] == 1
    assert "Ambiguous listing analysis selection" in task.error_message


def test_review_copilot_provider_error_fails_without_result(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})

    result = _run_task(db_session, task, FakeReviewCopilotClient(error=RuntimeError("timeout")))

    assert result["failed"] == 1
    assert task.status == "failed"
    assert "timeout" in task.error_message
    assert task.result_json == {}


def test_review_copilot_can_resolve_by_listing_with_explicit_context(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(
        db_session,
        {
            "listing_external_id": analysis.listing_external_id,
            "analysis_profile": analysis.profile,
            "context_key": analysis.context_key,
        },
    )

    result = _run_task(db_session, task, FakeReviewCopilotClient())

    assert result["succeeded"] == 1
    assert task.status == "success"


def _side_effect_counts(db_session) -> dict:
    return {
        "alerts_sent": db_session.scalar(select(func.count()).select_from(AlertSent)),
        "listing_search_matches": db_session.scalar(select(func.count()).select_from(ListingSearchMatch)),
        "agent_tasks": db_session.scalar(select(func.count()).select_from(AgentTask)),
    }
