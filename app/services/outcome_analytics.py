from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from app.models.human_review import (
    HUMAN_VERDICTS,
    OUTCOME_STATUSES,
    REVIEW_STATUSES,
    HumanReview,
    InvestmentDecision,
)
from app.models.listing_analysis import ListingAnalysis
from app.repositories.outcome_analytics import OutcomeAnalyticsRepository
from app.schemas.outcome_analytics import (
    DecisionCounts,
    OutcomeAnalyticsReport,
    OutcomeAnalyticsRequest,
    OutcomeBucketStats,
    OutcomeExample,
    OutcomeExamples,
    OutcomePeriod,
    OutcomeTotals,
    SearchOutcomeStats,
    SignalCounts,
)

REPORT_VERSION = "pr18b-outcome-analytics-v1"
SCORE_BUCKETS = ("0-39", "40-59", "60-74", "75-89", "90-100", "unknown")
POSITIVE_OUTCOMES = {
    "watchlist",
    "sent_to_expert",
    "deal_candidate",
    "offer_made",
    "deal_done",
}
NEGATIVE_OUTCOMES = {"rejected_after_call", "deal_lost"}


def utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score < 40:
        return "0-39"
    if score < 60:
        return "40-59"
    if score < 75:
        return "60-74"
    if score < 90:
        return "75-89"
    return "90-100"


def extract_risk_flags(risks_json: Any) -> list[str]:
    if not isinstance(risks_json, dict):
        return []
    raw_flags = risks_json.get("flags")
    if isinstance(raw_flags, list):
        return sorted(
            {
                flag.strip()
                for flag in raw_flags
                if isinstance(flag, str) and flag.strip()
            }
        )
    if isinstance(raw_flags, str) and raw_flags.strip():
        return [raw_flags.strip()]
    legacy = risks_json.get("risk_flags")
    if isinstance(legacy, list):
        return sorted(
            {flag.strip() for flag in legacy if isinstance(flag, str) and flag.strip()}
        )
    return []


def stable_hash(payload: Any) -> str:
    import json

    return sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


