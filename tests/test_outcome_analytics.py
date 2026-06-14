from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.db.base import Base
from app.models.human_review import HumanReview, InvestmentDecision
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_search_match import ListingSearchMatch
from app.models.search_job import SearchJob
from app.schemas.outcome_analytics import OutcomeAnalyticsRequest
from app.services.outcome_analytics import (
    HumanOutcomeAnalyticsService,
    extract_risk_flags,
    score_bucket,
)

AS_OF = datetime(2026, 6, 14, 12, tzinfo=UTC)


def dt(days: int) -> datetime:
    return (AS_OF + timedelta(days=days)).replace(tzinfo=None)


def add_review(db, external_id: str, **kwargs) -> HumanReview:
    now = kwargs.pop("updated_at", dt(0))
    review = HumanReview(
        listing_external_id=external_id,
        review_context_key=kwargs.pop(
            "review_context_key",
            f"ctx:{external_id}:{len(list(db.scalars(select(HumanReview))))}",
        ),
        review_status=kwargs.pop("review_status", "reviewed"),
        created_at=kwargs.pop("created_at", now),
        updated_at=now,
        reviewed_at=kwargs.pop("reviewed_at", now),
        **kwargs,
    )
    db.add(review)
    db.flush()
    return review


def add_analysis(
    db,
    external_id: str,
    score=None,
    verdict="watch",
    risks_json=None,
    facts_json=None,
    profile="default",
) -> ListingAnalysis:
    analysis = ListingAnalysis(
        listing_external_id=external_id,
        input_hash=f"h-{external_id}-{score}",
        status="success",
        score=score,
        verdict=verdict,
        risks_json=risks_json or {},
        facts_json=facts_json or {},
        profile=profile,
    )
    db.add(analysis)
    db.flush()
    return analysis


def report(db, **kwargs):
    return HumanOutcomeAnalyticsService(db).build_report(
        OutcomeAnalyticsRequest(as_of=AS_OF, **kwargs)
    )


def test_empty_db_returns_zeroed_report(db_session):
    r = report(db_session)
    assert r.totals.human_reviews_total == 0
    assert r.totals.human_reviews_in_period == 0
    assert r.totals.reviewed_listing_count == 0
    assert r.review_status_counts["reviewed"] == 0
    assert r.decision_counts.by_status["approved"] == 0
    assert r.request_hash
    assert r.stats_snapshot_hash


def test_validation_and_deterministic_period_window(db_session):
    with pytest.raises(ValidationError):
        OutcomeAnalyticsRequest(period_days=0)
    with pytest.raises(ValidationError):
        OutcomeAnalyticsRequest(max_examples_per_section=51)
    with pytest.raises(ValidationError):
        OutcomeAnalyticsRequest(date_basis="bad")
    with pytest.raises(ValidationError):
        OutcomeAnalyticsRequest(review_statuses=["bad"])
    r = report(db_session, period_days=7)
    assert r.period.period_start == AS_OF - timedelta(days=7)
    assert r.period.period_end == AS_OF


def test_period_and_coalesced_event_timestamps_for_reviews_and_decisions(db_session):
    inside = add_review(
        db_session, "inside", reviewed_at=None, updated_at=dt(-1), created_at=dt(-20)
    )
    add_review(
        db_session, "outside", reviewed_at=None, updated_at=dt(-40), created_at=dt(-40)
    )
    db_session.add(
        InvestmentDecision(
            human_review_id=inside.id,
            listing_external_id="inside",
            decision_type="send_to_expert",
            decision_status="approved",
            created_at=dt(-20),
            updated_at=dt(-1),
            decided_at=None,
        )
    )
    db_session.commit()
    r = report(db_session, period_days=7)
    assert r.totals.human_reviews_total == 2
    assert r.totals.human_reviews_in_period == 1
    assert r.totals.investment_decisions_in_period == 1
    assert r.decision_counts.by_type["send_to_expert"] == 1
    assert r.decision_counts.by_status["approved"] == 1
    assert (
        report(
            db_session, period_days=7, date_basis="reviewed_at"
        ).totals.human_reviews_in_period
        == 0
    )


def test_hashes_are_stable_and_scoped_to_request_and_selected_data(db_session):
    add_review(
        db_session,
        "stable",
        human_verdict="interesting",
        updated_at=dt(-1),
        reviewed_at=dt(-1),
    )
    first = report(db_session, period_days=7, listing_external_ids=["stable"])
    second = report(db_session, period_days=7, listing_external_ids=["stable"])
    assert first.request_hash == second.request_hash
    assert first.stats_snapshot_hash == second.stats_snapshot_hash
    assert first.request_hash != report(db_session, period_days=8).request_hash
    add_review(
        db_session,
        "old",
        human_verdict="false_positive",
        updated_at=dt(-100),
        reviewed_at=dt(-100),
    )
    assert (
        first.stats_snapshot_hash
        == report(db_session, period_days=7, listing_external_ids=["stable"]).stats_snapshot_hash
    )
    add_review(
        db_session,
        "new",
        human_verdict="false_positive",
        updated_at=dt(-1),
        reviewed_at=dt(-1),
    )
    assert (
        first.stats_snapshot_hash
        != report(db_session, period_days=7, listing_external_ids=["stable", "new"]).stats_snapshot_hash
    )


