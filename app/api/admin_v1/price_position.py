from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.analysis.market_comps import (
    ADJUSTED_COMPARABLE_MODEL_VERSION,
    COMPARABLE_SELECTION_POLICY_VERSION,
    SOURCE_QUALITY_MODEL_VERSION,
)
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

PRICE_POSITION_DTO_VERSION = "price-position-v1"
PRICE_POSITION_MODEL_VERSION = "price-position-v1"
PRICE_POSITION_POLICY_VERSION = "price-position-policy-v1"
PRICE_POSITION_LABEL_VERSION = "price-position-labels-v1"
PRICE_POSITION_CODES = ("below_market", "near_market", "above_market", "insufficient_data", "not_applicable")
PRICE_POSITION_CONFIDENCE = ("high", "medium", "low", "insufficient_data", "not_applicable")
PRICE_POSITION_LOCATION_BASIS = ("same_listing_context", "same_location_key", "manual_location", "insufficient_location")
PRICE_POSITION_CHART_REASONS = ("selected_comps_available", "insufficient_selected_comps", "insufficient_subject_price", "insufficient_subject_area", "insufficient_location", "not_applicable_profile")
PRICE_POSITION_METRICS = ("asking_rent_per_m2", "asking_sale_price_per_m2", "not_applicable")
PRICE_POSITION_RANGE_BASIS = ("selected_adjusted_comparables",)
SUPPORTED_PROFILE = "commercial_rent"
RANGE_BASIS = "selected_adjusted_comparables"

COMMON_LIMITATIONS = [
    "price_position_v1_deterministic",
    "selected_comparable_range_not_market_valuation",
    "not_appraisal",
    "not_valuation_report",
    "not_investment_advice",
    "frontend_must_not_compute_position",
    "commercial_rent_only_in_pr36",
]

POSITION_LABELS = {
    "below_market": {"ru": "Ниже диапазона выбранных сопоставимых объектов", "en": "Below selected comparable range"},
    "near_market": {"ru": "Около диапазона выбранных сопоставимых объектов", "en": "Near selected comparable range"},
    "above_market": {"ru": "Выше диапазона выбранных сопоставимых объектов", "en": "Above selected comparable range"},
    "insufficient_data": {"ru": "Недостаточно данных", "en": "Insufficient data"},
    "not_applicable": {"ru": "Не применимо", "en": "Not applicable"},
}
CONFIDENCE_LABELS = {
    "high": {"ru": "Высокая уверенность", "en": "High confidence"},
    "medium": {"ru": "Средняя уверенность", "en": "Medium confidence"},
    "low": {"ru": "Низкая уверенность", "en": "Low confidence"},
    "insufficient_data": {"ru": "Недостаточно данных", "en": "Insufficient data"},
    "not_applicable": {"ru": "Не применимо", "en": "Not applicable"},
}
LOCATION_LABELS = {
    "same_listing_context": {"ru": "Тот же контекст объявления", "en": "Same listing context"},
    "same_location_key": {"ru": "Та же локация", "en": "Same location key"},
    "manual_location": {"ru": "Ручная локация", "en": "Manual location"},
    "insufficient_location": {"ru": "Недостаточно данных о локации", "en": "Insufficient location"},
}
CHART_REASON_LABELS = {
    "selected_comps_available": {"ru": "Выбранные сопоставимые объекты доступны", "en": "Selected comparables available"},
    "insufficient_selected_comps": {"ru": "Недостаточно выбранных сопоставимых объектов", "en": "Insufficient selected comparables"},
    "insufficient_subject_price": {"ru": "Недостаточно данных о цене объекта", "en": "Insufficient subject price"},
    "insufficient_subject_area": {"ru": "Недостаточно данных о площади объекта", "en": "Insufficient subject area"},
    "insufficient_location": {"ru": "Недостаточно данных о локации", "en": "Insufficient location"},
    "not_applicable_profile": {"ru": "Профиль не поддерживается", "en": "Profile is not applicable"},
}
METRIC_LABELS = {
    "asking_rent_per_m2": {"ru": "Запрашиваемая аренда за м²", "en": "Asking rent per m2"},
    "asking_sale_price_per_m2": {"ru": "Запрашиваемая цена продажи за м²", "en": "Asking sale price per m2"},
    "not_applicable": {"ru": "Не применимо", "en": "Not applicable"},
}
RANGE_BASIS_LABELS = {RANGE_BASIS: {"ru": "Выбранные скорректированные сопоставимые объекты", "en": "Selected adjusted comparables"}}


@dataclass(frozen=True)
class SelectedAdjustedComparable:
    evidence_id: int
    adjusted_price_per_m2: float | Decimal


