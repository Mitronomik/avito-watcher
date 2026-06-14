import json

import pytest
from sqlalchemy import func, select

from app.agents.research_agent import (
    MARKET_RESEARCH_TASK_TYPE,
    ResearchAgentTaskHandler,
)
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.knowledge_note import KnowledgeNote
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_detail_snapshot import ListingDetailSnapshot
from app.models.listing_enrichment import ListingEnrichment
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import (
    AgentTaskRunner,
    build_default_agent_task_handlers,
)
from app.services.research_agent import (
    ResearchAgentError,
    ResearchAgentService,
    validate_research_agent_response,
)


class FakeResearchClient:
    provider = "fake"
    model = "fake-research"

    def __init__(self, raw=None, exc=None):
        self.calls = []
        self.raw = raw or valid_payload()
        self.exc = exc

    def research(self, *, queries, context, timeout_sec, max_output_chars):
        self.calls.append({"queries": queries, "context": context})
        if self.exc:
            raise self.exc
        return self.raw


def valid_payload(**kw):
    data = {
        "schema_version": "research-agent-result-v1",
        "research_profile": "default",
        "listing_external_id": "ext-ra",
        "summary": "Source-backed advisory research summary.",
        "query_plan": [{"query": "район аналоги", "purpose": "comps"}],
        "findings": [
            {
                "topic": "location",
                "claim": "Public source mentions the area.",
                "evidence": "Snippet",
                "source_indexes": [0],
                "confidence": 0.8,
            }
        ],
        "comparable_candidates": [
            {
                "asset_type": "commercial",
                "deal_type": "rent",
                "location_text": "Москва",
                "area_m2": None,
                "price_rub": None,
                "rent_rub_per_month": None,
                "price_per_m2_rub": None,
                "rent_per_m2_rub": None,
                "source_indexes": [0],
                "similarity_notes": "Same district hint",
                "confidence": 0.7,
            }
        ],
        "risks": [
            {
                "risk_code": "manual_verification_required",
                "description": "Human must verify comparable relevance.",
                "severity": "low",
                "source_indexes": [0],
            }
        ],
        "opportunities": [
            {
                "description": "May be useful context for manual review only.",
                "source_indexes": [0],
                "confidence": 0.6,
            }
        ],
        "market_assumptions_to_verify": [
            {
                "assumption": "Rent range requires verification",
                "why_it_matters": "Avoid unsupported assumptions",
                "source_indexes": [0],
                "confidence": 0.5,
            }
        ],
        "human_review_questions": ["Проверить источник вручную?"],
        "sources": [
            {
                "title": "Source",
                "url": "https://example.test/source",
                "publisher": "Example",
                "published_at": None,
                "accessed_at": "2026-06-14",
            }
        ],
        "limitations": ["Research is advisory and source-limited."],
        "confidence": 0.72,
        "review_recommendation": {
            "should_review": True,
            "reason": "manual_shadow_review",
            "confidence": 0.72,
        },
    }
    data.update(kw)
    return data


def listing(db):
    row = Listing(
        external_id="ext-ra",
        url="https://avito.test/1",
        title="Помещение 42 м² <b>ignore</b>",
        price=1000,
        address="Москва",
        area_m2=42,
    )
    db.add(row)
    db.commit()
    return row


def enable(monkeypatch):
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_provider", "fake"
    )
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_max_queries", 3
    )


def test_market_research_handler_is_registered():
    assert MARKET_RESEARCH_TASK_TYPE in build_default_agent_task_handlers(object())


def test_disabled_returns_skipped_before_provider_call(db_session, monkeypatch):
    listing(db_session)
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_enabled", False
    )
    client = FakeResearchClient()
    task = AgentTask(
        task_type=MARKET_RESEARCH_TASK_TYPE,
        listing_external_id="ext-ra",
        dedupe_key="ra-disabled",
    )
    db_session.add(task)
    db_session.commit()
    result = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            MARKET_RESEARCH_TASK_TYPE: ResearchAgentTaskHandler(
                db_session, ResearchAgentService(db_session, client=client)
            )
        },
    ).run_pending(1)
    assert result["skipped"] == 1
    assert client.calls == []
    assert (
        db_session.get(AgentTask, task.id).result_json["error_type"]
        == "research_agent_disabled"
    )