def test_counting_units_and_human_verdicts_outcomes_and_derived_signals(db_session):
    add_review(
        db_session,
        "same",
        review_context_key="c1",
        human_verdict="interesting",
        watchlist=True,
        outcome_status="watchlist",
    )
    add_review(
        db_session,
        "same",
        review_context_key="c2",
        human_verdict="not_interesting",
        outcome_status="closed",
    )
    add_review(db_session, "fp", human_verdict="false_positive")
    add_review(db_session, "fn", human_verdict="false_negative")
    add_review(
        db_session,
        "more",
        human_verdict="needs_more_data",
        outcome_status="sent_to_expert",
    )
    add_review(db_session, "deal", outcome_status="deal_candidate")
    add_review(db_session, "done", outcome_status="deal_done")
    add_review(db_session, "lost", outcome_status="deal_lost")
    r = report(db_session)
    assert r.totals.human_reviews_in_period == 8
    assert r.totals.reviewed_listing_count == 7
    assert r.totals.review_context_count == 8
    assert r.human_verdict_counts["interesting"] == 1
    assert r.human_verdict_counts["not_interesting"] == 1
    assert r.human_verdict_counts["needs_more_data"] == 1
    assert r.false_positive_counts["explicit"] == 1
    assert r.false_negative_counts["explicit"] == 1
    assert r.outcome_status_counts["closed"] == 1
    assert r.signal_counts.human_positive_signal_count == 4
    assert r.signal_counts.human_negative_signal_count == 3
    assert r.signal_counts.negative_signals.get("outcome_status_closed") is None


def test_analysis_alignment_score_buckets_risk_flags_and_no_latest_fallback(db_session):
    scores = [39, 40, 60, 75, 90, None]
    expected = ["0-39", "40-59", "60-74", "75-89", "90-100", "unknown"]
    assert [score_bucket(s) for s in scores] == expected
    for i, score in enumerate(scores):
        analysis = add_analysis(
            db_session,
            f"l{i}",
            score=score,
            verdict="strong",
            profile="p",
            facts_json={"rent_source": "manual", "market_evidence_used": True},
            risks_json={
                "flags": ["missing_area", "missing_area", 1, ""],
                "items": ["ignored"],
            },
        )
        add_review(
            db_session,
            f"l{i}",
            listing_analysis_id=analysis.id,
            human_verdict="interesting",
        )
    add_analysis(db_session, "unlinked", score=99, verdict="strong")
    add_review(db_session, "unlinked", human_verdict="interesting")
    r = report(db_session)
    assert r.totals.linked_analysis_count == 6
    assert r.totals.unlinked_review_count == 1
    assert r.analysis_alignment["by_verdict"]["strong"] == 6
    assert r.analysis_alignment["by_profile"]["p"] == 6
    assert r.score_bucket_stats["unknown"].total_reviews == 1
    assert r.risk_flag_stats["missing_area"].total_reviews == 6
    assert extract_risk_flags({"flags": "legacy"}) == ["legacy"]
    assert extract_risk_flags({"risk_flags": ["old", 2], "items": ["ignored"]}) == [
        "old"
    ]


def test_search_stats_use_explicit_search_job_id_and_examples_are_bounded(db_session):
    search = SearchJob(name="S", source_url="https://example.test")
    db_session.add(search)
    db_session.flush()
    listing = Listing(external_id="fp", url="https://example.test/fp", title="A" * 200)
    db_session.add(listing)
    db_session.flush()
    analysis = add_analysis(
        db_session, "fp", score=80, verdict="strong", risks_json={"flags": ["risk"]}
    )
    add_review(
        db_session,
        "fp",
        listing_id=listing.id,
        search_job_id=search.id,
        listing_analysis_id=analysis.id,
        human_verdict="false_positive",
    )
    low = add_analysis(db_session, "low", score=50, verdict="weak")
    add_review(
        db_session,
        "low",
        listing_analysis_id=low.id,
        human_verdict="interesting",
        outcome_status="sent_to_expert",
    )
    add_review(db_session, "deal", outcome_status="deal_candidate")
    add_review(db_session, "done", outcome_status="deal_done")
    db_session.add(
        ListingSearchMatch(listing_external_id="low", search_job_id=search.id)
    )
    db_session.commit()
    r = report(db_session, max_examples_per_section=1)
    assert len(r.search_stats) == 2
    explicit = next(s for s in r.search_stats if s.search_job_id == search.id)
    no_search = next(s for s in r.search_stats if s.search_job_id is None)
    assert explicit.reviews_count == 1
    assert no_search.reviews_count == 3
    assert explicit.top_risk_flags == {"risk": 1}
    assert len(r.examples.false_positive_examples) == 1
    assert r.examples.false_positive_examples[0].review_context_key
    assert len(r.examples.false_positive_examples[0].short_title) == 120
    assert len(r.examples.high_score_rejected_examples) == 1
    assert len(r.examples.low_score_interesting_examples) == 1
    assert len(r.examples.sent_to_expert_examples) == 1


