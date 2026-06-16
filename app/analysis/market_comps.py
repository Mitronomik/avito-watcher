from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
import hashlib
import json
from statistics import median
from typing import Any

from app.analysis.config import AnalysisConfig
from app.models.market_evidence import MarketEvidenceItem

DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MIN_COMPS = 3
DEFAULT_MAX_COMPS = 10
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_STRATEGY = "median"
DEFAULT_MISMATCH_THRESHOLD = 0.25
COMPARABLE_SELECTION_POLICY_VERSION = "v2"
COMPARABLE_SELECTION_MAX_CANDIDATES = 200
COMPARABLE_SELECTION_DEFAULT_CANDIDATE_LIMIT = 50
COMPARABLE_SELECTION_MAX_REJECTED_FACTS = 10
COMPARABLE_SELECTION_AREA_TOLERANCE_PCT = 0.25
COMPARABLE_SELECTION_MAX_EVIDENCE_AGE_DAYS = 30
COMPARABLE_QUALITY_MODEL_VERSION = "v0"
QUALITY_HIGH_THRESHOLD = 80
QUALITY_MEDIUM_THRESHOLD = 60
QUALITY_LOW_THRESHOLD = 35
QUALITY_STALE_DAYS = 30
QUALITY_MAX_AGE_DAYS = 90
QUALITY_PENALTIES = {
    "missing_source_url": 30,
    "stale_evidence": 25,
    "area_unknown": 5,
    "area_band_mismatch": 20,
    "location_unknown": 5,
    "location_mismatch": 20,
}
EVIDENCE_CONFIDENCE_CAP_WEAK = 0.5
EVIDENCE_CONFIDENCE_CAP_INDICATIVE = 0.35
MARKET_EVIDENCE_POLICY_SAME_LISTING = "same_listing"
MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY = "same_location_key"
ALLOWED_MARKET_EVIDENCE_POLICIES = {
    MARKET_EVIDENCE_POLICY_SAME_LISTING,
    MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY,
}

ADJUSTED_COMPARABLE_MODEL_VERSION = "v0"
ADJUSTED_COMPARABLE_CONFIG_VERSION = "v0"
MAX_ADJUSTMENT_ABS_PCT = 0.25
MAX_SINGLE_ADJUSTMENT_ABS_PCT = 0.10
AREA_ADJUSTMENT_MAX_ABS_PCT = 0.08
CONDITION_CAPEX_ADJUSTMENT_PCT = 0.05
FIRST_LINE_ADJUSTMENT_PCT = 0.05
FLOOR_ACCESS_ADJUSTMENT_PCT = 0.03
ASKING_TO_EFFECTIVE_DISCOUNT_PCT = 0.05
FRESHNESS_CONFIDENCE_PENALTY = "low"
ADJUSTED_COMPARABLE_STALE_DAYS = 30
MIN_ADJUSTED_COMP_COUNT_FOR_BASE_CONFIDENCE = 3
MIN_HIGH_OR_MEDIUM_QUALITY_SHARE = 0.5
MAX_ADJUSTED_COMP_FACT_ITEMS = 10

SOURCE_QUALITY_MODEL_VERSION = "v0"
SOURCE_QUALITY_CONFIG_VERSION = "v0"
SOURCE_QUALITY_STALE_DAYS = 30
SOURCE_QUALITY_AGING_DAYS = 14
MAX_SOURCE_QUALITY_FACT_ITEMS = 10
SOURCE_QUALITY_CAP_WEAK = 0.50
SOURCE_QUALITY_CAP_INDICATIVE = 0.35
ALLOWED_SOURCE_TYPES = {"asking", "confirmed", "effective", "manual", "unknown"}
ALLOWED_VERIFICATION_STATUSES = {"verified", "human_verified", "unverified", "unknown"}

REASON_AREA_ADJUSTMENT = "area_adjustment"
REASON_CONDITION_ADJUSTMENT = "condition_adjustment"
REASON_FIRST_LINE_ADJUSTMENT = "first_line_adjustment"
REASON_FLOOR_ACCESS_ADJUSTMENT = "floor_access_adjustment"
REASON_ASKING_TO_EFFECTIVE_DISCOUNT = "asking_to_effective_discount"


@dataclass(frozen=True)
class ResolvedMarketEvidenceConfig:
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    min_comps: int = DEFAULT_MIN_COMPS
    max_comps: int = DEFAULT_MAX_COMPS
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    location_key: str | None = None
    rent_strategy: str = DEFAULT_STRATEGY
    manual_mismatch_threshold_pct: float = DEFAULT_MISMATCH_THRESHOLD
    matching_policy: str = MARKET_EVIDENCE_POLICY_SAME_LISTING


@dataclass(frozen=True)
class MarketCompInput:
    id: int
    listing_external_id: str | None
    content_hash: str
    confidence: float
    checked_at: datetime
    expires_at: datetime | None
    source_url_normalized: str
    asset_type: str
    deal_type: str
    location_key: str | None
    rent_per_m2_rub: float | None
    rent_rub_per_month: float | None
    area_m2: float | None = None
    rent_period: str | None = "month"
    source_type: str | None = None
    condition: str | None = None
    capex_required: bool | None = None
    first_line: bool | None = None
    floor_access: str | None = None
    verification_status: str | None = None
    human_verified: bool | None = None
    verified_by_human: bool | None = None
    reviewed_by_human: bool | None = None
    source_verified: bool | None = None
    source_origin: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class ComparableTargetContext:
    target_listing_id: int | None
    target_listing_external_id: str | None
    profile: str
    estimate_purpose: str
    asset_type: str
    deal_type: str
    location_key: str | None
    area_m2: float | None
    first_line: bool | None = None
    floor_access: str | None = None
    condition: str | None = None
    capex_required: bool | None = None


@dataclass(frozen=True)
class ComparableSelectionDecision:
    evidence_id: int
    source_listing_external_id: str | None
    selection_status: str
    selection_stage: str
    selection_reason: str | None = None
    rejection_reason: str | None = None
    selection_flags: list[str] = field(default_factory=list)
    matched_on: list[str] = field(default_factory=list)
    selection_policy_version: str = COMPARABLE_SELECTION_POLICY_VERSION


@dataclass(frozen=True)
class ComparableSelectionResult:
    policy_version: str
    as_of: datetime
    target_context: ComparableTargetContext
    candidate_limit: int
    selected_limit: int
    max_rejected_facts: int
    decisions: list[ComparableSelectionDecision]
    selected_items: list[MarketCompInput]
    review_reasons: list[str]
    truncated_candidates: bool


@dataclass(frozen=True)
class SelectedMarketEvidenceContext:
    items: list[MarketCompInput]
    excluded_counts_by_reason: dict[str, int]
    limitations: list[str]
    retrieval_as_of_datetime: datetime
    retrieval_as_of_date: date
    config: ResolvedMarketEvidenceConfig
    target_listing_external_id: str | None = None
    selection_result: ComparableSelectionResult | None = None


@dataclass(frozen=True)
class ComparableQualityResult:
    evidence_id: int
    quality_score: int
    similarity_score: int
    quality_bucket: str
    accepted: bool
    quality_flags: list[str]
    rejection_reason: str | None = None


@dataclass(frozen=True)
class EvidenceSetQualitySummary:
    comparable_quality_model_version: str
    total_candidates: int
    accepted_count: int
    rejected_count: int
    high_quality_count: int
    medium_quality_count: int
    low_quality_count: int
    best_quality_score: int | None
    median_quality_score: float | None
    evidence_quality_bucket: str
    evidence_confidence_cap: float | None
    evidence_quality_reasons: list[str]
    force_review: bool
    review_reasons: list[str]


@dataclass(frozen=True)
class ComparableQualityAssessment:
    comparable_quality_model_version: str
    as_of: datetime
    results: list[ComparableQualityResult]
    summary: EvidenceSetQualitySummary

    @property
    def accepted_item_ids(self) -> set[int]:
        return {r.evidence_id for r in self.results if r.accepted}