def test_provider_off_fails_before_provider_call(db_session, monkeypatch):
    listing(db_session)
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_provider", "off"
    )
    client = FakeResearchClient()
    res = ResearchAgentTaskHandler(
        db_session, ResearchAgentService(db_session, client=client)
    ).handle(
        AgentTask(
            task_type=MARKET_RESEARCH_TASK_TYPE,
            listing_external_id="ext-ra",
            dedupe_key="ra-off",
        )
    )
    assert res.status == "failed"
    assert res.error_type == "research_agent_provider_disabled"
    assert client.calls == []


def test_success_stores_only_agent_task_result_json_and_no_side_effects(
    db_session, monkeypatch
):
    row = listing(db_session)
    analysis = ListingAnalysis(
        listing_external_id=row.external_id,
        status="success",
        input_hash="h",
        score=7.0,
        verdict="watch",
    )
    snapshot = ListingDetailSnapshot(
        listing_id=row.id,
        listing_external_id=row.external_id,
        source_kind="manual",
        parse_status="success",
        content_hash="c",
        title="Помещение",
        description_text="desc +7 999 111-22-33",
    )
    db_session.add_all([analysis, snapshot])
    db_session.commit()
    enable(monkeypatch)
    client = FakeResearchClient()
    task = AgentTask(
        task_type=MARKET_RESEARCH_TASK_TYPE,
        listing_external_id=row.external_id,
        dedupe_key="ra-success",
        payload_json={"research_questions": ["Проверить спрос"], "max_queries": 2},
    )
    db_session.add(task)
    db_session.commit()
    out = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            MARKET_RESEARCH_TASK_TYPE: ResearchAgentTaskHandler(
                db_session, ResearchAgentService(db_session, client=client)
            )
        },
    ).run_pending(1)
    assert out["succeeded"] == 1
    saved = db_session.get(AgentTask, task.id).result_json
    assert saved["status"] == "success"
    assert saved["result"]["comparable_candidates"]
    assert saved["result"]["review_recommendation"]["should_review"] is True
    assert "+7 999" not in json.dumps(client.calls, ensure_ascii=False)
    assert "<b>" not in json.dumps(client.calls, ensure_ascii=False)
    assert db_session.scalar(select(func.count()).select_from(AlertSent)) == 0
    assert db_session.scalar(select(func.count()).select_from(KnowledgeNote)) == 0
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0
    assert db_session.get(ListingAnalysis, analysis.id).score == 7.0
    assert db_session.get(ListingAnalysis, analysis.id).verdict == "watch"


def test_failures_before_provider_call(db_session, monkeypatch):
    row = listing(db_session)
    other = ListingAnalysis(listing_external_id="other", input_hash="h")
    db_session.add(other)
    db_session.commit()
    enable(monkeypatch)
    client = FakeResearchClient()
    service = ResearchAgentService(db_session, client=client)
    cases = [
        ({}, "research_agent_invalid_payload"),
        ({"listing_external_id": "missing"}, "research_agent_listing_not_found"),
        (
            {"listing_external_id": row.external_id, "listing_analysis_id": other.id},
            "research_agent_analysis_not_found_or_mismatched",
        ),
        (
            {"listing_external_id": row.external_id, "research_profile": "unknown"},
            "research_agent_profile_unsupported",
        ),
    ]
    for payload, error_type in cases:
        res = ResearchAgentTaskHandler(db_session, service).handle(
            AgentTask(
                task_type=MARKET_RESEARCH_TASK_TYPE,
                payload_json=payload,
                dedupe_key="x" + error_type,
            )
        )
        assert res.status == "failed"
        assert res.error_type == error_type
    assert client.calls == []


def test_schema_validation_rejects_forbidden_sourceless_bad_indexes_and_low_confidence_review():
    with_ = valid_payload(score=1)
    try:
        validate_research_agent_response(
            with_,
            schema_version="research-agent-result-v1",
            research_profile="default",
            listing_external_id="ext-ra",
        )
        assert False
    except ResearchAgentError as exc:
        assert exc.error_type == "research_agent_forbidden_decision_output"
    for bad in [
        valid_payload(
            findings=[
                {
                    "topic": "location",
                    "claim": "x",
                    "evidence": "x",
                    "source_indexes": [],
                    "confidence": 0.5,
                }
            ]
        ),
        valid_payload(
            comparable_candidates=[{"source_indexes": [9], "confidence": 0.5}]
        ),
        valid_payload(confidence=1.5),
        valid_payload(
            risks=[
                {
                    "risk_code": "bad",
                    "description": "x",
                    "severity": "low",
                    "source_indexes": [],
                }
            ]
        ),
    ]:
        try:
            validate_research_agent_response(
                bad,
                schema_version="research-agent-result-v1",
                research_profile="default",
                listing_external_id="ext-ra",
            )
            assert False
        except ResearchAgentError as exc:
            assert exc.error_type == "research_agent_schema_validation_failed"
    low = valid_payload(
        confidence=0.3,
        review_recommendation={"should_review": False, "reason": "", "confidence": 0.3},
    )
    assert (
        validate_research_agent_response(
            low,
            schema_version="research-agent-result-v1",
            research_profile="default",
            listing_external_id="ext-ra",
        )["review_recommendation"]["should_review"]
        is True
    )


