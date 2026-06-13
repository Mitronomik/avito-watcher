import json

import pytest

from app.models.knowledge_note import KnowledgeNote
from app.models.listing import Listing
from app.services.data_quality_agent import (
    DataQualityAgentError,
    DataQualityAgentService,
)


class FakeClient:
    provider = "openai_compatible"
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def complete(self, prompt):
        self.calls.append(prompt)
        return json.dumps(
            {
                "schema_version": "data-quality-assessment-schema-v1",
                "overall_status": "ok",
                "review_priority": "low",
                "should_human_review": False,
                "issues": [],
                "contradictions": [],
                "missing_evidence": [],
                "uncertain_fields": [],
                "rag_references": [
                    {"note_id": 1, "note_type": "rulebook", "usage": "context"}
                ],
                "human_review_recommendations": [],
                "recommended_rule_patch": None,
                "confidence": 0.8,
            }
        )


class FailingRag:
    def search_notes(self, **kwargs):
        raise RuntimeError("rag down")


def _listing(db):
    row = Listing(
        external_id="ext-rag",
        url="https://avito.test/1",
        title="street retail",
        price=1000,
        address="center",
        area_m2=42,
    )
    db.add(row)
    db.commit()
    return row


def test_rag_disabled_by_default_no_retrieval(db_session, monkeypatch):
    listing = _listing(db_session)
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_provider", "openai_compatible"
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_model", "fake-model"
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_rag_enabled",
        False,
    )

    class MustNotCall:
        def search_notes(self, **kwargs):
            raise AssertionError("RAG disabled must not retrieve")

    client = FakeClient()
    result = DataQualityAgentService(
        db_session, client=client, knowledge_retrieval_service=MustNotCall()
    ).assess(listing_external_id=listing.external_id)
    assert result.enrichment.warnings_json == ["extraction_missing", "rag_disabled"]
    assert "rag_notes" in client.calls[0]


def test_rag_enabled_retrieves_bounds_and_does_not_mutate_notes(
    db_session, monkeypatch
):
    listing = _listing(db_session)
    note = KnowledgeNote(
        note_type="rulebook",
        profile="commercial_rent",
        title="street",
        body_md="street retail quality rule " * 100,
        priority=10,
    )
    db_session.add(note)
    db_session.commit()
    updated = note.updated_at
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_provider", "openai_compatible"
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_model", "fake-model"
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_rag_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_rag_limit", 1
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_rag_max_chars",
        120,
    )
    client = FakeClient()
    result = DataQualityAgentService(db_session, client=client).assess(
        listing_external_id=listing.external_id
    )
    assert result.enrichment.id
    assert db_session.get(KnowledgeNote, note.id).body_md.startswith("street retail")
    assert db_session.get(KnowledgeNote, note.id).updated_at == updated
    assert len(client.calls[0]) < 16000
    assert '"id": 1' in client.calls[0]


def test_rag_failure_fails_closed_before_provider(db_session, monkeypatch):
    listing = _listing(db_session)
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_provider", "openai_compatible"
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_rag_enabled",
        True,
    )
    client = FakeClient()
    with pytest.raises(DataQualityAgentError) as exc:
        DataQualityAgentService(
            db_session, client=client, knowledge_retrieval_service=FailingRag()
        ).assess(listing_external_id=listing.external_id)
    assert exc.value.error_type == "data_quality_agent_rag_retrieval_failed"
    assert not client.calls