@dataclass(frozen=True)
class SourceQualityConfig:
    stale_days: int = SOURCE_QUALITY_STALE_DAYS
    aging_days: int = SOURCE_QUALITY_AGING_DAYS
    max_fact_items: int = MAX_SOURCE_QUALITY_FACT_ITEMS
    min_strong_or_medium_trace_share: float = 0.5
    min_verified_or_known_source_type_share: float = 0.5


@dataclass(frozen=True)
class SourceQualityItem:
    evidence_id: int
    source_listing_external_id: str | None
    source_type: str
    verification_status: str
    trace_strength: str
    freshness_bucket: str
    source_origin: str | None
    confidence_bucket: str
    confidence_cap: float | None
    source_quality_flags: list[str]
    source_quality_reasons: list[str]


@dataclass(frozen=True)
class SourceQualityAssessment:
    source_quality_model_version: str
    source_quality_config_version: str
    as_of: datetime
    target_context: ComparableTargetContext
    assessed_count: int
    weak_or_missing_trace_count: int
    unknown_source_type_count: int
    verified_count: int
    stale_or_expired_count: int
    evidence_confidence_cap: float | None
    source_quality_bucket: str
    review_reasons: list[str]
    items: list[SourceQualityItem]

    def facts(self, *, max_items: int = MAX_SOURCE_QUALITY_FACT_ITEMS) -> dict[str, Any]:
        return source_quality_facts(self, max_items=max_items)


@dataclass(frozen=True)
class AdjustedComparableConfig:
    max_adjustment_abs_pct: float = MAX_ADJUSTMENT_ABS_PCT
    max_single_adjustment_abs_pct: float = MAX_SINGLE_ADJUSTMENT_ABS_PCT
    area_adjustment_max_abs_pct: float = AREA_ADJUSTMENT_MAX_ABS_PCT
    condition_capex_adjustment_pct: float = CONDITION_CAPEX_ADJUSTMENT_PCT
    first_line_adjustment_pct: float = FIRST_LINE_ADJUSTMENT_PCT
    floor_access_adjustment_pct: float = FLOOR_ACCESS_ADJUSTMENT_PCT
    asking_to_effective_discount_pct: float = ASKING_TO_EFFECTIVE_DISCOUNT_PCT
    stale_days: int = ADJUSTED_COMPARABLE_STALE_DAYS
    min_adjusted_comp_count_for_base_confidence: int = (
        MIN_ADJUSTED_COMP_COUNT_FOR_BASE_CONFIDENCE
    )
    min_high_or_medium_quality_share: float = MIN_HIGH_OR_MEDIUM_QUALITY_SHARE
    max_fact_items: int = MAX_ADJUSTED_COMP_FACT_ITEMS


@dataclass(frozen=True)
class AdjustedComparableItem:
    evidence_id: int
    source_listing_external_id: str | None
    raw_rent: float | None
    raw_rent_period: str | None
    raw_rent_per_m2: float
    adjusted_rent: float | None
    adjusted_rent_per_m2: float
    adjustment_delta_pct: float
    adjustment_reasons: list[str]
    adjustment_flags: list[str]
    adjustment_cap_applied: bool
    quality_bucket: str | None
    selection_reason: str | None
    source_trace_ref: str | None


@dataclass(frozen=True)
class AdjustedComparableResult:
    version: str
    config_version: str
    as_of: datetime
    target_context: ComparableTargetContext
    selected_count: int
    adjusted_count: int
    excluded_from_adjusted_count: int
    items: list[AdjustedComparableItem]
    raw_median_rent_per_m2: float | None
    adjusted_median_rent_per_m2: float | None
    raw_median_rent: float | None
    adjusted_median_rent: float | None
    confidence: str
    confidence_cap: str | None
    review_reasons: list[str]
    adjustment_summary: dict[str, int]
    adjusted_median_used: bool
    adjusted_median_not_used_reason: str | None
    market_estimate_source: str

    def facts(self, *, max_items: int = MAX_ADJUSTED_COMP_FACT_ITEMS) -> dict[str, Any]:
        return adjusted_comparable_facts(self, max_items=max_items)


@dataclass(frozen=True)
class MarketRentEstimate:
    monthly_rent: float | None
    rent_per_m2: float | None
    comp_count: int
    usable_comp_count: int
    confidence: float | None
    item_ids: list[int]
    content_hashes: list[str]
    source_urls: list[str]
    risk_flags: list[str]


def select_comparable_candidates(
    target_context: ComparableTargetContext,
    evidence_candidates: list[MarketCompInput],
    *,
    as_of: datetime,
    candidate_limit: int = COMPARABLE_SELECTION_DEFAULT_CANDIDATE_LIMIT,
    selected_limit: int = DEFAULT_MAX_COMPS,
    max_rejected_facts: int = COMPARABLE_SELECTION_MAX_REJECTED_FACTS,
    max_age_days: int = COMPARABLE_SELECTION_MAX_EVIDENCE_AGE_DAYS,
) -> ComparableSelectionResult:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of_utc = as_of.astimezone(UTC)
    safe_candidate_limit = max(
        0, min(int(candidate_limit), COMPARABLE_SELECTION_MAX_CANDIDATES)
    )
    safe_selected_limit = max(
        0, min(int(selected_limit), COMPARABLE_SELECTION_MAX_CANDIDATES)
    )
    considered = sorted(
        evidence_candidates,
        key=lambda c: (-c.confidence, -c.checked_at.timestamp(), c.id),
    )[:safe_candidate_limit]
    decisions: list[ComparableSelectionDecision] = []
    selected: list[MarketCompInput] = []
    for item in considered:
        decision = _selection_decision(
            target_context, item, as_of_utc, max_age_days=max_age_days
        )
        if (
            decision.selection_status == "selected"
            and len(selected) < safe_selected_limit
        ):
            selected.append(item)
            decisions.append(decision)
        elif decision.selection_status == "selected":
            decisions.append(
                ComparableSelectionDecision(
                    item.id,
                    item.listing_external_id,
                    "rejected",
                    "hard_gate",
                    rejection_reason="candidate_limit_exceeded",
                    selection_flags=["selected_limit_reached"],
                )
            )
        else:
            decisions.append(decision)
    review: list[str] = []
    if len(selected) < safe_selected_limit:
        review.append("insufficient_selected_comparable_evidence")
    return ComparableSelectionResult(
        COMPARABLE_SELECTION_POLICY_VERSION,
        as_of_utc,
        target_context,
        safe_candidate_limit,
        safe_selected_limit,
        max(0, int(max_rejected_facts)),
        decisions,
        selected,
        review,
        len(evidence_candidates) > safe_candidate_limit,
    )


