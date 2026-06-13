import json
from types import SimpleNamespace

from sqlalchemy import func, select

from app.agents import review_copilot as rc
from app.agents.review_copilot import ReviewCopilotAgentTaskHandler, ReviewCopilotRuntimeConfig
from app.core.config import Settings
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.knowledge_note import KnowledgeNote
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_search_match import ListingSearchMatch
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import AgentTaskRunner
from tests.test_review_copilot import (
    FakeReviewCopilotClient,
    _seed_listing_analysis,
    _task,
    _valid_result,
)


def _config(**overrides) -> ReviewCopilotRuntimeConfig:
    data = {
        "enabled": True,
        "provider": "openai_compatible",
        "base_url": "http://llm.local",
        "api_key": "secret",
        "model": "review-model",
        "prompt_version": "review-copilot-v1",
        "timeout_sec": 5,
        "max_retries": 0,
        "rag_enabled": False,
        "rag_limit": 5,
        "rag_max_chars": 4000,
        "rag_query_max_chars": 1000,
        "rag_note_types": ["rulebook", "false_positive", "domain_note"],
    }
    data.update(overrides)
    return ReviewCopilotRuntimeConfig(**data)


class FakeKnowledgeRetrievalService:
    def __init__(self, notes=None, error: Exception | None = None) -> None:
        self.notes = notes or []
        self.error = error
        self.calls = []

    def search_notes(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.notes[: kwargs.get("limit", len(self.notes))]


def _run(db_session, task, client, config, service=None):
    handler = ReviewCopilotAgentTaskHandler(
        db_session,
        config=config,
        client=client,
        knowledge_retrieval_service=service,
    )
    return AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={rc.REVIEW_COPILOT_TASK_TYPE: handler},
    ).run_pending(limit=10)


