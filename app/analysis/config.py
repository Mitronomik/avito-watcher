from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class AnalysisConfig:
    profile: str = "default"
    min_area_m2: float | None = None
    max_area_m2: float | None = None
    max_price: float | None = None
    max_age_hours: float = 72.0
    min_price_per_m2: float | None = None
    max_price_per_m2: float | None = None
    suspicious_total_price: float | None = None
    suspicious_low_price_per_m2: float | None = None
    estimated_monthly_rent: float | None = None
    opex_ratio: float | None = None
    opex_monthly: float | None = None
    vacancy_rate: float | None = None
    capex_initial: float | None = None
    min_gross_yield: float | None = None
    min_noi_yield: float | None = None
    max_payback_years: float | None = None

    @classmethod
    def from_search_filters(
        cls, profile: str, filters_json: dict | None = None
    ) -> "AnalysisConfig":
        values = asdict(_profile_defaults(profile))
        if isinstance(filters_json, dict):
            for key in values:
                if key == "profile" or key not in filters_json:
                    continue
                coerced = _coerce_number(filters_json[key])
                if coerced is not _MISSING:
                    values[key] = coerced
        return cls(**values)

    def to_hash_payload(self) -> dict[str, Any]:
        return {
            key: _normalize_value(value)
            for key, value in asdict(self).items()
            if value is not None
        }

    def hash(self) -> str:
        raw = json.dumps(
            self.to_hash_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def facts_metadata(self) -> dict[str, Any]:
        return {
            "hash": self.hash(),
            "max_age_hours": _normalize_value(self.max_age_hours),
            "min_area_m2": _normalize_value(self.min_area_m2),
            "max_area_m2": _normalize_value(self.max_area_m2),
            "max_price": _normalize_value(self.max_price),
        }


_MISSING = object()


def _profile_defaults(profile: str) -> AnalysisConfig:
    if profile == "commercial_rent":
        return AnalysisConfig(
            profile=profile,
            min_area_m2=40.0,
            max_area_m2=150.0,
            max_price=200_000.0,
            max_age_hours=72.0,
            max_price_per_m2=5_000.0,
            suspicious_total_price=5_000.0,
            suspicious_low_price_per_m2=300.0,
        )
    if profile == "flat_sale":
        return AnalysisConfig(
            profile=profile,
            min_area_m2=25.0,
            max_area_m2=90.0,
            max_price=15_000_000.0,
            max_age_hours=72.0,
            max_price_per_m2=350_000.0,
            suspicious_low_price_per_m2=100_000.0,
        )
    if profile == "flat_rent":
        return AnalysisConfig(
            profile=profile,
            min_area_m2=20.0,
            max_area_m2=90.0,
            max_price=100_000.0,
            max_age_hours=72.0,
            max_price_per_m2=3_000.0,
            suspicious_total_price=5_000.0,
            suspicious_low_price_per_m2=600.0,
        )
    return AnalysisConfig(profile=profile, max_age_hours=72.0)


def _coerce_number(value: Any) -> float | object:
    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return _MISSING
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return _MISSING
        try:
            return float(stripped)
        except ValueError:
            return _MISSING
    return _MISSING


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return float(Decimal(str(value)).normalize())
    if isinstance(value, dict):
        return {key: _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value