def _selection_decision(
    target: ComparableTargetContext,
    item: MarketCompInput,
    as_of: datetime,
    *,
    max_age_days: int,
) -> ComparableSelectionDecision:
    base = {
        "evidence_id": item.id,
        "source_listing_external_id": item.listing_external_id,
        "selection_status": "rejected",
        "selection_stage": "hard_gate",
    }
    same_listing = bool(
        target.target_listing_external_id
        and item.listing_external_id == target.target_listing_external_id
    )
    if (
        not item.content_hash
        and not item.source_url_normalized
        and not item.listing_external_id
        and item.id is None
    ):
        return ComparableSelectionDecision(
            **base, rejection_reason="missing_source_trace"
        )
    if item.asset_type != target.asset_type:
        return ComparableSelectionDecision(
            **base, rejection_reason="asset_type_mismatch"
        )
    if item.deal_type != target.deal_type:
        return ComparableSelectionDecision(
            **base, rejection_reason="deal_type_mismatch"
        )
    if (
        target.deal_type == "rent"
        and item.rent_per_m2_rub is None
        and item.rent_rub_per_month is None
    ):
        return ComparableSelectionDecision(
            **base, rejection_reason="missing_rent_metric"
        )
    if (as_of - item.checked_at.astimezone(UTC)).days > max_age_days:
        return ComparableSelectionDecision(**base, rejection_reason="stale_evidence")
    matched = ["asset_type", "deal_type"]
    if same_listing:
        matched.append("same_listing")
        return ComparableSelectionDecision(
            item.id,
            item.listing_external_id,
            "selected",
            "hard_gate",
            selection_reason="same_listing_direct_evidence",
            selection_flags=["source_trace_present"],
            matched_on=matched,
        )
    if not target.location_key or not item.location_key:
        return ComparableSelectionDecision(
            **base, rejection_reason="insufficient_match_data"
        )
    if item.location_key != target.location_key:
        return ComparableSelectionDecision(
            **base, rejection_reason="location_key_mismatch"
        )
    matched.append("location_key")
    if target.area_m2 is None:
        return ComparableSelectionDecision(
            item.id,
            item.listing_external_id,
            "selected",
            "hard_gate",
            selection_reason="same_location_key_reuse",
            selection_flags=["source_trace_present", "target_area_unavailable"],
            matched_on=matched,
        )
    if item.area_m2 is None:
        return ComparableSelectionDecision(
            **base, rejection_reason="insufficient_match_data"
        )
    rel = abs(item.area_m2 - target.area_m2) / max(item.area_m2, target.area_m2)
    if rel > COMPARABLE_SELECTION_AREA_TOLERANCE_PCT:
        return ComparableSelectionDecision(
            **base, rejection_reason="area_band_mismatch"
        )
    matched.append("area_band")
    return ComparableSelectionDecision(
        item.id,
        item.listing_external_id,
        "selected",
        "hard_gate",
        selection_reason="same_location_key_reuse",
        selection_flags=["source_trace_present"],
        matched_on=matched,
    )


def comparable_selection_facts(
    result: ComparableSelectionResult,
    quality_assessment: ComparableQualityAssessment | None = None,
) -> dict[str, Any]:
    quality_by_id = (
        {r.evidence_id: r for r in quality_assessment.results}
        if quality_assessment is not None
        else {}
    )
    selected = []
    rejected = []
    for d in result.decisions:
        row = {
            "evidence_id": d.evidence_id,
            "source_listing_external_id": d.source_listing_external_id,
            "selection_stage": d.selection_stage,
        }
        if d.selection_status == "selected":
            row.update(
                {"selection_reason": d.selection_reason, "matched_on": d.matched_on}
            )
            if d.evidence_id in quality_by_id:
                row["quality_bucket"] = quality_by_id[d.evidence_id].quality_bucket
            selected.append(row)
        elif len(rejected) < result.max_rejected_facts:
            row["rejection_reason"] = d.rejection_reason
            rejected.append(row)
    return {
        "version": result.policy_version,
        "as_of": result.as_of.isoformat(),
        "target_context": {
            "profile": result.target_context.profile,
            "estimate_purpose": result.target_context.estimate_purpose,
            "asset_type": result.target_context.asset_type,
            "deal_type": result.target_context.deal_type,
            "location_key": result.target_context.location_key,
            "area_m2": result.target_context.area_m2,
        },
        "candidate_limit": result.candidate_limit,
        "selected_limit": result.selected_limit,
        "max_rejected_facts": result.max_rejected_facts,
        "candidate_count_considered": len(result.decisions),
        "selected_count": len(selected),
        "rejected_count": sum(
            1 for d in result.decisions if d.selection_status == "rejected"
        ),
        "truncated_candidates": result.truncated_candidates,
        "truncated_rejected_facts": sum(
            1 for d in result.decisions if d.selection_status == "rejected"
        )
        > result.max_rejected_facts,
        "selected": selected,
        "rejected": rejected,
        "review_reasons": result.review_reasons,
    }


def resolve_market_evidence_config(
    config: AnalysisConfig,
) -> ResolvedMarketEvidenceConfig:
    return ResolvedMarketEvidenceConfig(
        min_confidence=float(
            config.market_evidence_min_confidence
            if config.market_evidence_min_confidence is not None
            else DEFAULT_MIN_CONFIDENCE
        ),
        min_comps=int(
            config.market_evidence_min_comps
            if config.market_evidence_min_comps is not None
            else DEFAULT_MIN_COMPS
        ),
        max_comps=int(
            config.market_evidence_max_comps
            if config.market_evidence_max_comps is not None
            else DEFAULT_MAX_COMPS
        ),
        max_age_days=int(
            config.market_evidence_max_age_days
            if config.market_evidence_max_age_days is not None
            else DEFAULT_MAX_AGE_DAYS
        ),
        location_key=config.market_evidence_location_key,
        rent_strategy=config.market_evidence_rent_strategy or DEFAULT_STRATEGY,
        manual_mismatch_threshold_pct=float(
            config.market_evidence_manual_mismatch_threshold_pct
            if config.market_evidence_manual_mismatch_threshold_pct is not None
            else DEFAULT_MISMATCH_THRESHOLD
        ),
        matching_policy=(
            config.market_evidence_matching_policy
            or MARKET_EVIDENCE_POLICY_SAME_LISTING
        ),
    )


def select_market_evidence(
    *,
    candidates: list[MarketEvidenceItem],
    config: AnalysisConfig,
    expected_asset_type: str,
    evidence_retrieval_as_of_datetime: datetime,
    evidence_retrieval_as_of_date: date,
    target_listing_external_id: str | None = None,
    target_area_m2: float | None = None,
    profile: str = "investment",
) -> SelectedMarketEvidenceContext:
    if evidence_retrieval_as_of_datetime.tzinfo is None:
        raise ValueError("evidence_retrieval_as_of_datetime must be timezone-aware")
    resolved = resolve_market_evidence_config(config)
    if resolved.matching_policy not in ALLOWED_MARKET_EVIDENCE_POLICIES:
        return SelectedMarketEvidenceContext(
            items=[],
            excluded_counts_by_reason={"invalid_matching_policy": len(candidates)},
            limitations=["market_evidence_matching_policy_invalid"],
            retrieval_as_of_datetime=evidence_retrieval_as_of_datetime,
            retrieval_as_of_date=evidence_retrieval_as_of_date,
            config=resolved,
            target_listing_external_id=target_listing_external_id,
        )
    if (
        resolved.matching_policy == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
        and not resolved.location_key
    ):
        return SelectedMarketEvidenceContext(
            items=[],
            excluded_counts_by_reason={"missing_location_key": len(candidates)},
            limitations=["market_evidence_location_key_missing"],
            retrieval_as_of_datetime=evidence_retrieval_as_of_datetime,
            retrieval_as_of_date=evidence_retrieval_as_of_date,
            config=ResolvedMarketEvidenceConfig(
                **{**resolved.__dict__, "min_comps": max(resolved.min_comps, 3)}
            ),
            target_listing_external_id=target_listing_external_id,
        )
    if resolved.matching_policy == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY:
        resolved = ResolvedMarketEvidenceConfig(
            **{**resolved.__dict__, "min_comps": max(resolved.min_comps, 3)}
        )
    cutoff = evidence_retrieval_as_of_datetime - timedelta(days=resolved.max_age_days)
    excluded: dict[str, int] = {}
    selected: list[MarketCompInput] = []
    for item in candidates:
        reason = _exclusion_reason(
            item,
            resolved,
            expected_asset_type,
            cutoff,
            evidence_retrieval_as_of_datetime,
            target_listing_external_id,
        )
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        selected.append(_to_comp(item))
    target = ComparableTargetContext(
        target_listing_id=None,
        target_listing_external_id=target_listing_external_id
        or _single_listing_external_id(selected),
        profile=profile,
        estimate_purpose="rent_estimate",
        asset_type=expected_asset_type,
        deal_type="rent",
        location_key=resolved.location_key
        if resolved.matching_policy == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
        else None,
        area_m2=target_area_m2,
    )
    selection = select_comparable_candidates(
        target,
        selected,
        as_of=evidence_retrieval_as_of_datetime,
        candidate_limit=COMPARABLE_SELECTION_DEFAULT_CANDIDATE_LIMIT,
        selected_limit=resolved.max_comps,
        max_age_days=resolved.max_age_days,
    )
    return SelectedMarketEvidenceContext(
        items=selection.selected_items,
        excluded_counts_by_reason=excluded,
        limitations=[],
        retrieval_as_of_datetime=evidence_retrieval_as_of_datetime,
        retrieval_as_of_date=evidence_retrieval_as_of_date,
        config=resolved,
        target_listing_external_id=target_listing_external_id,
        selection_result=selection,
    )