@dataclass(frozen=True)
class SelectedAdjustedComparableSource:
    items: tuple[SelectedAdjustedComparable, ...]
    location_basis: str = "insufficient_location"
    excluded_count: int | None = None
    excluded_evidence_ids: tuple[int, ...] | None = None
    selection_policy_version: str | None = COMPARABLE_SELECTION_POLICY_VERSION
    adjustment_model_version: str | None = ADJUSTED_COMPARABLE_MODEL_VERSION
    source_quality_model_version: str | None = SOURCE_QUALITY_MODEL_VERSION
    source_quality_confidence_cap: str | None = None


def get_selected_adjusted_comparable_source(listing: Listing, analysis: ListingAnalysis | None) -> SelectedAdjustedComparableSource | None:
    del listing, analysis
    return None


def _money(value: Any) -> float | None:
    if value is None:
        return None
    dec = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(dec) if dec == dec.to_integral() else float(dec)


def _subject_price_per_m2(listing: Listing) -> tuple[float | None, str | None]:
    if listing.price is None:
        return None, "insufficient_subject_price"
    if listing.area_m2 is None or listing.area_m2 <= 0:
        return None, "insufficient_subject_area"
    return _money(Decimal(str(listing.price)) / Decimal(str(listing.area_m2))), None


def _median(values: list[Decimal]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return _money(ordered[mid])
    return _money((ordered[mid - 1] + ordered[mid]) / Decimal("2"))


def _hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _profile(analysis: ListingAnalysis | None) -> str | None:
    return analysis.profile if analysis and analysis.profile else None


def _labels(position: str, confidence: str, location_basis: str, chart_reason: str, metric: str) -> dict[str, Any]:
    return {
        "position": POSITION_LABELS[position],
        "confidence": CONFIDENCE_LABELS[confidence],
        "location_basis": LOCATION_LABELS[location_basis],
        "chart_reason": CHART_REASON_LABELS[chart_reason],
        "metric": METRIC_LABELS[metric],
        "range_basis": RANGE_BASIS_LABELS[RANGE_BASIS],
    }


def _label_keys(position: str, confidence: str, location_basis: str, chart_reason: str, metric: str) -> dict[str, str]:
    return {
        "position": f"price_position.{position}",
        "confidence": f"price_position_confidence.{confidence}",
        "location_basis": f"price_position_location_basis.{location_basis}",
        "chart_reason": f"price_position_chart_reason.{chart_reason}",
        "metric": f"price_position_metric.{metric}",
        "range_basis": f"price_position_range_basis.{RANGE_BASIS}",
    }


def _confidence(position: str, count: int, location_basis: str, source_cap: str | None, limitations: list[str]) -> str:
    if position == "not_applicable":
        return "not_applicable"
    if position == "insufficient_data":
        return "insufficient_data"
    if source_cap is None:
        limitations.append("source_quality_confidence_cap_not_available_in_pr36")
        return "medium" if count >= 3 and location_basis in {"same_listing_context", "same_location_key", "manual_location"} else "low"
    if count >= 5 and location_basis in {"same_listing_context", "same_location_key"} and source_cap == "high":
        return "high"
    if count >= 3 and location_basis in {"same_listing_context", "same_location_key", "manual_location"}:
        return "medium" if source_cap in {"high", "medium"} else "low"
    return "low"


def _base_refs(listing: Listing, analysis: ListingAnalysis | None, selected_ids: list[int], source: SelectedAdjustedComparableSource | None) -> dict[str, Any]:
    return {
        "listing_id": listing.id,
        "listing_external_id": listing.external_id,
        "listing_analysis_id": analysis.id if analysis else None,
        "selected_evidence_ids": selected_ids,
        "excluded_evidence_ids": list(source.excluded_evidence_ids or []) if source else [],
        "selection_policy_version": source.selection_policy_version if source else None,
        "adjustment_model_version": source.adjustment_model_version if source else None,
        "source_quality_model_version": source.source_quality_model_version if source else None,
    }


def build_price_position(
    listing: Listing,
    analysis: ListingAnalysis | None,
    *,
    comparable_source: SelectedAdjustedComparableSource | None = None,
    decision_card_input_hash: str | None = None,
    workflow_source_hash: str | None = None,
    readiness_checklist_input_hash: str | None = None,
    risk_attention_input_hash: str | None = None,
) -> dict[str, Any]:
    profile = _profile(analysis)
    subject, subject_problem = _subject_price_per_m2(listing)
    limitations = list(COMMON_LIMITATIONS)
    metric = "asking_rent_per_m2"
    currency: str | None = "RUB"
    period: str | None = "month"
    area_unit = "m2"
    source = comparable_source if comparable_source is not None else get_selected_adjusted_comparable_source(listing, analysis)

    selected_ids: list[int] = []
    values: list[Decimal] = []
    location_basis = source.location_basis if source and source.location_basis in PRICE_POSITION_LOCATION_BASIS else "insufficient_location"
    excluded_count = 0
    if source is None:
        limitations.extend(["selected_adjusted_comps_not_available_in_pr36", "adjusted_comps_not_available_in_pr36", "excluded_comps_count_not_available_in_pr36"])
    else:
        if source.excluded_count is None:
            limitations.append("excluded_comps_count_not_available_in_pr36")
        else:
            excluded_count = max(0, int(source.excluded_count))
        for item in sorted(source.items, key=lambda x: x.evidence_id):
            if item.adjusted_price_per_m2 is None:
                continue
            dec = Decimal(str(item.adjusted_price_per_m2))
            if dec > 0:
                selected_ids.append(int(item.evidence_id))
                values.append(dec)
        if len(selected_ids) != len(source.items):
            limitations.append("adjusted_comps_not_available_in_pr36")

    if profile != SUPPORTED_PROFILE:
        metric = "not_applicable"
        currency = None
        period = None
        position = confidence = "not_applicable"
        chart_reason = "not_applicable_profile"
        chart_visible = False
        market_low = market_median = market_high = None
        location_basis = "insufficient_location"
        limitations.append("profile_unknown" if profile is None else "unsupported_profile_for_price_position_v1")
    else:
        market_low = _money(min(values)) if values else None
        market_median = _median(values)
        market_high = _money(max(values)) if values else None
        if subject_problem == "insufficient_subject_price":
            position = confidence = "insufficient_data"
            chart_reason = "insufficient_subject_price"
            chart_visible = False
            limitations.append("subject_price_missing")
        elif subject_problem == "insufficient_subject_area":
            position = confidence = "insufficient_data"
            chart_reason = "insufficient_subject_area"
            chart_visible = False
            limitations.append("subject_area_missing_or_invalid")
        elif source is not None and location_basis == "insufficient_location":
            position = confidence = "insufficient_data"
            chart_reason = "insufficient_location"
            chart_visible = False
            limitations.append("insufficient_location")
        elif len(values) < 3:
            position = confidence = "insufficient_data"
            chart_reason = "insufficient_selected_comps"
            chart_visible = False
            limitations.append("insufficient_selected_comps")
        else:
            low_band = Decimal(str(market_median)) * Decimal("0.90")
            high_band = Decimal(str(market_median)) * Decimal("1.10")
            subject_dec = Decimal(str(subject))
            if subject_dec < low_band:
                position = "below_market"
            elif subject_dec > high_band:
                position = "above_market"
            else:
                position = "near_market"
            chart_reason = "selected_comps_available"
            chart_visible = True
            confidence = _confidence(position, len(values), location_basis, source.source_quality_confidence_cap if source else None, limitations)

    selected_count = len(selected_ids)
    source_refs = _base_refs(listing, analysis, selected_ids, source)
    input_hash_payload = {
        "versions": [PRICE_POSITION_MODEL_VERSION, PRICE_POSITION_POLICY_VERSION, PRICE_POSITION_LABEL_VERSION],
        "listing": {"id": listing.id, "external_id": listing.external_id, "profile": profile, "price": listing.price, "area_m2": listing.area_m2},
        "metric": metric,
        "currency": currency,
        "period": period,
        "area_unit": area_unit,
        "subject_price_per_m2": subject,
        "selected_evidence_ids": selected_ids,
        "selected_adjusted_values": [_money(v) for v in values],
        "excluded_count": excluded_count,
        "location_basis": location_basis,
        "position": position,
        "confidence": confidence,
        "chart_reason": chart_reason,
        "source_quality_cap": source.source_quality_confidence_cap if source else None,
        "range_basis": RANGE_BASIS,
    }
    input_hashes = {
        "price_position_input_hash": _hash(input_hash_payload),
        "decision_card_input_hash": decision_card_input_hash,
        "workflow_source_hash": workflow_source_hash,
        "readiness_checklist_input_hash": readiness_checklist_input_hash,
        "risk_attention_input_hash": risk_attention_input_hash,
    }
    return {
        "schema_version": PRICE_POSITION_DTO_VERSION,
        "price_position_model_version": PRICE_POSITION_MODEL_VERSION,
        "price_position_policy_version": PRICE_POSITION_POLICY_VERSION,
        "price_position_label_version": PRICE_POSITION_LABEL_VERSION,
        "listing_id": listing.id,
        "listing_external_id": listing.external_id,
        "metric": metric,
        "currency": currency,
        "period": period,
        "area_unit": area_unit,
        "range_basis": RANGE_BASIS,
        "subject_price_per_m2": subject,
        "market_low": market_low,
        "market_median": market_median,
        "market_high": market_high,
        "position": position,
        "confidence": confidence,
        "location_basis": location_basis,
        "selected_comps_count": selected_count,
        "excluded_comps_count": excluded_count,
        "selected_evidence_ids": selected_ids,
        "chart": {"visible": chart_visible, "reason": chart_reason},
        "labels": _labels(position, confidence, location_basis, chart_reason, metric),
        "label_keys": _label_keys(position, confidence, location_basis, chart_reason, metric),
        "source_refs": source_refs,
        "input_hashes": input_hashes,
        "limitations": list(dict.fromkeys(limitations)),
    }