class HumanOutcomeAnalyticsService:
    def __init__(self, db: Session) -> None:
        self.repo = OutcomeAnalyticsRepository(db)

    def build_report(
        self, request: OutcomeAnalyticsRequest | None = None
    ) -> OutcomeAnalyticsReport:
        request = request or OutcomeAnalyticsRequest()
        as_of = utc_aware(request.as_of) or datetime.now(UTC)
        period_end = as_of
        period_start = as_of - timedelta(days=request.period_days)
        period = OutcomePeriod(
            as_of=as_of,
            period_start=period_start,
            period_end=period_end,
            period_days=request.period_days,
            date_basis=request.date_basis,
        )
        req_payload = request.model_dump(mode="json") | {
            "as_of": as_of.isoformat(),
            "schema_version": REPORT_VERSION,
        }
        request_hash = stable_hash(req_payload)
        filters = request.model_dump()
        rows = self.repo.get_reviews_for_period(
            period_start=period_start,
            period_end=period_end,
            date_basis=request.date_basis,
            filters=filters,
        )
        decisions = (
            self.repo.get_decisions_for_period(
                period_start=period_start, period_end=period_end, filters=filters
            )
            if request.include_decision_stats
            else []
        )

        report = self._aggregate(request, period, rows, decisions, request_hash)
        snapshot_payload = report.model_dump(
            mode="json", exclude={"stats_snapshot_hash"}
        )
        snapshot_payload["totals"]["human_reviews_total"] = None
        snapshot_payload["totals"]["investment_decisions_total"] = None
        report.stats_snapshot_hash = stable_hash(snapshot_payload)
        return report

    def _aggregate(
        self,
        request: OutcomeAnalyticsRequest,
        period: OutcomePeriod,
        rows: list[Any],
        decisions: list[InvestmentDecision],
        request_hash: str,
    ) -> OutcomeAnalyticsReport:
        review_rows = [(r[0], r[1], r[2]) for r in rows]
        linked = [
            review for review, analysis, _title in review_rows if analysis is not None
        ]
        totals = OutcomeTotals(
            human_reviews_total=self.repo.count_reviews_total(),
            human_reviews_in_period=len(review_rows),
            review_context_count=len(
                {r.review_context_key for r, _a, _t in review_rows}
            ),
            reviewed_listing_count=len(
                {r.listing_external_id for r, _a, _t in review_rows}
            ),
            linked_analysis_count=len(linked),
            unlinked_review_count=len(review_rows) - len(linked),
            investment_decisions_total=self.repo.count_decisions_total(),
            investment_decisions_in_period=len(decisions),
        )
        review_status_counts = self._zeroed_counter(
            REVIEW_STATUSES, [r.review_status for r, _a, _t in review_rows]
        )
        human_verdict_counts = self._zeroed_counter(
            HUMAN_VERDICTS,
            [r.human_verdict for r, _a, _t in review_rows if r.human_verdict],
        )
        outcome_status_counts = self._zeroed_counter(
            OUTCOME_STATUSES,
            [r.outcome_status for r, _a, _t in review_rows if r.outcome_status],
        )
        bucket_stats = {name: OutcomeBucketStats() for name in SCORE_BUCKETS}
        risk_stats: dict[str, OutcomeBucketStats] = {}
        search_groups: dict[
            int | None, list[tuple[HumanReview, ListingAnalysis | None, str | None]]
        ] = defaultdict(list)
        analysis_alignment: dict[str, dict[str, int]] = {
            "alignment_mode": {"explicit_only": 1},
            "by_verdict": {},
            "by_profile": {},
            "by_rent_source": {},
            "by_market_evidence_usage": {},
        }
        examples = OutcomeExamples()
        pos_signal_count = Counter()
        neg_signal_count = Counter()
        pos_reviews = set()
        neg_reviews = set()

        for review, analysis, title in review_rows:
            pos, neg, pos_names, neg_names = self._signals(review)
            if pos:
                pos_reviews.add(review.id)
            if neg:
                neg_reviews.add(review.id)
            pos_signal_count.update(pos_names)
            neg_signal_count.update(neg_names)
            if analysis is not None:
                self._inc_alignment(analysis_alignment, analysis)
                self._add_bucket(
                    bucket_stats[score_bucket(analysis.score)], review, pos, neg
                )
                if request.include_risk_flag_stats:
                    for flag in extract_risk_flags(analysis.risks_json):
                        risk_stats.setdefault(flag, OutcomeBucketStats())
                        self._add_bucket(risk_stats[flag], review, pos, neg)
            if request.include_search_stats:
                search_groups[review.search_job_id].append((review, analysis, title))
            if request.include_examples:
                self._collect_examples(
                    examples, review, analysis, title, request.max_examples_per_section
                )

        decision_counts = DecisionCounts()
        for decision in decisions:
            decision_counts.by_type[decision.decision_type] = (
                decision_counts.by_type.get(decision.decision_type, 0) + 1
            )
            decision_counts.by_status[decision.decision_status] = (
                decision_counts.by_status.get(decision.decision_status, 0) + 1
            )
        search_stats = [
            self._search_stats(search_id, group)
            for search_id, group in sorted(
                search_groups.items(),
                key=lambda item: -1 if item[0] is None else item[0],
            )
        ]
        report = OutcomeAnalyticsReport(
            request_hash=request_hash,
            stats_snapshot_hash="",
            period=period,
            totals=totals,
            review_status_counts=review_status_counts,
            human_verdict_counts=human_verdict_counts,
            outcome_status_counts=outcome_status_counts,
            watchlist_counts={
                "true": sum(1 for r, _a, _t in review_rows if r.watchlist),
                "false": sum(1 for r, _a, _t in review_rows if not r.watchlist),
            },
            false_positive_counts={
                "explicit": sum(
                    1 for r, _a, _t in review_rows if self._is_false_positive(r)
                )
            },
            false_negative_counts={
                "explicit": sum(
                    1 for r, _a, _t in review_rows if self._is_false_negative(r)
                )
            },
            signal_counts=SignalCounts(
                positive_signals=dict(sorted(pos_signal_count.items())),
                negative_signals=dict(sorted(neg_signal_count.items())),
                human_positive_signal_count=len(pos_reviews),
                human_negative_signal_count=len(neg_reviews),
            ),
            decision_counts=decision_counts,
            analysis_alignment=analysis_alignment,
            score_bucket_stats=bucket_stats
            if request.include_score_bucket_stats
            else {},
            risk_flag_stats=dict(sorted(risk_stats.items()))
            if request.include_risk_flag_stats
            else {},
            search_stats=search_stats,
            examples=examples if request.include_examples else OutcomeExamples(),
            limitations=[
                "Analysis alignment uses explicit human_reviews.listing_analysis_id only.",
                "Search stats use explicit human_reviews.search_job_id only.",
                "Closed is counted as an outcome status but is not treated as a negative signal.",
            ],
        )
        return report

    @staticmethod
    def _zeroed_counter(keys: set[str], values: list[str]) -> dict[str, int]:
        counts = Counter(values)
        return {key: counts.get(key, 0) for key in sorted(keys)}

    @staticmethod
    def _signals(review: HumanReview) -> tuple[bool, bool, list[str], list[str]]:
        pos = []
        neg = []
        if review.human_verdict == "interesting":
            pos.append("human_verdict_interesting")
        if review.watchlist:
            pos.append("watchlist")
        if review.outcome_status in POSITIVE_OUTCOMES:
            pos.append(f"outcome_status_{review.outcome_status}")
        if review.human_verdict == "not_interesting":
            neg.append("human_verdict_not_interesting")
        if review.human_verdict == "false_positive":
            neg.append("human_verdict_false_positive")
        if review.false_positive is True:
            neg.append("false_positive_true")
        if review.outcome_status in NEGATIVE_OUTCOMES:
            neg.append(f"outcome_status_{review.outcome_status}")
        if review.false_negative is True:
            pos.append("false_negative_true")
        return bool(pos), bool(neg), pos, neg

    @staticmethod
    def _is_false_positive(review: HumanReview) -> bool:
        return review.human_verdict == "false_positive" or review.false_positive is True

    @staticmethod
    def _is_false_negative(review: HumanReview) -> bool:
        return review.human_verdict == "false_negative" or review.false_negative is True

    def _add_bucket(
        self, stats: OutcomeBucketStats, review: HumanReview, pos: bool, neg: bool
    ) -> None:
        stats.total_reviews += 1
        stats.interesting_count += int(review.human_verdict == "interesting")
        stats.not_interesting_count += int(review.human_verdict == "not_interesting")
        stats.false_positive_count += int(self._is_false_positive(review))
        stats.false_negative_count += int(self._is_false_negative(review))
        stats.watchlist_count += int(review.watchlist)
        stats.sent_to_expert_count += int(review.outcome_status == "sent_to_expert")
        stats.deal_candidate_count += int(review.outcome_status == "deal_candidate")
        stats.deal_done_count += int(review.outcome_status == "deal_done")
        stats.human_positive_signal_count += int(pos)
        stats.human_negative_signal_count += int(neg)

    @staticmethod
    def _inc_alignment(
        alignment: dict[str, dict[str, int]], analysis: ListingAnalysis
    ) -> None:
        if analysis.verdict:
            alignment["by_verdict"][analysis.verdict] = (
                alignment["by_verdict"].get(analysis.verdict, 0) + 1
            )
        alignment["by_profile"][analysis.profile] = (
            alignment["by_profile"].get(analysis.profile, 0) + 1
        )
        facts = analysis.facts_json if isinstance(analysis.facts_json, dict) else {}
        rent_source = facts.get("rent_source")
        if isinstance(rent_source, str):
            alignment["by_rent_source"][rent_source] = (
                alignment["by_rent_source"].get(rent_source, 0) + 1
            )
        evidence = facts.get("market_evidence_used")
        if evidence is not None:
            key = str(bool(evidence)).lower()
            alignment["by_market_evidence_usage"][key] = (
                alignment["by_market_evidence_usage"].get(key, 0) + 1
            )

    def _collect_examples(
        self,
        examples: OutcomeExamples,
        review: HumanReview,
        analysis: ListingAnalysis | None,
        title: str | None,
        limit: int,
    ) -> None:
        item = OutcomeExample(
            listing_external_id=review.listing_external_id,
            listing_id=review.listing_id,
            search_job_id=review.search_job_id,
            listing_analysis_id=review.listing_analysis_id,
            review_context_key=review.review_context_key,
            score=analysis.score if analysis else None,
            verdict=analysis.verdict if analysis else None,
            review_status=review.review_status,
            human_verdict=review.human_verdict,
            outcome_status=review.outcome_status,
            watchlist=review.watchlist,
            false_positive=review.false_positive,
            false_negative=review.false_negative,
            reviewed_at=utc_aware(review.reviewed_at),
            updated_at=utc_aware(review.updated_at),
            short_title=(title[:120] if title else None),
        )
        targets = []
        if self._is_false_positive(review):
            targets.append(examples.false_positive_examples)
        if self._is_false_negative(review):
            targets.append(examples.false_negative_examples)
        if (
            analysis
            and analysis.score is not None
            and analysis.score >= 75
            and (
                review.human_verdict in {"not_interesting", "false_positive"}
                or review.false_positive is True
            )
        ):
            targets.append(examples.high_score_rejected_examples)
        if (
            analysis
            and analysis.score is not None
            and analysis.score < 60
            and review.human_verdict in {"interesting", "false_negative"}
        ):
            targets.append(examples.low_score_interesting_examples)
        if review.outcome_status == "sent_to_expert":
            targets.append(examples.sent_to_expert_examples)
        if review.outcome_status == "deal_candidate":
            targets.append(examples.deal_candidate_examples)
        if review.outcome_status == "deal_done":
            targets.append(examples.deal_done_examples)
        for target in targets:
            if len(target) < limit:
                target.append(item)

    def _search_stats(
        self,
        search_id: int | None,
        group: list[tuple[HumanReview, ListingAnalysis | None, str | None]],
    ) -> SearchOutcomeStats:
        stats = SearchOutcomeStats(
            search_job_id=search_id,
            reviews_count=len(group),
            reviewed_listing_count=len({r.listing_external_id for r, _a, _t in group}),
            linked_analysis_count=sum(1 for _r, a, _t in group if a),
        )
        scores = []
        for review, analysis, _title in group:
            pos, neg, _p, _n = self._signals(review)
            self._add_bucket(stats, review, pos, neg)
            if analysis and analysis.score is not None:
                scores.append(analysis.score)
                stats.score_bucket_distribution[score_bucket(analysis.score)] = (
                    stats.score_bucket_distribution.get(score_bucket(analysis.score), 0)
                    + 1
                )
                for flag in extract_risk_flags(analysis.risks_json):
                    stats.top_risk_flags[flag] = stats.top_risk_flags.get(flag, 0) + 1
        stats.average_score = round(sum(scores) / len(scores), 4) if scores else None
        stats.top_risk_flags = dict(
            sorted(stats.top_risk_flags.items(), key=lambda item: (-item[1], item[0]))[
                :10
            ]
        )
        stats.score_bucket_distribution = {
            bucket: stats.score_bucket_distribution.get(bucket, 0)
            for bucket in SCORE_BUCKETS
        }
        return stats