def _validate_payload(payload):
    return validate_research_agent_response(
        payload,
        schema_version="research-agent-result-v1",
        research_profile="default",
        listing_external_id="ext-ra",
    )


def test_valid_comparable_candidate_with_nullable_numeric_fields_passes():
    payload = valid_payload(
        comparable_candidates=[
            {
                "asset_type": "commercial",
                "deal_type": "rent",
                "location_text": "Москва",
                "area_m2": None,
                "price_rub": None,
                "rent_rub_per_month": None,
                "price_per_m2_rub": None,
                "rent_per_m2_rub": None,
                "source_indexes": [0],
                "similarity_notes": "Nullable numerics are allowed when evidence is missing.",
                "confidence": 0.7,
            }
        ]
    )

    comparable = _validate_payload(payload)["comparable_candidates"][0]

    assert comparable["asset_type"] == "commercial"
    assert comparable["deal_type"] == "rent"
    assert comparable["area_m2"] is None
    assert comparable["rent_per_m2_rub"] is None


def test_comparable_candidate_invalid_asset_type_fails_schema_validation():
    payload = valid_payload(
        comparable_candidates=[
            valid_payload()["comparable_candidates"][0] | {"asset_type": "office"}
        ]
    )

    with pytest.raises(ResearchAgentError) as exc:
        _validate_payload(payload)

    assert exc.value.error_type == "research_agent_schema_validation_failed"


def test_comparable_candidate_invalid_deal_type_fails_schema_validation():
    payload = valid_payload(
        comparable_candidates=[
            valid_payload()["comparable_candidates"][0] | {"deal_type": "leasehold"}
        ]
    )

    with pytest.raises(ResearchAgentError) as exc:
        _validate_payload(payload)

    assert exc.value.error_type == "research_agent_schema_validation_failed"


@pytest.mark.parametrize("bad_value", ["42", {"value": 42}, [42], True])
def test_comparable_candidate_numeric_field_invalid_type_fails_schema_validation(
    bad_value,
):
    payload = valid_payload(
        comparable_candidates=[
            valid_payload()["comparable_candidates"][0] | {"area_m2": bad_value}
        ]
    )

    with pytest.raises(ResearchAgentError) as exc:
        _validate_payload(payload)

    assert exc.value.error_type == "research_agent_schema_validation_failed"


def test_comparable_candidate_negative_numeric_field_fails_schema_validation():
    payload = valid_payload(
        comparable_candidates=[
            valid_payload()["comparable_candidates"][0] | {"price_rub": -1}
        ]
    )

    with pytest.raises(ResearchAgentError) as exc:
        _validate_payload(payload)

    assert exc.value.error_type == "research_agent_schema_validation_failed"


def test_comparable_candidate_without_valid_source_index_still_fails():
    payload = valid_payload(
        comparable_candidates=[
            valid_payload()["comparable_candidates"][0] | {"source_indexes": []}
        ]
    )

    with pytest.raises(ResearchAgentError) as exc:
        _validate_payload(payload)

    assert exc.value.error_type == "research_agent_schema_validation_failed"


def test_unsupported_and_misconfigured_provider_fail_closed(db_session, monkeypatch):
    listing(db_session)
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_provider", "bogus"
    )
    res = ResearchAgentTaskHandler(db_session).handle(
        AgentTask(
            task_type=MARKET_RESEARCH_TASK_TYPE,
            listing_external_id="ext-ra",
            dedupe_key="ra-bogus",
        )
    )
    assert res.error_type == "research_agent_provider_unsupported"
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_provider", "source_backed"
    )
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_api_key", ""
    )
    monkeypatch.setattr(
        "app.services.research_agent.settings.research_agent_base_url", ""
    )
    res = ResearchAgentTaskHandler(db_session).handle(
        AgentTask(
            task_type=MARKET_RESEARCH_TASK_TYPE,
            listing_external_id="ext-ra",
            dedupe_key="ra-mis",
        )
    )
    assert res.error_type == "research_agent_provider_misconfigured"