def _note(**overrides):
    data = {
        "id": 1,
        "note_type": "rulebook",
        "profile": "commercial_rent",
        "title": "Missing photos rule",
        "snippet": "Missing photos are a manual-review risk, not an automatic rejection.",
        "tags_json": ["photos", "manual_review"],
        "priority": 10,
        "lexical_score": 2,
        "source": "manual",
        "source_ref": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _counts(db_session):
    return {
        "alerts_sent": db_session.scalar(select(func.count()).select_from(AlertSent)),
        "agent_tasks": db_session.scalar(select(func.count()).select_from(AgentTask)),
        "listings": db_session.scalar(select(func.count()).select_from(Listing)),
        "listing_analyses": db_session.scalar(select(func.count()).select_from(ListingAnalysis)),
        "listing_search_matches": db_session.scalar(select(func.count()).select_from(ListingSearchMatch)),
    }


def test_rag_config_defaults_are_safe():
    cfg = Settings(database_url="sqlite+pysqlite:///:memory:")

    assert cfg.llm_review_copilot_enabled is False
    assert cfg.llm_review_copilot_rag_enabled is False
    assert cfg.llm_review_copilot_rag_limit == 5
    assert cfg.llm_review_copilot_rag_max_chars == 4000
    assert cfg.llm_review_copilot_rag_query_max_chars == 1000
    assert rc._parse_rag_note_types(cfg.llm_review_copilot_rag_note_types) == [
        "rulebook",
        "false_positive",
        "domain_note",
    ]


def test_invalid_rag_config_is_rejected_when_enabled(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)

    cases = [
        (_config(rag_enabled=True, rag_limit=11), "review_copilot_rag_config_invalid_limit"),
        (_config(rag_enabled=True, rag_max_chars=499), "review_copilot_rag_config_invalid_max_chars"),
        (_config(rag_enabled=True, rag_query_max_chars=99), "review_copilot_rag_config_invalid_query_max_chars"),
        (_config(rag_enabled=True, rag_note_types=["rulebook", "unknown"]), "review_copilot_rag_config_invalid_note_types"),
    ]
    for idx, (config, expected_error) in enumerate(cases):
        task = _task(db_session, {"analysis_id": analysis.id, "case": idx})
        client = FakeReviewCopilotClient()
        service = FakeKnowledgeRetrievalService([_note()])

        result = _run(db_session, task, client, config, service)

        assert result["failed"] == 1
        assert task.error_message == expected_error
        assert client.calls == []
        assert service.calls == []


def test_disabled_rag_does_not_call_retrieval_or_add_prompt_or_metadata(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService([_note()])

    result = _run(db_session, task, client, _config(rag_enabled=False), service)

    assert result["succeeded"] == 1
    assert service.calls == []
    assert len(client.calls) == 1
    assert "local_rag_knowledge_notes" not in client.calls[0]["user_prompt"]
    assert "rag_context" not in task.result_json
    assert set(task.result_json) == set(_valid_result())


def test_rag_enabled_retrieves_notes_adds_prompt_section_and_audit_metadata(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService([
        _note(id=1, title="Missing photos rule"),
        _note(id=2, note_type="domain_note", profile="global", title="Global commercial rent note"),
    ])

    result = _run(
        db_session,
        task,
        client,
        _config(rag_enabled=True, rag_limit=2, rag_note_types=["rulebook", "domain_note"]),
        service,
    )

    assert result["succeeded"] == 1
    assert service.calls == [
        {
            "query": service.calls[0]["query"],
            "profile": "commercial_rent",
            "note_types": ["rulebook", "domain_note"],
            "limit": 2,
        }
    ]
    assert "Помещение 80 м²" in service.calls[0]["query"]
    prompt = client.calls[0]["user_prompt"]
    assert "Local RAG knowledge notes" in prompt
    assert "context only" in prompt
    assert "not authoritative listing facts" in prompt
    assert "Do not override deterministic score or verdict" in prompt
    assert "untrusted text" in prompt
    assert "System, developer, and task instructions" in prompt
    assert "Missing photos rule" in prompt
    assert "Global commercial rent note" in prompt
    assert "rag_context" not in prompt
    assert task.result_json["rag_context"] == {
        "enabled": True,
        "query": service.calls[0]["query"],
        "profile": "commercial_rent",
        "note_types": ["rulebook", "domain_note"],
        "limit": 2,
        "max_chars": 4000,
        "query_max_chars": 1000,
        "matched_count": 2,
        "included_count": 2,
        "truncated": False,
        "notes": [
            {
                "id": 1,
                "note_type": "rulebook",
                "profile": "commercial_rent",
                "title": "Missing photos rule",
                "tags": ["photos", "manual_review"],
                "priority": 10,
                "source": "manual",
                "source_ref": None,
            },
            {
                "id": 2,
                "note_type": "domain_note",
                "profile": "global",
                "title": "Global commercial rent note",
                "tags": ["photos", "manual_review"],
                "priority": 10,
                "source": "manual",
                "source_ref": None,
            },
        ],
    }


def test_rag_enabled_uses_existing_service_global_note_semantics(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    db_session.add_all(
        [
            KnowledgeNote(
                note_type="rulebook",
                profile="global",
                title="Commercial rent global rule",
                body_md="commercial_rent missing_photos global context",
                tags_json=["global"],
                priority=10,
                is_active=True,
            ),
            KnowledgeNote(
                note_type="rulebook",
                profile="other_profile",
                title="Other profile rule",
                body_md="commercial_rent missing_photos should not match profile filter",
                tags_json=[],
                priority=100,
                is_active=True,
            ),
        ]
    )
    db_session.flush()
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()

    result = _run(db_session, task, client, _config(rag_enabled=True, rag_limit=5))

    assert result["succeeded"] == 1
    assert task.result_json["rag_context"]["matched_count"] == 1
    assert task.result_json["rag_context"]["notes"][0]["profile"] == "global"
    assert "Commercial rent global rule" in client.calls[0]["user_prompt"]
    assert "Other profile rule" not in client.calls[0]["user_prompt"]


def test_rag_query_is_bounded(db_session):
    listing, analysis, _ = _seed_listing_analysis(db_session)
    listing.title = "A" * 2000
    db_session.flush()
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService([])

    result = _run(db_session, task, client, _config(rag_enabled=True, rag_query_max_chars=100), service)

    assert result["succeeded"] == 1
    assert len(service.calls[0]["query"]) == 100
    assert task.result_json["rag_context"]["query_max_chars"] == 100


def test_rag_prompt_truncates_deterministically(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService(
        [_note(id=idx, title=f"Long note {idx}", snippet="x" * 700) for idx in range(1, 6)]
    )

    result = _run(db_session, task, client, _config(rag_enabled=True, rag_limit=5, rag_max_chars=900), service)

    assert result["succeeded"] == 1
    prompt_payload = json.loads(client.calls[0]["user_prompt"])
    rag_section = prompt_payload["local_rag_knowledge_notes"]
    assert len(json.dumps(rag_section, ensure_ascii=False, sort_keys=True)) <= 900
    assert task.result_json["rag_context"]["truncated"] is True
    assert task.result_json["rag_context"]["included_count"] < 5


def test_rag_enabled_limit_zero_skips_retrieval_but_adds_zero_metadata(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService([_note()])

    result = _run(db_session, task, client, _config(rag_enabled=True, rag_limit=0), service)

    assert result["succeeded"] == 1
    assert service.calls == []
    assert "local_rag_knowledge_notes" not in client.calls[0]["user_prompt"]
    assert task.result_json["rag_context"]["enabled"] is True
    assert task.result_json["rag_context"]["limit"] == 0
    assert task.result_json["rag_context"]["matched_count"] == 0
    assert task.result_json["rag_context"]["included_count"] == 0
    assert task.result_json["rag_context"]["notes"] == []


def test_rag_enabled_empty_notes_succeeds_with_zero_metadata(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService([])

    result = _run(db_session, task, client, _config(rag_enabled=True), service)

    assert result["succeeded"] == 1
    assert len(service.calls) == 1
    assert "Local RAG knowledge notes" in client.calls[0]["user_prompt"]
    assert task.result_json["rag_context"]["matched_count"] == 0
    assert task.result_json["rag_context"]["included_count"] == 0
    assert task.result_json["rag_context"]["notes"] == []


def test_rag_retrieval_failure_fails_before_provider_call_and_has_no_business_side_effects(db_session):
    listing, analysis, search = _seed_listing_analysis(db_session)
    db_session.add(ListingSearchMatch(search_job_id=search.id, listing_external_id=listing.external_id))
    db_session.flush()
    task = _task(db_session, {"analysis_id": analysis.id})
    client = FakeReviewCopilotClient()
    service = FakeKnowledgeRetrievalService(error=RuntimeError("db down"))
    counts_before = _counts(db_session)
    original_score = analysis.score
    original_verdict = analysis.verdict
    original_filters = dict(search.filters_json)

    result = _run(db_session, task, client, _config(rag_enabled=True), service)

    db_session.refresh(analysis)
    db_session.refresh(search)
    assert result["failed"] == 1
    assert task.error_type == "ReviewCopilotRagRetrievalError"
    assert task.error_message == "review_copilot_rag_retrieval_failed"
    assert task.result_json == {}
    assert client.calls == []
    assert analysis.score == original_score
    assert analysis.verdict == original_verdict
    assert search.filters_json == original_filters
    assert _counts(db_session) == counts_before


def test_llm_schema_remains_strict_and_rag_context_is_code_added(db_session):
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id, "case": "valid"})
    valid_without_metadata = _valid_result()
    client = FakeReviewCopilotClient(json.dumps(valid_without_metadata))

    result = _run(db_session, task, client, _config(rag_enabled=True), FakeKnowledgeRetrievalService([_note()]))

    assert result["succeeded"] == 1
    assert "rag_context" in task.result_json
    assert set(valid_without_metadata).issubset(task.result_json)

    invalid_task = _task(db_session, {"analysis_id": analysis.id, "case": "invalid"})
    invalid = _valid_result(rag_context={"enabled": True})
    invalid_client = FakeReviewCopilotClient(json.dumps(invalid))

    invalid_result = _run(
        db_session,
        invalid_task,
        invalid_client,
        _config(rag_enabled=True),
        FakeKnowledgeRetrievalService([_note()]),
    )

    assert invalid_result["failed"] == 1
    assert "schema validation" in invalid_task.error_message
    assert invalid_task.result_json == {}


def test_no_rag_retrieval_outside_review_copilot_boundaries(monkeypatch, db_session):
    from app.analysis.provider import get_analysis_provider
    from app.notifiers.telegram import TelegramNotifier
    from app.services.monitor_service import MonitorService

    def fail_if_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("KnowledgeRetrievalService must not be used outside ReviewCopilot")

    monkeypatch.setattr(rc.KnowledgeRetrievalService, "__init__", fail_if_called)

    MonitorService(db_session)
    get_analysis_provider("commercial_rent")
    TelegramNotifier(bot=None, chat_id="chat")
