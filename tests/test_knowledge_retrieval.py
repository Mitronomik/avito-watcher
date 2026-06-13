import inspect

import pytest

from app.agents.review_copilot import REVIEW_COPILOT_TASK_TYPE
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_search_match import ListingSearchMatch
from app.models.search_job import SearchJob
from app.services.knowledge_retrieval import KnowledgeRetrievalService
import app.agents.review_copilot as review_copilot
import app.services.monitor_service as monitor_service
import app.analysis.service as analysis_service


def _seed_notes(service: KnowledgeRetrievalService):
    global_note = service.create_note(
        note_type="rulebook",
        title="Fresh listing rulebook",
        body_md="Fresh commercial listings with published_at are preferred.",
        tags_json=["fresh"],
        priority=1,
    )
    commercial = service.create_note(
        note_type="false_positive",
        profile="commercial_rent",
        title="Parking and гараж false positives",
        body_md="Parking, storage rooms, гараж, машиноместо and кладовка often match commercial rent searches.",
        tags_json=["parking", "гараж"],
        priority=10,
    )
    inactive = service.create_note(
        note_type="domain_note",
        profile="commercial_rent",
        title="Inactive parking note",
        body_md="parking should not be returned when inactive",
        is_active=False,
        priority=100,
    )
    flat = service.create_note(
        note_type="domain_note",
        profile="flat_rent",
        title="Flat rent note",
        body_md="parking can be a bonus for flats",
        priority=9,
    )
    return global_note, commercial, inactive, flat


def test_search_matches_title_body_and_tags_case_insensitively(db_session):
    service = KnowledgeRetrievalService(db_session)
    _, commercial, _, _ = _seed_notes(service)

    title_results = service.search_notes("PARKING", profile="commercial_rent")
    body_results = service.search_notes("машиноместо", profile="commercial_rent")
    tag_results = service.search_notes("гараж", profile="commercial_rent")

    assert title_results[0].id == commercial.id
    assert body_results[0].id == commercial.id
    assert tag_results[0].id == commercial.id
    assert title_results[0].snippet
    assert len(title_results[0].snippet) <= 500


def test_profile_search_includes_global_but_not_other_profiles(db_session):
    service = KnowledgeRetrievalService(db_session)
    global_note, commercial, _, flat = _seed_notes(service)

    results = service.search_notes("commercial parking", profile="commercial_rent", limit=10)
    ids = [result.id for result in results]

    assert commercial.id in ids
    assert global_note.id in ids
    assert flat.id not in ids


def test_search_filters_by_note_type_and_tag(db_session):
    service = KnowledgeRetrievalService(db_session)
    _, commercial, _, _ = _seed_notes(service)

    assert service.search_notes("parking", note_types=["false_positive"])[0].id == commercial.id
    assert service.search_notes("parking", tags=["гараж"])[0].id == commercial.id
    assert service.search_notes("parking", note_types=["rulebook"]) == []
    assert service.search_notes("parking", tags=["missing-tag"]) == []


def test_search_excludes_inactive_notes_and_list_active_only_by_default(db_session):
    service = KnowledgeRetrievalService(db_session)
    _, _, inactive, _ = _seed_notes(service)

    search_ids = [result.id for result in service.search_notes("inactive parking", profile="commercial_rent", limit=10)]
    list_ids = [note.id for note in service.list_notes(active_only=False, limit=10)]
    active_list_ids = [note.id for note in service.list_notes(limit=10)]

    assert inactive.id not in search_ids
    assert inactive.id in list_ids
    assert inactive.id not in active_list_ids


def test_search_limit_and_ordering_are_deterministic(db_session):
    service = KnowledgeRetrievalService(db_session)
    low = service.create_note(note_type="domain_note", title="Parking low", body_md="parking only", priority=1)
    high = service.create_note(note_type="domain_note", title="Parking high", body_md="parking only", priority=20)
    richer = service.create_note(note_type="domain_note", title="Parking richer", body_md="parking гараж", priority=20)

    results = service.search_notes("parking гараж", limit=2)

    assert [result.id for result in results] == [richer.id, high.id]
    assert low.id not in [result.id for result in results]


def test_search_blank_query_and_invalid_limit_are_rejected(db_session):
    service = KnowledgeRetrievalService(db_session)

    with pytest.raises(ValueError, match="non-empty"):
        service.search_notes("   ")
    with pytest.raises(ValueError, match="positive"):
        service.search_notes("parking", limit=0)


def test_retrieval_has_no_side_effects_on_core_tables(db_session):
    service = KnowledgeRetrievalService(db_session)
    search = SearchJob(name="Search", source_url="https://www.avito.ru/search", filters_json={"max_price": 1})
    analysis = ListingAnalysis(
        listing_external_id="1",
        profile="commercial_rent",
        status="success",
        analysis_version="det-v1",
        input_hash="hash",
        score=50,
        verdict="review",
    )
    match = ListingSearchMatch(search_job_id=1, listing_external_id="1")
    db_session.add_all([search, analysis, match])
    db_session.flush()
    original_filters = dict(search.filters_json)
    service.create_note(note_type="rulebook", title="Parking", body_md="parking", priority=1)

    counts_before = {
        "alerts": db_session.query(AlertSent).count(),
        "agent_tasks": db_session.query(AgentTask).count(),
        "matches": db_session.query(ListingSearchMatch).count(),
    }
    assert service.search_notes("parking")
    db_session.refresh(analysis)
    db_session.refresh(search)

    assert analysis.score == 50
    assert analysis.verdict == "review"
    assert search.filters_json == original_filters
    assert db_session.query(AlertSent).count() == counts_before["alerts"]
    assert db_session.query(AgentTask).count() == counts_before["agent_tasks"]
    assert db_session.query(ListingSearchMatch).count() == counts_before["matches"]


def test_pr8_boundaries_do_not_integrate_knowledge_retrieval():
    forbidden = "KnowledgeRetrievalService"

    assert forbidden not in inspect.getsource(review_copilot)
    assert forbidden not in inspect.getsource(monitor_service)
    assert forbidden not in inspect.getsource(analysis_service)


def test_review_copilot_boundary_prompt_has_no_rag_context_marker(db_session):
    from tests.test_review_copilot import FakeReviewCopilotClient, _run_task, _seed_listing_analysis, _task

    service = KnowledgeRetrievalService(db_session)
    service.create_note(
        note_type="rulebook",
        title="SECRET RAG SHOULD NOT APPEAR",
        body_md="Parking note that PR8 must not inject into ReviewCopilot.",
    )
    _, analysis, _ = _seed_listing_analysis(db_session)
    task = _task(db_session, {"analysis_id": analysis.id, "listing_external_id": analysis.listing_external_id})
    client = FakeReviewCopilotClient()

    result = _run_task(db_session, task, client)

    assert result["succeeded"] == 1
    assert len(client.calls) == 1
    prompt_text = client.calls[0]["system_prompt"] + "\n" + client.calls[0]["user_prompt"]
    assert "SECRET RAG SHOULD NOT APPEAR" not in prompt_text
    assert "Parking note that PR8 must not inject" not in prompt_text
    assert "rag" not in prompt_text.lower()
    assert task.task_type == REVIEW_COPILOT_TASK_TYPE
