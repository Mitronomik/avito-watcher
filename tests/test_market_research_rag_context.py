from datetime import UTC, datetime, timedelta

from app.models.knowledge_note import KnowledgeNote
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun
from app.services.market_evidence import MarketResearchRagContextBuilder


def test_context_builder_returns_bounded_sql_context_without_knowledge_note_writes(
    db_session,
):
    run = MarketResearchRun(
        agent_task_id=1,
        status="success",
        schema_version="research-agent-result-v1",
        checked_at=datetime.now(UTC).replace(tzinfo=None),
        research_profile="default",
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        MarketEvidenceItem(
            run_id=run.id,
            evidence_type="comparable_candidate",
            research_profile="default",
            listing_external_id="ext",
            asset_type="commercial",
            deal_type="rent",
            location_text="SPb",
            location_key="spb",
            claim="comp",
            area_m2=50,
            rent_rub_per_month=120000,
            rent_per_m2_rub=2400,
            source_url="https://example.com",
            source_url_normalized="https://example.com/",
            source_indexes_json=[0],
            evidence_json={},
            confidence=0.8,
            is_reusable=True,
            checked_at=datetime.now(UTC).replace(tzinfo=None),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
            content_hash="h",
        )
    )
    db_session.commit()
    before = db_session.query(KnowledgeNote).count()
    ctx = MarketResearchRagContextBuilder(db_session).build_context(
        listing_external_id="ext", limit=5
    )
    assert ctx["context_type"] == "market_research_rag_v0"
    assert ctx["retrieval_backend"] == "sql"
    assert len(ctx["items"]) == 1
    item = ctx["items"][0]
    assert item["evidence_item_id"]
    assert item["source_url"] == "https://example.com"
    assert item["confidence"] == 0.8
    assert item["checked_at"]
    assert item["expires_at"]
    assert db_session.query(KnowledgeNote).count() == before
