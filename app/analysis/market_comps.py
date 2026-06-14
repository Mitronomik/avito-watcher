from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
MARKET_EVIDENCE_POLICY_SAME_LISTING = "same_listing"
MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY = "same_location_key"
ALLOWED_MARKET_EVIDENCE_POLICIES = {
    MARKET_EVIDENCE_POLICY_SAME_LISTING,
    MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY,
}


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


@dataclass(frozen=True)
class SelectedMarketEvidenceContext:
    items: list[MarketCompInput]
    excluded_counts_by_reason: dict[str, int]
    limitations: list[str]
    retrieval_as_of_datetime: datetime
    retrieval_as_of_date: date
    config: ResolvedMarketEvidenceConfig
    target_listing_external_id: str | None = None


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
    selected.sort(key=lambda c: (-c.confidence, -c.checked_at.timestamp(), c.id))
    return SelectedMarketEvidenceContext(
        items=selected[: max(0, resolved.max_comps)],
        excluded_counts_by_reason=excluded,
        limitations=[],
        retrieval_as_of_datetime=evidence_retrieval_as_of_datetime,
        retrieval_as_of_date=evidence_retrieval_as_of_date,
        config=resolved,
        target_listing_external_id=target_listing_external_id,
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
    if not item.content_hash:
        return "missing_content_hash"
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
    )


def estimate_market_rent(
    *, context: SelectedMarketEvidenceContext, area_m2: float | None
) -> MarketRentEstimate:
    flags: list[str] = []
    if context.config.rent_strategy != "median":
        flags.append("unsupported_market_rent_strategy")
        return _estimate_none(context, flags)
    items = context.items
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
                "source_url_normalized": i.source_url_normalized,
                "asset_type": i.asset_type,
                "deal_type": i.deal_type,
                "location_key": i.location_key,
                "rent_per_m2_rub": i.rent_per_m2_rub,
                "rent_rub_per_month": i.rent_rub_per_month,
            }
            for i in sorted(context.items, key=lambda x: x.id)
        ],
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