def _exclusion_reason(
    item: MarketEvidenceItem,
    cfg: ResolvedMarketEvidenceConfig,
    asset: str,
    cutoff: datetime,
    as_of: datetime,
    target_listing_external_id: str | None,
) -> str | None:
    if (
        cfg.matching_policy == MARKET_EVIDENCE_POLICY_SAME_LISTING
        and target_listing_external_id is not None
        and item.listing_external_id != target_listing_external_id
    ):
        return "wrong_listing_external_id"
    if (
        cfg.matching_policy == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
        and item.location_key != cfg.location_key
    ):
        return "wrong_location_key"
    if item.evidence_type != "comparable_candidate":
        return "wrong_evidence_type"
    if item.is_reusable is not True:
        return "not_reusable"
    if item.checked_at is None or _aware(item.checked_at) < cutoff:
        return "too_old"
    if item.expires_at is not None and _aware(item.expires_at) < as_of:
        return "expired"
    if item.confidence is None or item.confidence < cfg.min_confidence:
        return "low_confidence"
    if item.asset_type != asset:
        return "wrong_asset_type"
    if item.deal_type != "rent":
        return "wrong_deal_type"
    if (
        cfg.matching_policy == MARKET_EVIDENCE_POLICY_SAME_LISTING
        and cfg.location_key is not None
        and item.location_key != cfg.location_key
    ):
        return "wrong_location_key"
    if not (item.source_url_normalized or item.source_url):
        return "missing_source"
    if item.rent_per_m2_rub is None and item.rent_rub_per_month is None:
        return "missing_rent_metric"
    return None


def _to_comp(item: MarketEvidenceItem) -> MarketCompInput:
    return MarketCompInput(
        id=int(item.id),
        listing_external_id=item.listing_external_id,
        content_hash=item.content_hash,
        confidence=float(item.confidence or 0),
        checked_at=_aware(item.checked_at),
        expires_at=_aware(item.expires_at) if item.expires_at else None,
        source_url_normalized=(
            item.source_url_normalized or item.source_url or ""
        ).strip(),
        asset_type=item.asset_type or "",
        deal_type=item.deal_type or "",
        location_key=item.location_key,
        rent_per_m2_rub=float(item.rent_per_m2_rub)
        if item.rent_per_m2_rub is not None
        else None,
        rent_rub_per_month=float(item.rent_rub_per_month)
        if item.rent_rub_per_month is not None
        else None,
        area_m2=float(item.area_m2) if item.area_m2 is not None else None,
        rent_period=(item.evidence_json or {}).get("rent_period", "month"),
        source_type=(item.evidence_json or {}).get("source_type"),
        condition=(item.evidence_json or {}).get("condition"),
        capex_required=(item.evidence_json or {}).get("capex_required"),
        first_line=(item.evidence_json or {}).get("first_line"),
        floor_access=(item.evidence_json or {}).get("floor_access"),
        verification_status=(item.evidence_json or {}).get("verification_status"),
        human_verified=(item.evidence_json or {}).get("human_verified"),
        verified_by_human=(item.evidence_json or {}).get("verified_by_human"),
        reviewed_by_human=(item.evidence_json or {}).get("reviewed_by_human"),
        source_verified=(item.evidence_json or {}).get("source_verified"),
        source_origin=(item.evidence_json or {}).get("source_origin"),
        published_at=_parse_source_datetime(item.source_published_at)
        or _parse_source_datetime((item.evidence_json or {}).get("published_at")),
    )



def assess_source_quality(
    *,
    target_context: ComparableTargetContext,
    selected_comps: list[MarketCompInput],
    selection_result: ComparableSelectionResult | None,
    quality_result: ComparableQualityAssessment | None,
    as_of: datetime,
    config: SourceQualityConfig | None = None,
) -> SourceQualityAssessment:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    cfg = config or SourceQualityConfig()
    as_of_utc = as_of.astimezone(UTC)
    selected_ids = (
        {d.evidence_id for d in selection_result.decisions if d.selection_status == "selected"}
        if selection_result is not None
        else {i.id for i in selected_comps}
    )
    allowed_quality_ids = (
        {r.evidence_id for r in quality_result.results if r.accepted}
        if quality_result is not None
        else {i.id for i in selected_comps}
    )
    items: list[SourceQualityItem] = []
    review: list[str] = []
    for comp in sorted(selected_comps, key=lambda i: (-1 if i.id is None else i.id, i.listing_external_id or "")):
        if comp.id not in selected_ids or comp.id not in allowed_quality_ids:
            continue
        flags: list[str] = []
        reasons: list[str] = []
        source_type = _source_quality_type(comp.source_type, flags, reasons)
        verification_status = _source_quality_verification(comp, flags)
        trace_strength = _source_trace_strength(comp)
        freshness_bucket, age_days, freshness_reasons = _source_freshness(comp, as_of_utc, cfg)
        reasons.extend(freshness_reasons)
        if trace_strength in {"weak", "missing"}:
            reasons.append(f"{trace_strength}_source_trace")
        if source_type == "unknown":
            review.append("source_type_unknown")
        if freshness_reasons:
            review.extend(freshness_reasons)
        if trace_strength == "missing":
            review.append("missing_source_trace")
        cap = _source_item_cap(trace_strength, source_type, freshness_bucket, comp.expires_at, as_of_utc)
        bucket = _source_confidence_bucket(cap)
        items.append(SourceQualityItem(
            comp.id,
            comp.listing_external_id,
            source_type,
            verification_status,
            trace_strength,
            freshness_bucket,
            comp.source_origin,
            bucket,
            cap,
            list(dict.fromkeys(flags)),
            list(dict.fromkeys(reasons)),
        ))
    assessed = len(items)
    weak_missing = sum(1 for i in items if i.trace_strength in {"weak", "missing"})
    unknown_type = sum(1 for i in items if i.source_type == "unknown")
    verified = sum(1 for i in items if i.verification_status in {"verified", "human_verified"})
    stale_expired = sum(1 for i in items if i.freshness_bucket == "stale" or "expired_source" in i.source_quality_reasons)
    if assessed:
        strong_medium_share = (assessed - weak_missing) / assessed
        verified_or_known_share = sum(1 for i in items if i.verification_status in {"verified", "human_verified"} or i.source_type != "unknown") / assessed
        if strong_medium_share < cfg.min_strong_or_medium_trace_share:
            review.append("weak_or_missing_source_trace_share")
        if verified_or_known_share < cfg.min_verified_or_known_source_type_share:
            review.append("low_verified_or_known_source_type_share")
    cap_values = [i.confidence_cap for i in items if i.confidence_cap is not None]
    evidence_cap = min(cap_values) if cap_values else None
    if evidence_cap is not None:
        review.append("source_quality_confidence_cap_applied")
    bucket = "high" if evidence_cap is None and unknown_type == 0 and weak_missing == 0 and stale_expired == 0 else "medium"
    if evidence_cap == SOURCE_QUALITY_CAP_WEAK:
        bucket = "weak"
    if evidence_cap == SOURCE_QUALITY_CAP_INDICATIVE:
        bucket = "indicative"
    return SourceQualityAssessment(
        SOURCE_QUALITY_MODEL_VERSION,
        SOURCE_QUALITY_CONFIG_VERSION,
        as_of_utc,
        target_context,
        assessed,
        weak_missing,
        unknown_type,
        verified,
        stale_expired,
        evidence_cap,
        bucket,
        list(dict.fromkeys(review)),
        items,
    )