def test_decision_filters_follow_listing_search_and_review_context(db_session):
    search_a = SearchJob(name="A", source_url="https://example.test/a")
    search_b = SearchJob(name="B", source_url="https://example.test/b")
    db_session.add_all([search_a, search_b])
    db_session.flush()
    review_a = add_review(
        db_session,
        "decision-a",
        search_job_id=search_a.id,
        human_verdict="interesting",
        outcome_status="sent_to_expert",
    )
    review_b = add_review(
        db_session,
        "decision-b",
        search_job_id=search_b.id,
        human_verdict="not_interesting",
        outcome_status="deal_lost",
    )
    db_session.add_all(
        [
            InvestmentDecision(
                human_review_id=review_a.id,
                listing_external_id="decision-a",
                decision_type="send_to_expert",
                decision_status="approved",
                created_at=dt(-1),
                updated_at=dt(-1),
                decided_at=dt(-1),
            ),
            InvestmentDecision(
                human_review_id=review_b.id,
                listing_external_id="decision-b",
                decision_type="reject",
                decision_status="rejected",
                created_at=dt(-1),
                updated_at=dt(-1),
                decided_at=dt(-1),
            ),
        ]
    )
    db_session.commit()

    by_listing = report(db_session, listing_external_ids=["decision-a"])
    assert by_listing.decision_counts.by_status["approved"] == 1
    assert by_listing.decision_counts.by_status["rejected"] == 0

    by_search = report(db_session, search_job_ids=[search_b.id])
    assert by_search.decision_counts.by_type["reject"] == 1
    assert by_search.decision_counts.by_type["send_to_expert"] == 0

    by_review_context = report(
        db_session,
        human_verdicts=["interesting"],
        outcome_statuses=["sent_to_expert"],
    )
    assert by_review_context.decision_counts.by_status["approved"] == 1
    assert by_review_context.decision_counts.by_status["rejected"] == 0


def test_stats_hash_ignores_unrelated_decision_outside_selected_filters(db_session):
    selected = add_review(db_session, "selected", human_verdict="interesting")
    db_session.add(
        InvestmentDecision(
            human_review_id=selected.id,
            listing_external_id="selected",
            decision_type="send_to_expert",
            decision_status="approved",
            created_at=dt(-1),
            updated_at=dt(-1),
            decided_at=dt(-1),
        )
    )
    db_session.commit()
    first = report(db_session, listing_external_ids=["selected"])

    unrelated = add_review(db_session, "unrelated", human_verdict="not_interesting")
    db_session.add(
        InvestmentDecision(
            human_review_id=unrelated.id,
            listing_external_id="unrelated",
            decision_type="reject",
            decision_status="rejected",
            created_at=dt(-1),
            updated_at=dt(-1),
            decided_at=dt(-1),
        )
    )
    db_session.commit()

    second = report(db_session, listing_external_ids=["selected"])
    assert second.decision_counts.by_status["approved"] == 1
    assert second.decision_counts.by_status["rejected"] == 0
    assert first.stats_snapshot_hash == second.stats_snapshot_hash


def test_read_only_no_side_effects_or_session_writes(db_session, monkeypatch):
    add_review(db_session, "safe")
    db_session.commit()
    tables = [
        "human_reviews",
        "human_review_actions",
        "investment_decisions",
        "listings",
        "listing_analyses",
        "alerts_sent",
        "market_research_runs",
        "market_evidence_items",
        "agent_tasks",
        "knowledge_notes",
        "listing_enrichments",
        "listing_detail_snapshots",
        "search_jobs",
    ]
    before = {
        name: db_session.scalar(
            select(func.count()).select_from(Base.metadata.tables[name])
        )
        for name in tables
    }
    monkeypatch.setattr(
        db_session, "add", lambda *a, **k: pytest.fail("session.add called")
    )
    monkeypatch.setattr(
        db_session, "delete", lambda *a, **k: pytest.fail("session.delete called")
    )
    monkeypatch.setattr(
        db_session, "commit", lambda *a, **k: pytest.fail("session.commit called")
    )
    report(db_session)
    after = {
        name: db_session.scalar(
            select(func.count()).select_from(Base.metadata.tables[name])
        )
        for name in tables
    }
    assert after == before
