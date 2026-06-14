from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect

from app.models.agent_task import AgentTask
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun
from app.services.market_evidence import (
    MarketEvidenceError,
    MarketEvidenceService,
    normalize_source_url,
)


def valid_result(confidence=0.8):
    return {
        "schema_version": "research-agent-result-v1",
        "research_profile": "default",
        "listing_external_id": "ext-1",
        "summary": "source-backed advisory summary",
        "query_plan": [{"query": "rent comps", "purpose": "comps"}],
        "findings": [
            {
                "topic": "price_context",
                "claim": "Rents are visible in sources",
                "evidence": "source snippet",
                "source_indexes": [0],
                "confidence": confidence,
            }
        ],
        "comparable_candidates": [
            {
                "asset_type": "commercial",
                "deal_type": "rent",
                "area_m2": 50,
                "price_rub": None,
                "rent_rub_per_month": 120000,
                "price_per_m2_rub": None,
                "rent_per_m2_rub": 2400,
                "location_text": "SPb Center",
                "source_indexes": [0],
                "similarity_notes": "same district",
                "confidence": confidence,
            }
        ],
        "risks": [],
        "opportunities": [
            {
                "description": "demand context",
                "source_indexes": [1],
                "confidence": confidence,
            }
        ],
        "market_assumptions_to_verify": [
            {
                "assumption": "rent is sustainable",
                "why_it_matters": "affects future PR16 scoring",
                "source_indexes": [1],
                "confidence": confidence,
            }
        ],
        "human_review_questions": [],
        "sources": [
            {
                "title": "Comp",
                "url": "HTTPS://Example.COM/path/?utm_source=x&b=1#frag",
                "publisher": "Pub",
                "published_at": "2026-06-01",
                "accessed_at": "2026-06-14",
            },
            {
                "title": "Context",
                "url": "https://example.com/context?gclid=1",
                "publisher": "Pub",
                "published_at": None,
                "accessed_at": "2026-06-14",
            },
        ],
        "limitations": ["advisory only"],
        "confidence": confidence,
        "review_recommendation": {
            "should_review": False,
            "reason": "manual_shadow_review",
            "confidence": confidence,
        },
    }


def make_task(db, result=None, status="success", task_type="market_research"):
    task = AgentTask(
        task_type=task_type,
        status=status,
        dedupe_key=f"task-{status}-{task_type}-{id(result)}",
        listing_external_id="ext-1",
        result_json=result or valid_result(),
        payload_json={"provider": "manual", "model": "fake"},
    )
    db.add(task)
    db.commit()
    return task


def test_market_evidence_tables_and_columns_exist(db_session):
    inspector = inspect(db_session.bind)
    assert "market_research_runs" in inspector.get_table_names()
    assert "market_evidence_items" in inspector.get_table_names()
    run_cols = {c["name"] for c in inspector.get_columns("market_research_runs")}
    item_cols = {c["name"] for c in inspector.get_columns("market_evidence_items")}
    assert {"agent_task_id", "sources_json", "checked_at", "expires_at"}.issubset(
        run_cols
    )
    assert {
        "run_id",
        "evidence_type",
        "source_url_normalized",
        "is_reusable",
        "reuse_block_reason",
        "content_hash",
    }.issubset(item_cols)


def test_ingest_successful_market_research_task_idempotently(db_session):
    task = make_task(db_session)
    service = MarketEvidenceService(
        db_session, now=datetime(2026, 6, 14, tzinfo=UTC).replace(tzinfo=None)
    )
    result = service.ingest_agent_task(task.id)
    db_session.commit()
    assert result.created_run is True
    assert result.created_items == 4
    assert db_session.query(MarketResearchRun).count() == 1
    assert db_session.query(MarketEvidenceItem).count() == 4
    assert (
        db_session.query(MarketEvidenceItem).filter_by(evidence_type="source").count()
        == 0
    )
    comp = (
        db_session.query(MarketEvidenceItem)
        .filter_by(evidence_type="comparable_candidate")
        .one()
    )
    assert comp.is_reusable is True
    assert comp.source_url_normalized == "https://example.com/path?b=1"
    assert comp.checked_at and comp.expires_at
    assert (
        db_session.query(MarketEvidenceItem)
        .filter_by(evidence_type="assumption_to_verify")
        .count()
        == 1
    )

    again = service.ingest_agent_task(task.id)
    db_session.commit()
    assert again.created_run is False
    assert again.created_items == 0
    assert again.reused_items == 4
    assert db_session.query(MarketEvidenceItem).count() == 4


def test_validation_rejects_wrong_failed_and_invalid_tasks(db_session):
    with pytest.raises(MarketEvidenceError) as wrong:
        MarketEvidenceService(db_session).ingest_agent_task(
            make_task(db_session, task_type="review_copilot").id
        )
    assert wrong.value.error_type == "market_evidence_wrong_task_type"
    with pytest.raises(MarketEvidenceError) as failed:
        MarketEvidenceService(db_session).ingest_agent_task(
            make_task(db_session, status="failed").id
        )
    assert failed.value.error_type == "market_evidence_task_not_success"
    with pytest.raises(MarketEvidenceError) as invalid:
        MarketEvidenceService(db_session).ingest_agent_task(
            make_task(db_session, result={"bad": True}).id
        )
    assert invalid.value.error_type == "market_evidence_invalid_result"


def test_low_confidence_stored_non_reusable_and_source_url_normalization(db_session):
    task = make_task(db_session, valid_result(confidence=0.4))
    MarketEvidenceService(db_session).ingest_agent_task(task.id)
    item = (
        db_session.query(MarketEvidenceItem)
        .filter_by(evidence_type="comparable_candidate")
        .one()
    )
    assert item.is_reusable is False
    assert item.reuse_block_reason == "low_confidence"
    assert normalize_source_url(
        "https://EXAMPLE.com/path/?utm_campaign=x&b=1#frag"
    ) == normalize_source_url("https://example.com/path?b=1")


def test_cascade_delete_run_deletes_items(db_session):
    task = make_task(db_session)
    MarketEvidenceService(db_session).ingest_agent_task(task.id)
    run = db_session.query(MarketResearchRun).one()
    db_session.delete(run)
    db_session.commit()
    assert db_session.query(MarketEvidenceItem).count() == 0