def _source_quality_type(value: str | None, flags: list[str], reasons: list[str]) -> str:
    if value is None or str(value).strip() == "":
        flags.append("source_type_unknown")
        return "unknown"
    normalized = str(value).strip().lower()
    if normalized in ALLOWED_SOURCE_TYPES and normalized != "unknown":
        flags.append("source_type_explicit")
        return normalized
    if normalized == "unknown":
        flags.append("source_type_unknown")
        return "unknown"
    flags.append("source_type_unknown")
    reasons.append("source_type_untrusted_value")
    return "unknown"


def _source_quality_verification(comp: MarketCompInput, flags: list[str]) -> str:
    raw = comp.verification_status
    if raw is not None and str(raw).strip().lower() in ALLOWED_VERIFICATION_STATUSES:
        status = str(raw).strip().lower()
    elif comp.human_verified is True or comp.verified_by_human is True or comp.reviewed_by_human is True:
        status = "human_verified"
    elif comp.source_verified is True:
        status = "verified"
    elif comp.source_verified is False or comp.human_verified is False:
        status = "unverified"
    else:
        status = "unknown"
    if status in {"verified", "human_verified"}:
        flags.append(status)
    return status


def _source_trace_strength(comp: MarketCompInput) -> str:
    has_url = bool(comp.source_url_normalized)
    has_hash = bool(comp.content_hash)
    if has_url and has_hash:
        return "strong"
    if has_url or has_hash:
        return "medium"
    if comp.listing_external_id or comp.id is not None:
        return "weak"
    return "missing"


def _source_freshness(comp: MarketCompInput, as_of: datetime, cfg: SourceQualityConfig) -> tuple[str, int | None, list[str]]:
    if comp.expires_at is not None and comp.expires_at.astimezone(UTC) < as_of:
        return "stale", None, ["expired_source"]
    ts = comp.checked_at or comp.published_at
    if ts is None:
        return "unknown", None, ["freshness_unknown"]
    age = (as_of - ts.astimezone(UTC)).days
    if age > cfg.stale_days:
        return "stale", age, ["stale_source"]
    if age > cfg.aging_days:
        return "aging", age, []
    return "fresh", age, []


def _source_item_cap(trace_strength: str, source_type: str, freshness_bucket: str, expires_at: datetime | None, as_of: datetime) -> float | None:
    caps: list[float] = []
    if trace_strength == "missing":
        caps.append(SOURCE_QUALITY_CAP_INDICATIVE)
    elif trace_strength == "weak":
        caps.append(SOURCE_QUALITY_CAP_WEAK)
    if source_type == "unknown":
        caps.append(SOURCE_QUALITY_CAP_WEAK)
    if freshness_bucket == "stale" or (expires_at is not None and expires_at.astimezone(UTC) < as_of):
        caps.append(SOURCE_QUALITY_CAP_WEAK)
    return min(caps) if caps else None


def _source_confidence_bucket(cap: float | None) -> str:
    if cap is None:
        return "high"
    if cap <= SOURCE_QUALITY_CAP_INDICATIVE:
        return "indicative"
    return "weak"


def source_quality_facts(result: SourceQualityAssessment, *, max_items: int = MAX_SOURCE_QUALITY_FACT_ITEMS) -> dict[str, Any]:
    capped = result.items[: max(0, max_items)]
    return {
        "version": result.source_quality_model_version,
        "config_version": result.source_quality_config_version,
        "as_of": result.as_of.isoformat(),
        "summary": {
            "assessed_count": result.assessed_count,
            "source_quality_bucket": result.source_quality_bucket,
            "evidence_confidence_cap": result.evidence_confidence_cap,
            "unknown_source_type_count": result.unknown_source_type_count,
            "weak_or_missing_trace_count": result.weak_or_missing_trace_count,
            "verified_count": result.verified_count,
            "stale_or_expired_count": result.stale_or_expired_count,
        },
        "items": [i.__dict__ for i in capped],
        "truncated_items": len(result.items) > max_items,
        "review_reasons": result.review_reasons,
    }


def combine_confidence_caps(*caps: float | None) -> float | None:
    values = [c for c in caps if c is not None]
    return min(values) if values else None


def source_quality_fingerprint(assessment: SourceQualityAssessment | None) -> dict[str, Any] | None:
    if assessment is None:
        return None
    return {
        "source_quality_model_version": assessment.source_quality_model_version,
        "source_quality_config_version": assessment.source_quality_config_version,
        "as_of": assessment.as_of.isoformat(),
        "constants": {
            "stale_days": SOURCE_QUALITY_STALE_DAYS,
            "aging_days": SOURCE_QUALITY_AGING_DAYS,
            "cap_weak": SOURCE_QUALITY_CAP_WEAK,
            "cap_indicative": SOURCE_QUALITY_CAP_INDICATIVE,
        },
        "items": [
            {
                "evidence_id": i.evidence_id,
                "source_listing_external_id": i.source_listing_external_id,
                "source_type": i.source_type,
                "verification_status": i.verification_status,
                "trace_strength": i.trace_strength,
                "freshness_bucket": i.freshness_bucket,
                "source_origin": i.source_origin,
                "confidence_cap": i.confidence_cap,
                "reasons": i.source_quality_reasons,
            }
            for i in sorted(assessment.items, key=lambda x: (-1 if x.evidence_id is None else x.evidence_id, x.source_listing_external_id or ""))
        ],
    }


def _safe_url_fingerprint(value: str) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    safe = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return hashlib.sha256(safe.encode("utf-8")).hexdigest()


def _parse_source_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

def adjust_comparable_rents(
    *,
    target_context: ComparableTargetContext,
    selected_comps: list[MarketCompInput],
    quality_result: ComparableQualityAssessment | None,
    selection_result: ComparableSelectionResult | None,
    as_of: datetime,
    config: AdjustedComparableConfig | None = None,
    manual_rent: float | None = None,
) -> AdjustedComparableResult:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    cfg = config or AdjustedComparableConfig()
    as_of_utc = as_of.astimezone(UTC)
    selected_ids = (
        {
            d.evidence_id
            for d in selection_result.decisions
            if d.selection_status == "selected"
        }
        if selection_result
        else {i.id for i in selected_comps}
    )
    selection_reason = (
        {
            d.evidence_id: d.selection_reason
            for d in selection_result.decisions
            if d.selection_status == "selected"
        }
        if selection_result
        else {}
    )
    quality_by_id = (
        {r.evidence_id: r for r in quality_result.results} if quality_result else {}
    )
    allowed_quality_ids = (
        {r.evidence_id for r in quality_result.results if r.accepted}
        if quality_result
        else {i.id for i in selected_comps}
    )
    items: list[AdjustedComparableItem] = []
    excluded = 0
    review: list[str] = []
    summary: dict[str, int] = {}
    for comp in sorted(
        selected_comps, key=lambda i: (i.id, i.listing_external_id or "")
    ):
        if comp.id not in selected_ids or comp.id not in allowed_quality_ids:
            excluded += 1
            continue
        raw_m2 = comp.rent_per_m2_rub
        if (
            raw_m2 is None
            and comp.rent_rub_per_month is not None
            and comp.area_m2
            and comp.area_m2 > 0
        ):
            raw_m2 = comp.rent_rub_per_month / comp.area_m2
        if comp.rent_period not in (None, "month", "monthly"):
            excluded += 1
            review.append("insufficient_rent_metric")
            continue
        if raw_m2 is None or raw_m2 <= 0:
            excluded += 1
            review.append("missing_rent_metric")
            continue
        flags: list[str] = []
        reasons: list[str] = []
        deltas: list[float] = []
        cap_applied = False

        def add_delta(reason: str, delta: float):
            nonlocal cap_applied
            capped = _cap_pct(delta, cfg.max_single_adjustment_abs_pct)
            capped = (
                _cap_pct(capped, cfg.area_adjustment_max_abs_pct)
                if reason == REASON_AREA_ADJUSTMENT
                else capped
            )
            if capped != delta:
                cap_applied = True
                flags.append(f"{reason}_cap_applied")
            deltas.append(capped)
            reasons.append(reason)
            summary[reason] = summary.get(reason, 0) + 1

        if target_context.area_m2 is None:
            flags.append("area_adjustment_skipped_target_area_unknown")
        elif not comp.area_m2:
            flags.append("area_adjustment_skipped_comp_area_unknown")
        else:
            rel = (target_context.area_m2 - comp.area_m2) / max(
                target_context.area_m2, comp.area_m2
            )
            if rel:
                add_delta(REASON_AREA_ADJUSTMENT, rel * 0.20)
                flags.append("area_adjustment_applied")
        _boolean_advantage_adjust(
            target_context.first_line,
            comp.first_line,
            cfg.first_line_adjustment_pct,
            REASON_FIRST_LINE_ADJUSTMENT,
            "first_line",
            add_delta,
            flags,
        )
        _ordered_adjust(
            target_context.condition,
            comp.condition,
            cfg.condition_capex_adjustment_pct,
            REASON_CONDITION_ADJUSTMENT,
            "condition",
            add_delta,
            flags,
        )
        if target_context.capex_required is not None or comp.capex_required is not None:
            flags.append("capex_signal_present")
            _boolean_advantage_adjust(
                False
                if target_context.capex_required
                else True
                if target_context.capex_required is not None
                else None,
                False
                if comp.capex_required
                else True
                if comp.capex_required is not None
                else None,
                cfg.condition_capex_adjustment_pct,
                REASON_CONDITION_ADJUSTMENT,
                "capex",
                add_delta,
                flags,
            )
        _ordered_adjust(
            target_context.floor_access,
            comp.floor_access,
            cfg.floor_access_adjustment_pct,
            REASON_FLOOR_ACCESS_ADJUSTMENT,
            "floor_access",
            add_delta,
            flags,
        )
        if comp.source_type == "asking":
            add_delta(
                REASON_ASKING_TO_EFFECTIVE_DISCOUNT,
                -cfg.asking_to_effective_discount_pct,
            )
            flags.append("asking_to_effective_discount_applied")
        elif comp.source_type in ("confirmed", "effective"):
            flags.append("source_type_confirmed_no_discount")
        else:
            flags.append("source_type_unknown")
            review.append("source_type_unknown")
        age_days = (
            (as_of_utc - comp.checked_at.astimezone(UTC)).days
            if comp.checked_at
            else None
        )
        if age_days is None:
            flags.append("freshness_unknown")
            review.append("freshness_unknown")
        elif age_days > cfg.stale_days:
            flags.extend(["stale_comp", "freshness_confidence_penalty_applied"])
            review.append("stale_comp")
        total = sum(deltas)
        capped_total = _cap_pct(total, cfg.max_adjustment_abs_pct)
        if capped_total != total:
            cap_applied = True
            flags.append("total_adjustment_cap_applied")
        adj_m2 = round(raw_m2 * (1 + capped_total), 2)
        adj_rent = (
            round(adj_m2 * target_context.area_m2, 2)
            if target_context.area_m2
            else None
        )
        q = quality_by_id.get(comp.id)
        if q and q.quality_bucket == "low":
            review.append("low_quality_comps_in_adjusted_set")
        items.append(
            AdjustedComparableItem(
                comp.id,
                comp.listing_external_id,
                comp.rent_rub_per_month,
                comp.rent_period,
                round(raw_m2, 2),
                adj_rent,
                adj_m2,
                round(capped_total, 4),
                list(dict.fromkeys(reasons)),
                list(dict.fromkeys(flags)),
                cap_applied,
                q.quality_bucket if q else None,
                selection_reason.get(comp.id),
                comp.source_url_normalized or comp.content_hash,
            )
        )
    raw_vals = [i.raw_rent_per_m2 for i in items]
    adj_vals = [i.adjusted_rent_per_m2 for i in items]
    raw_med_m2 = round(float(median(raw_vals)), 2) if raw_vals else None
    adj_med_m2 = round(float(median(adj_vals)), 2) if adj_vals else None
    raw_med = (
        round(raw_med_m2 * target_context.area_m2, 2)
        if raw_med_m2 is not None and target_context.area_m2
        else None
    )
    adj_med = (
        round(adj_med_m2 * target_context.area_m2, 2)
        if adj_med_m2 is not None and target_context.area_m2
        else None
    )
    high_medium = sum(1 for i in items if i.quality_bucket in ("high", "medium"))
    share = high_medium / len(items) if items else 0
    confidence = (
        "medium"
        if len(items) >= cfg.min_adjusted_comp_count_for_base_confidence
        and share >= cfg.min_high_or_medium_quality_share
        else "low"
    )
    cap = None if confidence == "medium" else FRESHNESS_CONFIDENCE_PENALTY
    if len(items) < cfg.min_adjusted_comp_count_for_base_confidence:
        review.append("insufficient_adjusted_comps")
    if share < cfg.min_high_or_medium_quality_share:
        review.append("insufficient_high_or_medium_quality_share")
    if target_context.area_m2 is None:
        review.append("target_area_unknown")
    can_use = manual_rent is None and confidence != "low" and adj_med is not None
    not_used = (
        None
        if can_use
        else (
            "manual_rent_primary"
            if manual_rent is not None
            else "insufficient_adjusted_market_evidence"
        )
    )
    source = (
        "adjusted_market_comps"
        if can_use
        else ("manual_rent" if manual_rent is not None else "raw_market_comps")
    )
    return AdjustedComparableResult(
        ADJUSTED_COMPARABLE_MODEL_VERSION,
        ADJUSTED_COMPARABLE_CONFIG_VERSION,
        as_of_utc,
        target_context,
        len(selected_comps),
        len(items),
        excluded,
        items,
        raw_med_m2,
        adj_med_m2,
        raw_med,
        adj_med,
        confidence,
        cap,
        list(dict.fromkeys(review)),
        summary,
        can_use,
        not_used,
        source,
    )


def _cap_pct(value: float, max_abs: float) -> float:
    return max(-max_abs, min(max_abs, value))


def _boolean_advantage_adjust(
    target: bool | None,
    comp: bool | None,
    pct: float,
    reason: str,
    flag_prefix: str,
    add_delta,
    flags: list[str],
) -> None:
    if target is None or comp is None:
        flags.append(f"{flag_prefix}_unknown")
        return
    if target == comp:
        return
    add_delta(reason, pct if target and not comp else -pct)
    flags.append(f"{flag_prefix}_adjustment_applied")


def _ordered_adjust(
    target: str | None,
    comp: str | None,
    pct: float,
    reason: str,
    flag_prefix: str,
    add_delta,
    flags: list[str],
) -> None:
    order = {
        "poor": 0,
        "needs_capex": 0,
        "average": 1,
        "good": 2,
        "excellent": 3,
        "basement": 0,
        "bad": 0,
        "standard": 1,
        "ground": 2,
        "street": 2,
    }
    if target is None or comp is None:
        flags.append(f"{flag_prefix}_unknown")
        return
    if target not in order or comp not in order:
        flags.append(f"{flag_prefix}_direction_unknown")
        return
    if order[target] == order[comp]:
        return
    add_delta(reason, pct if order[target] > order[comp] else -pct)
    flags.append(f"{flag_prefix}_adjustment_applied")


def adjusted_comparable_facts(
    result: AdjustedComparableResult, *, max_items: int = MAX_ADJUSTED_COMP_FACT_ITEMS
) -> dict[str, Any]:
    return {
        "version": result.version,
        "config_version": result.config_version,
        "as_of": result.as_of.isoformat(),
        "target_context": {
            "asset_type": result.target_context.asset_type,
            "deal_type": result.target_context.deal_type,
            "location_key": result.target_context.location_key,
            "area_m2": result.target_context.area_m2,
        },
        "summary": {
            "selected_count": result.selected_count,
            "adjusted_count": result.adjusted_count,
            "excluded_from_adjusted_count": result.excluded_from_adjusted_count,
            "raw_median_rent_per_m2": result.raw_median_rent_per_m2,
            "adjusted_median_rent_per_m2": result.adjusted_median_rent_per_m2,
            "raw_median_rent": result.raw_median_rent,
            "adjusted_median_rent": result.adjusted_median_rent,
            "confidence": result.confidence,
            "confidence_cap": result.confidence_cap,
            "adjusted_median_used": result.adjusted_median_used,
            "adjusted_median_not_used_reason": result.adjusted_median_not_used_reason,
            "market_estimate_source": result.market_estimate_source,
        },
        "adjustment_summary": result.adjustment_summary,
        "items": [i.__dict__ for i in result.items[: max(0, max_items)]],
        "truncated_items": len(result.items) > max_items,
        "review_reasons": result.review_reasons,
    }


def estimate_market_rent(
    *,
    context: SelectedMarketEvidenceContext,
    area_m2: float | None,
    quality_assessment: ComparableQualityAssessment | None = None,
    source_quality_assessment: SourceQualityAssessment | None = None,
) -> MarketRentEstimate:
    flags: list[str] = []
    if context.config.rent_strategy != "median":
        flags.append("unsupported_market_rent_strategy")
        return _estimate_none(context, flags)
    items = (
        [i for i in context.items if i.id in quality_assessment.accepted_item_ids]
        if quality_assessment is not None
        else context.items
    )
    rent_m2_items = [i for i in items if i.rent_per_m2_rub is not None]
    monthly_items = [i for i in items if i.rent_rub_per_month is not None]
    used: list[MarketCompInput] = []
    monthly = rent_m2 = None
    if area_m2 is not None and area_m2 > 0 and rent_m2_items:
        used = rent_m2_items
        rent_m2 = round(
            float(
                median(
                    [i.rent_per_m2_rub for i in used if i.rent_per_m2_rub is not None]
                )
            ),
            2,
        )
        monthly = round(rent_m2 * area_m2, 2)
    elif monthly_items:
        used = monthly_items
        monthly = round(
            float(
                median(
                    [
                        i.rent_rub_per_month
                        for i in used
                        if i.rent_rub_per_month is not None
                    ]
                )
            ),
            2,
        )
    elif rent_m2_items:
        flags.append("missing_area_for_market_rent")
    conf = _confidence(used, context.config.min_comps) if used else None
    if (
        conf is not None
        and quality_assessment is not None
        and quality_assessment.summary.evidence_confidence_cap is not None
    ):
        conf = min(conf, quality_assessment.summary.evidence_confidence_cap)
    if (
        conf is not None
        and source_quality_assessment is not None
        and source_quality_assessment.evidence_confidence_cap is not None
    ):
        conf = min(conf, source_quality_assessment.evidence_confidence_cap)
    return MarketRentEstimate(
        monthly,
        rent_m2,
        len(items),
        len(used),
        conf,
        [i.id for i in used],
        [i.content_hash for i in used],
        [i.source_url_normalized for i in used],
        flags,
    )


def _estimate_none(
    context: SelectedMarketEvidenceContext, flags: list[str]
) -> MarketRentEstimate:
    return MarketRentEstimate(
        None, None, len(context.items), 0, None, [], [], [], flags
    )


def _confidence(items: list[MarketCompInput], min_comps: int) -> float:
    base = sum(i.confidence for i in items) / len(items)
    count = len(items)
    factor = 1.0 if count >= min_comps + 2 else 0.85 if count >= min_comps else 0.5
    return round(min(base, factor), 4)


def market_evidence_fingerprint(
    context: SelectedMarketEvidenceContext,
) -> dict[str, Any]:
    return {
        "evidence_retrieval_as_of_date": context.retrieval_as_of_date.isoformat(),
        "config": context.config.__dict__,
        "items": [
            {
                "id": i.id,
                "listing_external_id": i.listing_external_id,
                "content_hash": i.content_hash,
                "confidence": i.confidence,
                "checked_at": i.checked_at.date().isoformat(),
                "expires_at": i.expires_at.date().isoformat() if i.expires_at else None,
                "source_url_hash": _safe_url_fingerprint(i.source_url_normalized),
                "asset_type": i.asset_type,
                "deal_type": i.deal_type,
                "location_key": i.location_key,
                "rent_per_m2_rub": i.rent_per_m2_rub,
                "rent_rub_per_month": i.rent_rub_per_month,
                "area_m2": i.area_m2,
                "rent_period": i.rent_period,
                "source_type": i.source_type,
                "condition": i.condition,
                "capex_required": i.capex_required,
                "first_line": i.first_line,
                "floor_access": i.floor_access,
                "verification_status": i.verification_status,
                "human_verified": i.human_verified,
                "verified_by_human": i.verified_by_human,
                "reviewed_by_human": i.reviewed_by_human,
                "source_verified": i.source_verified,
                "source_origin": i.source_origin,
                "published_at": i.published_at.isoformat() if i.published_at else None,
            }
            for i in sorted(context.items, key=lambda x: x.id)
        ],
        "comparable_selection_policy_version": COMPARABLE_SELECTION_POLICY_VERSION,
        "selection": comparable_selection_fingerprint(context.selection_result)
        if context.selection_result is not None
        else None,
        "comparable_quality_model_version": COMPARABLE_QUALITY_MODEL_VERSION,
        "quality_as_of_datetime": context.retrieval_as_of_datetime.isoformat(),
        "adjusted_comparable_model_version": ADJUSTED_COMPARABLE_MODEL_VERSION,
        "adjusted_comparable_config_version": ADJUSTED_COMPARABLE_CONFIG_VERSION,
        "source_quality_model_version": SOURCE_QUALITY_MODEL_VERSION,
        "source_quality_config_version": SOURCE_QUALITY_CONFIG_VERSION,
        "source_quality_constants": {
            "stale_days": SOURCE_QUALITY_STALE_DAYS,
            "aging_days": SOURCE_QUALITY_AGING_DAYS,
            "cap_weak": SOURCE_QUALITY_CAP_WEAK,
            "cap_indicative": SOURCE_QUALITY_CAP_INDICATIVE,
            "max_fact_items": MAX_SOURCE_QUALITY_FACT_ITEMS,
        },
        "source_quality_as_of_datetime": context.retrieval_as_of_datetime.isoformat(),
        "adjusted_comparable_constants": {
            "max_adjustment_abs_pct": MAX_ADJUSTMENT_ABS_PCT,
            "max_single_adjustment_abs_pct": MAX_SINGLE_ADJUSTMENT_ABS_PCT,
            "area_adjustment_max_abs_pct": AREA_ADJUSTMENT_MAX_ABS_PCT,
            "condition_capex_adjustment_pct": CONDITION_CAPEX_ADJUSTMENT_PCT,
            "first_line_adjustment_pct": FIRST_LINE_ADJUSTMENT_PCT,
            "floor_access_adjustment_pct": FLOOR_ACCESS_ADJUSTMENT_PCT,
            "asking_to_effective_discount_pct": ASKING_TO_EFFECTIVE_DISCOUNT_PCT,
            "stale_days": ADJUSTED_COMPARABLE_STALE_DAYS,
        },
    }


def comparable_selection_fingerprint(
    result: ComparableSelectionResult | None,
) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "version": result.policy_version,
        "as_of": result.as_of.isoformat(),
        "target_context": result.target_context.__dict__,
        "candidate_limit": result.candidate_limit,
        "selected_limit": result.selected_limit,
        "decisions": [
            {
                "evidence_id": d.evidence_id,
                "source_listing_external_id": d.source_listing_external_id,
                "selection_status": d.selection_status,
                "selection_stage": d.selection_stage,
                "selection_reason": d.selection_reason,
                "rejection_reason": d.rejection_reason,
                "matched_on": d.matched_on,
            }
            for d in sorted(result.decisions, key=lambda x: x.evidence_id)
        ],
    }


def assess_comparable_quality(
    *,
    context: SelectedMarketEvidenceContext,
    expected_asset_type: str,
    target_area_m2: float | None,
    target_location_key: str | None,
    as_of: datetime,
) -> ComparableQualityAssessment:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    results = [
        _score_comp(
            item=i,
            expected_asset_type=expected_asset_type,
            target_area_m2=target_area_m2,
            target_location_key=target_location_key,
            as_of=as_of.astimezone(UTC),
        )
        for i in context.items
    ]
    summary = _summarize_quality(results)
    return ComparableQualityAssessment(
        COMPARABLE_QUALITY_MODEL_VERSION, as_of.astimezone(UTC), results, summary
    )


def _score_comp(
    *,
    item: MarketCompInput,
    expected_asset_type: str,
    target_area_m2: float | None,
    target_location_key: str | None,
    as_of: datetime,
) -> ComparableQualityResult:
    if item.asset_type and item.asset_type != expected_asset_type:
        return _rejected(item.id, "asset_type_mismatch")
    if not item.asset_type:
        return _rejected(item.id, "insufficient_data")
    if item.deal_type and item.deal_type != "rent":
        return _rejected(item.id, "deal_type_mismatch")
    if not item.deal_type:
        return _rejected(item.id, "insufficient_data")
    if item.rent_per_m2_rub is None and item.rent_rub_per_month is None:
        return _rejected(item.id, "missing_rent_metric")
    score = 100
    similarity = 100
    flags = ["rent_metric_present"]
    if item.source_url_normalized:
        flags.append("source_url_present")
    else:
        score -= QUALITY_PENALTIES["missing_source_url"]
        flags.append("missing_source_url")
    age_days = (as_of - item.checked_at.astimezone(UTC)).days
    if age_days > QUALITY_MAX_AGE_DAYS:
        return _rejected(item.id, "stale_evidence")
    if age_days > QUALITY_STALE_DAYS:
        score -= QUALITY_PENALTIES["stale_evidence"]
        flags.append("stale_evidence")
    else:
        flags.append("fresh")
    if target_area_m2 and item.area_m2:
        rel = abs(item.area_m2 - target_area_m2) / max(item.area_m2, target_area_m2)
        if rel > 0.5:
            return _rejected(item.id, "area_band_mismatch")
        if rel > 0.25:
            score -= QUALITY_PENALTIES["area_band_mismatch"]
            similarity -= QUALITY_PENALTIES["area_band_mismatch"]
            flags.append("area_band_mismatch")
        else:
            flags.append("area_similar")
    else:
        score -= QUALITY_PENALTIES["area_unknown"]
        similarity -= QUALITY_PENALTIES["area_unknown"]
        flags.append("area_unknown")
    if target_location_key and item.location_key:
        if item.location_key != target_location_key:
            score -= QUALITY_PENALTIES["location_mismatch"]
            similarity -= QUALITY_PENALTIES["location_mismatch"]
            flags.append("location_mismatch")
        else:
            flags.append("location_match")
    else:
        score -= QUALITY_PENALTIES["location_unknown"]
        similarity -= QUALITY_PENALTIES["location_unknown"]
        flags.append("location_unknown")
    return _penalized_result(item.id, score, similarity, flags)


def _penalized_result(
    evidence_id: int, score: int, similarity: int, flags: list[str]
) -> ComparableQualityResult:
    score = max(0, min(100, int(score)))
    similarity = max(0, min(100, int(similarity)))
    if score >= QUALITY_HIGH_THRESHOLD:
        bucket = "high"
    elif score >= QUALITY_MEDIUM_THRESHOLD:
        bucket = "medium"
    elif score >= QUALITY_LOW_THRESHOLD:
        bucket = "low"
    else:
        return _rejected(evidence_id, "insufficient_data")
    return ComparableQualityResult(
        evidence_id, score, similarity, bucket, True, list(dict.fromkeys(flags))
    )


def _rejected(evidence_id: int, reason: str) -> ComparableQualityResult:
    return ComparableQualityResult(evidence_id, 0, 0, "rejected", False, [], reason)


def _summarize_quality(
    results: list[ComparableQualityResult],
) -> EvidenceSetQualitySummary:
    accepted = [r for r in results if r.accepted]
    scores = [r.quality_score for r in accepted]
    high = sum(1 for r in accepted if r.quality_bucket == "high")
    medium = sum(1 for r in accepted if r.quality_bucket == "medium")
    low = sum(1 for r in accepted if r.quality_bucket == "low")
    reasons: list[str] = []
    review: list[str] = []
    cap = None
    force = False
    if not accepted:
        bucket = "none"
        force = True
        reasons.append("no_accepted_comps")
        review.append("no_accepted_comps")
    elif len(accepted) == 1:
        bucket = "weak" if low else "medium"
        force = True
        cap = EVIDENCE_CONFIDENCE_CAP_WEAK
        reasons.append("single_comp")
        review.append("single_comp_cannot_support_strong_estimate")
    elif high + medium < 2:
        bucket = "indicative"
        force = True
        cap = EVIDENCE_CONFIDENCE_CAP_INDICATIVE
        reasons.append("only_low_quality_comps")
        review.append("low_quality_comps_only")
    elif high >= 2:
        bucket = "strong"
    else:
        bucket = "medium"
        cap = EVIDENCE_CONFIDENCE_CAP_WEAK
    return EvidenceSetQualitySummary(
        comparable_quality_model_version=COMPARABLE_QUALITY_MODEL_VERSION,
        total_candidates=len(results),
        accepted_count=len(accepted),
        rejected_count=len(results) - len(accepted),
        high_quality_count=high,
        medium_quality_count=medium,
        low_quality_count=low,
        best_quality_score=max(scores) if scores else None,
        median_quality_score=float(median(scores)) if scores else None,
        evidence_quality_bucket=bucket,
        evidence_confidence_cap=cap,
        evidence_quality_reasons=reasons,
        force_review=force,
        review_reasons=review,
    )


def comparable_quality_facts(assessment: ComparableQualityAssessment) -> dict[str, Any]:
    return {
        "comparable_quality_model_version": assessment.comparable_quality_model_version,
        "quality_as_of_datetime": assessment.as_of.isoformat(),
        "comparables": [
            {
                "evidence_id": r.evidence_id,
                "quality_score": r.quality_score,
                "similarity_score": r.similarity_score,
                "quality_bucket": r.quality_bucket,
                "accepted": r.accepted,
                **(
                    {"quality_flags": r.quality_flags}
                    if r.accepted
                    else {"rejection_reason": r.rejection_reason}
                ),
            }
            for r in assessment.results
        ],
        "summary": assessment.summary.__dict__,
    }


def market_evidence_fingerprint_hash(context: SelectedMarketEvidenceContext) -> str:
    raw = json.dumps(
        market_evidence_fingerprint(context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _single_listing_external_id(items: list[MarketCompInput]) -> str | None:
    ids = {i.listing_external_id for i in items if i.listing_external_id}
    return next(iter(ids)) if len(ids) == 1 else None
