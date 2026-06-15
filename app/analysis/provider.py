from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any, Protocol

from app.analysis.config import AnalysisConfig
from app.analysis.investment import calculate_investment_metrics
from app.analysis.market_comps import (
    MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY,
    SelectedMarketEvidenceContext,
    assess_comparable_quality,
    comparable_quality_facts,
    estimate_market_rent,
)
from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot


@dataclass(frozen=True)
class ListingAnalysisResult:
    score: float | None
    verdict: str | None
    facts_json: dict = field(default_factory=dict)
    risks_json: dict = field(default_factory=dict)
    questions_json: dict = field(default_factory=dict)
    report_md: str = ""
    model_provider: str | None = None
    model_name: str | None = None


class AnalysisProvider(Protocol):
    profile: str
    analysis_version: str
    model_provider: str | None
    model_name: str | None

    def analyze(
        self,
        *,
        listing: Listing,
        snapshot: ListingSnapshot | None,
        input_hash: str,
        config: AnalysisConfig | None = None,
    ) -> ListingAnalysisResult:
        """Analyze already parsed listing data without external calls."""


@dataclass(frozen=True)
class CommonSanityResult:
    flags: list[str]
    score_cap: int | None
    verdict_cap: str | None
    facts: dict


_AREA_RE = re.compile(
    r"(?<!\d)(\d{1,4}(?:[,.]\d{1,2})?)\s*"
    r"(?:м\s*(?:2|²)|кв\.?\s*м\.?|кв\s+метр(?:а|ов)?|квадратн(?:ых|ые)\s+метр(?:а|ов)?)",
    re.IGNORECASE,
)
_AREA_CONTEXT_RE = re.compile(
    r"(?<![а-яё])площадь\D{0,20}(\d{1,4}(?:[,.]\d{1,2})?)\s*метр(?:а|ов)?",
    re.IGNORECASE,
)

COMMERCIAL_RENT_HIGH_PRICE_PER_M2 = 5_000.0
FLAT_SALE_EXPENSIVE_PRICE_PER_M2 = 350_000.0
FLAT_RENT_EXPENSIVE_RENT_PER_M2 = 3_000.0

_PARKING_AMENITY_RE = re.compile(
    r"(?<![а-яё])"
    r"(?:есть|удобная|удобный|рядом|гостевая|наземная|бесплатная)"
    r"\s+парковк",
    re.IGNORECASE,
)
_PARKING_OBJECT_RE = re.compile(
    r"(?<![а-яё])(?:место\s+в\s+паркинге|парковочное\s+место)(?![а-яё])",
    re.IGNORECASE,
)
_STORAGE_OBJECT_RE = re.compile(
    r"(?<![а-яё])(?:кладовка|кладовая|машиноместо|машино-место|гараж)(?![а-яё])",
    re.IGNORECASE,
)


def apply_common_sanity_guards(
    *,
    profile: str,
    title: str | None,
    text: str,
    price: float | None,
    area_m2: float | None,
    price_per_m2: float | None,
    published_at: datetime | None,
    freshness_status: str,
    suspicious_total_price: float | None = 5_000.0,
    suspicious_low_price_per_m2: float | None = None,
    max_price_per_m2: float | None = None,
) -> CommonSanityResult:
    del published_at
    flags: list[str] = []
    score_cap: int | None = None
    verdict_cap: str | None = None
    title_area_m2 = _extract_area_m2(title or "")
    facts: dict[str, Any] = {"flags": flags}
    if title_area_m2 is not None:
        facts["title_area_m2"] = title_area_m2

    if profile in {"commercial_rent", "flat_rent"} and (
        suspicious_total_price is not None
        and price is not None
        and price <= suspicious_total_price
        and area_m2 is not None
        and area_m2 >= 10
    ):
        flags.append("suspicious_total_price")
        score_cap = _min_cap(score_cap, 70)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")

    if (
        profile == "commercial_rent"
        and suspicious_low_price_per_m2 is not None
        and price_per_m2 is not None
        and price_per_m2 < suspicious_low_price_per_m2
    ):
        flags.append("suspicious_low_price_per_m2")
        score_cap = _min_cap(score_cap, 70)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")

    high_price_per_m2 = max_price_per_m2
    if high_price_per_m2 is None:
        high_price_per_m2 = {
            "commercial_rent": COMMERCIAL_RENT_HIGH_PRICE_PER_M2,
            "flat_rent": FLAT_RENT_EXPENSIVE_RENT_PER_M2,
            "flat_sale": FLAT_SALE_EXPENSIVE_PRICE_PER_M2,
        }.get(profile)
    if (
        high_price_per_m2 is not None
        and price_per_m2 is not None
        and price_per_m2 > high_price_per_m2
    ):
        flags.append("suspicious_high_price_per_m2")
        facts["high_price_per_m2_threshold"] = high_price_per_m2
        score_cap = _min_cap(score_cap, 70)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "medium")

    if area_m2 is None:
        flags.append("missing_area_sanity_cap")
        score_cap = _min_cap(score_cap, 70)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")

    if freshness_status == "stale":
        flags.append("stale_publication_sanity_cap")
        score_cap = _min_cap(score_cap, 70)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")

    if (
        title_area_m2 is not None
        and area_m2 is not None
        and _material_area_mismatch(title_area_m2, area_m2)
    ):
        flags.append("area_parser_mismatch")
        facts["parsed_area_m2"] = area_m2
        facts["area_abs_diff_m2"] = round(abs(title_area_m2 - area_m2), 2)
        facts["area_rel_diff"] = round(
            abs(title_area_m2 - area_m2) / max(title_area_m2, area_m2), 4
        )
        score_cap = _min_cap(score_cap, 65)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")

    if _is_storage_parking_garage_object(" ".join([title or "", text])):
        flags.append("storage_parking_garage_object")
        score_cap = _min_cap(score_cap, 60)
        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")

    facts["score_cap"] = score_cap
    facts["verdict_cap"] = verdict_cap
    return CommonSanityResult(
        flags=flags, score_cap=score_cap, verdict_cap=verdict_cap, facts=facts
    )


def _extract_area_m2(text: str) -> float | None:
    match = _AREA_RE.search(text) or _AREA_CONTEXT_RE.search(text)
    if match is None:
        return None
    return float(match.group(1).replace(",", "."))


def _material_area_mismatch(title_area_m2: float, parsed_area_m2: float) -> bool:
    abs_diff = abs(title_area_m2 - parsed_area_m2)
    rel_diff = abs_diff / max(title_area_m2, parsed_area_m2)
    return abs_diff >= 10 and rel_diff >= 0.25


def _is_storage_parking_garage_object(text: str) -> bool:
    normalized = text.casefold()
    if _STORAGE_OBJECT_RE.search(normalized):
        return True
    if "парковк" in normalized and _PARKING_AMENITY_RE.search(normalized):
        normalized = _PARKING_AMENITY_RE.sub(" ", normalized)
    return _PARKING_OBJECT_RE.search(normalized) is not None


def _min_cap(current: int | None, new: int) -> int:
    return new if current is None else min(current, new)


def _strongest_verdict_cap(current: str | None, new: str) -> str:
    order = {"review": 0, "weak": 1, "medium": 2, "strong": 3}
    if current is None or order[new] < order[current]:
        return new
    return current


def _apply_verdict_cap(verdict: str, verdict_cap: str | None) -> str:
    if verdict_cap is None:
        return verdict
    order = {"review": 0, "weak": 1, "medium": 2, "strong": 3}
    if order[verdict] > order[verdict_cap]:
        return verdict_cap
    return verdict


def _extend_flags(flags: list[str], new_flags: list[str]) -> None:
    for flag in new_flags:
        if flag not in flags:
            flags.append(flag)


class DeterministicAnalysisProvider:
    profile = "default"
    analysis_version = "mock-v1"
    model_provider = "mock"
    model_name = "deterministic-local"

    def analyze(
        self,
        *,
        listing: Listing,
        snapshot: ListingSnapshot | None,
        input_hash: str,
        config: AnalysisConfig | None = None,
    ) -> ListingAnalysisResult:
        config = _provider_config(self.profile, config)
        facts = {
            "external_id": listing.external_id,
            "title": listing.title,
            "price": listing.price,
            "address": listing.address,
            "area_m2": listing.area_m2,
            "rooms": listing.rooms,
            "published_label": listing.published_label,
            "has_snapshot": snapshot is not None,
            "snapshot_id": snapshot.id if snapshot is not None else None,
        }
        _add_analysis_config_facts(facts, config)
        risks: dict[str, list[str]] = {"flags": []}
        questions: dict[str, list[str]] = {"items": []}

        if listing.price is None:
            risks["flags"].append("missing_price")
            questions["items"].append("Уточнить цену объявления.")
        if not listing.address:
            risks["flags"].append("missing_address")
            questions["items"].append("Уточнить адрес или локацию объекта.")
        if snapshot is None:
            risks["flags"].append("missing_snapshot")

        score = max(0.0, 1.0 - 0.2 * len(risks["flags"]))
        verdict = "review" if risks["flags"] else "ok"
        report_lines = [
            f"# Listing analysis: {listing.external_id}",
            "",
            f"- Title: {listing.title or 'n/a'}",
            f"- Price: {listing.price if listing.price is not None else 'n/a'}",
            f"- Address: {listing.address or 'n/a'}",
            f"- Snapshot: {snapshot.id if snapshot is not None else 'none'}",
            f"- Input hash: {input_hash}",
            f"- Verdict: {verdict}",
        ]

        return ListingAnalysisResult(
            score=score,
            verdict=verdict,
            facts_json=facts,
            risks_json=risks,
            questions_json=questions,
            report_md="\n".join(report_lines),
            model_provider=self.model_provider,
            model_name=self.model_name,
        )


_COMMERCIAL_TYPE_KEYWORDS = (
    ("free_purpose", ("свободного назначения", "псн")),
    ("production", ("производство",)),
    ("warehouse", ("склад",)),
    ("retail", ("торгов", "магазин", "павильон", "витрина", "стрит")),
    ("office", ("офис", "кабинет")),
)

_RISK_DESCRIPTIONS = {
    "missing_price": "Не указана цена аренды.",
    "missing_area": "Не указана площадь помещения.",
    "missing_address": "Не указан адрес или понятная локация.",
    "missing_published_at": "Не удалось определить дату публикации.",
    "stale_publication": "Публикация старше целевого окна свежести.",
    "over_budget": "Цена выше целевого бюджета.",
    "area_too_small": "Площадь меньше целевого диапазона.",
    "area_too_large": "Площадь больше целевого диапазона.",
    "warehouse_or_production_for_service_use": "Складской или производственный формат может не подойти для офиса, сервиса, ПВЗ или шоурума.",
    "parking_storage_garage_keyword": "В тексте есть признаки парковки, кладовки, машиноместа или гаража вместо коммерческого помещения.",
    "sublease_or_partial_area_ambiguity": "В тексте есть признаки субаренды, части площади или рабочего места внутри помещения.",
    "ambiguous_price_area": "Недостаточно данных, чтобы проверить ставку за м².",
    "no_snapshot": "Нет сохранённого снапшота объявления.",
    "suspicious_total_price": "Общая цена выглядит слишком низкой для аренды и площади; возможна ошибка парсинга или цена за часть объекта.",
    "suspicious_low_price_per_m2": "Ставка за м² подозрительно низкая; нужна ручная проверка цены и площади.",
    "suspicious_high_price_per_m2": "Ставка за м² подозрительно высокая для профиля; нужна ручная проверка цены и площади.",
    "missing_area_sanity_cap": "Без площади детерминированный скоринг не должен давать сильный вердикт.",
    "stale_publication_sanity_cap": "Старое объявление не должно получать сильный вердикт без ручной проверки.",
    "area_parser_mismatch": "Площадь в заголовке заметно отличается от распарсенной площади объявления.",
    "storage_parking_garage_object": "Есть признаки, что объект является кладовкой, машиноместом, паркингом или гаражом.",
}

_ALWAYS_QUESTIONS = [
    "Уточнить ставку за м² и коммунальные платежи.",
    "Уточнить вход, вывеску, режим доступа.",
    "Уточнить мокрую точку, электрическую мощность, вентиляцию.",
    "Уточнить срок договора и возможность регистрации.",
]


class CommercialRentDeterministicAnalysisProvider:
    profile = "commercial_rent"
    analysis_version = "commercial-rent-v0"
    model_provider = "deterministic"
    model_name = "commercial-rent-rules-v0"

    target_min_area_m2 = 40.0
    target_max_area_m2 = 150.0
    target_max_price = 200_000.0
    target_freshness_hours = 72.0

    def analyze(
        self,
        *,
        listing: Listing,
        snapshot: ListingSnapshot | None,
        input_hash: str,
        config: AnalysisConfig | None = None,
    ) -> ListingAnalysisResult:
        del input_hash
        config = _provider_config(self.profile, config)
        rules = _configured_provider(self, config)
        price = (
            listing.price
            if listing.price is not None
            else (snapshot.price if snapshot is not None else None)
        )
        area_m2 = listing.area_m2
        address = listing.address or ""
        published_label = listing.published_label or (
            snapshot.published_label if snapshot is not None else ""
        )
        published_at = listing.published_at or (
            snapshot.published_at if snapshot is not None else None
        )
        published_at_utc = _as_utc(published_at)
        freshness_status = _freshness_status(
            published_at_utc, max_age_hours=config.max_age_hours
        )
        detected_type = _detect_commercial_type(listing, snapshot)
        price_per_m2 = (
            round(price / area_m2, 2)
            if _positive(price) and _positive(area_m2)
            else None
        )

        target_fit = rules._target_fit(
            price=price, area_m2=area_m2, freshness_status=freshness_status
        )
        facts = {
            "external_id": listing.external_id,
            "title": listing.title,
            "url": listing.url,
            "price": price,
            "area_m2": area_m2,
            "price_per_m2": price_per_m2,
            "address": address,
            "published_label": published_label,
            "published_at": published_at_utc.isoformat()
            if published_at_utc is not None
            else None,
            "has_snapshot": snapshot is not None,
            "snapshot_id": snapshot.id if snapshot is not None else None,
            "detected_commercial_type": detected_type,
            "freshness_status": freshness_status,
            "target_fit": target_fit,
        }
        _add_analysis_config_facts(facts, config)

        text = _analysis_text(listing, snapshot)
        flags = rules._risk_flags(
            price=price,
            area_m2=area_m2,
            address=address,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            detected_type=detected_type,
            text=text,
            has_snapshot=snapshot is not None,
        )
        sanity = apply_common_sanity_guards(
            profile=self.profile,
            title=listing.title,
            text=text,
            price=price,
            area_m2=area_m2,
            price_per_m2=price_per_m2,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            suspicious_total_price=config.suspicious_total_price,
            suspicious_low_price_per_m2=config.suspicious_low_price_per_m2,
            max_price_per_m2=config.max_price_per_m2,
        )
        _extend_flags(flags, sanity.flags)
        facts["sanity"] = sanity.facts
        risks = {
            "flags": flags,
            "items": [
                {"flag": flag, "description": _RISK_DESCRIPTIONS[flag]}
                for flag in flags
            ],
        }
        questions = {"items": _questions_for(flags=flags, facts=facts)}
        score = rules._score(facts=facts, flags=flags)
        if sanity.score_cap is not None:
            score = min(score, sanity.score_cap)
        verdict = _verdict(score=score, flags=flags)
        verdict = _apply_verdict_cap(verdict, sanity.verdict_cap)

        return ListingAnalysisResult(
            score=score,
            verdict=verdict,
            facts_json=facts,
            risks_json=risks,
            questions_json=questions,
            report_md=_report_md(
                external_id=listing.external_id,
                score=score,
                verdict=verdict,
                facts=facts,
                risks=risks,
                questions=questions,
            ),
            model_provider=self.model_provider,
            model_name=self.model_name,
        )

    def _target_fit(
        self, *, price: float | None, area_m2: float | None, freshness_status: str
    ) -> dict:
        area_fit = (
            None
            if area_m2 is None
            else self.target_min_area_m2 <= area_m2 <= self.target_max_area_m2
        )
        price_fit = None if price is None else price <= self.target_max_price
        freshness_fit = (
            None
            if freshness_status == "unknown"
            else freshness_status in {"fresh", "recent"}
        )
        known = [
            value for value in (area_fit, price_fit, freshness_fit) if value is not None
        ]
        if not known:
            overall = "unknown"
        elif all(known) and len(known) == 3:
            overall = "good"
        elif any(known):
            overall = "partial"
        else:
            overall = "poor"
        return {
            "area_fit": area_fit,
            "price_fit": price_fit,
            "freshness_fit": freshness_fit,
            "overall": overall,
            "target_min_area_m2": self.target_min_area_m2,
            "target_max_area_m2": self.target_max_area_m2,
            "target_max_price": self.target_max_price,
            "target_freshness_hours": self.target_freshness_hours,
        }

    def _risk_flags(
        self,
        *,
        price: float | None,
        area_m2: float | None,
        address: str,
        published_at: datetime | None,
        freshness_status: str,
        detected_type: str,
        text: str,
        has_snapshot: bool,
    ) -> list[str]:
        flags: list[str] = []
        if price is None:
            flags.append("missing_price")
        if area_m2 is None:
            flags.append("missing_area")
        if not address:
            flags.append("missing_address")
        if published_at is None:
            flags.append("missing_published_at")
        if freshness_status == "stale":
            flags.append("stale_publication")
        if price is not None and price > self.target_max_price:
            flags.append("over_budget")
        if area_m2 is not None and area_m2 < self.target_min_area_m2:
            flags.append("area_too_small")
        if area_m2 is not None and area_m2 > self.target_max_area_m2:
            flags.append("area_too_large")
        if detected_type in {"warehouse", "production"}:
            flags.append("warehouse_or_production_for_service_use")
        if _has_any(text, ("кладовка", "паркинг", "машиноместо", "гараж")):
            flags.append("parking_storage_garage_keyword")
        if _has_any(
            text,
            (
                "субаренда",
                "часть помещения",
                "часть площади",
                "островок",
                "отдельное рабочее место",
                "рабочее место",
                "помещение внутри",
                "место внутри",
            ),
        ):
            flags.append("sublease_or_partial_area_ambiguity")
        if (
            price is None
            or area_m2 is None
            or not _positive(price)
            or not _positive(area_m2)
        ):
            flags.append("ambiguous_price_area")
        if not has_snapshot:
            flags.append("no_snapshot")
        return flags

    def _score(self, *, facts: dict, flags: list[str]) -> int:
        target_fit = facts["target_fit"]
        score = 50
        if facts["freshness_status"] in {"fresh", "recent"}:
            score += 10
        if target_fit["price_fit"] is True:
            score += 10
        if target_fit["area_fit"] is True:
            score += 10
        if facts["address"]:
            score += 5
        if facts["has_snapshot"]:
            score += 5
        if facts["detected_commercial_type"] in {"office", "retail", "free_purpose"}:
            score += 5
        if "missing_published_at" in flags:
            score -= 10
        if "stale_publication" in flags:
            score -= 15
        if "over_budget" in flags:
            score -= 15
        if "area_too_small" in flags or "area_too_large" in flags:
            score -= 10
        if "parking_storage_garage_keyword" in flags:
            score -= 15
        if "warehouse_or_production_for_service_use" in flags:
            score -= 10
        if "sublease_or_partial_area_ambiguity" in flags:
            score -= 10
        return max(0, min(100, score))


_FLAT_ROOM_MARKER_RE_BY_TYPE = (
    (
        "studio",
        re.compile(
            r"(?<![а-яё])(?:квартира\s*-\s*студия|студия)(?![а-яё])",
            re.IGNORECASE,
        ),
    ),
    (
        "one_room",
        re.compile(
            r"(?<![\dа-яё])(?:1\s*-\s*(?:к|комн)\.?|1\s+к\.?)(?![а-яё])"
            r"|(?<![а-яё])однокомнат",
            re.IGNORECASE,
        ),
    ),
    (
        "two_room",
        re.compile(
            r"(?<![\dа-яё])(?:2\s*-\s*(?:к|комн)\.?|2\s+к\.?)(?![а-яё])"
            r"|(?<![а-яё])двухкомнат",
            re.IGNORECASE,
        ),
    ),
    (
        "three_room",
        re.compile(
            r"(?<![\dа-яё])(?:3\s*-\s*(?:к|комн)\.?|3\s+к\.?)(?![а-яё])"
            r"|(?<![а-яё])трехкомнат|(?<![а-яё])трёхкомнат",
            re.IGNORECASE,
        ),
    ),
    (
        "multi_room",
        re.compile(
            r"(?<![\dа-яё])(?:4\s*-\s*(?:к|комн)\.?|4\s+к\.?)(?![а-яё])"
            r"|(?<![а-яё])многокомнат",
            re.IGNORECASE,
        ),
    ),
)

_FLAT_FLOOR_RE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})\s*эт", re.IGNORECASE)

_FLAT_RISK_DESCRIPTIONS = {
    "missing_price": "Не указана цена квартиры.",
    "missing_area": "Не указана площадь квартиры.",
    "missing_address": "Не указан адрес или понятная локация.",
    "missing_published_at": "Не удалось определить дату публикации.",
    "stale_publication": "Публикация старше целевого окна свежести v0.",
    "over_budget": "Цена выше целевого бюджета v0.",
    "area_too_small": "Площадь меньше целевого диапазона v0.",
    "area_too_large": "Площадь больше целевого диапазона v0.",
    "first_floor": "Первый этаж требует дополнительной проверки.",
    "last_floor": "Последний этаж требует дополнительной проверки.",
    "unknown_floor": "Этаж не удалось определить из заголовка.",
    "unknown_flat_type": "Тип квартиры не удалось определить из текста объявления.",
    "no_snapshot": "Нет сохранённого снапшота объявления.",
    "suspicious_low_price": "Цена за м² ниже простого порога v0; нужна ручная проверка.",
    "expensive_price_per_m2": "Цена за м² выше простого порога v0.",
    "suspicious_total_price": "Общая цена выглядит слишком низкой для аренды и площади; возможна ошибка парсинга или цена за часть объекта.",
    "suspicious_low_price_per_m2": "Ставка за м² подозрительно низкая; нужна ручная проверка цены и площади.",
    "suspicious_high_price_per_m2": "Ставка за м² подозрительно высокая для профиля; нужна ручная проверка цены и площади.",
    "missing_area_sanity_cap": "Без площади детерминированный скоринг не должен давать сильный вердикт.",
    "stale_publication_sanity_cap": "Старое объявление не должно получать сильный вердикт без ручной проверки.",
    "area_parser_mismatch": "Площадь в заголовке заметно отличается от распарсенной площади объявления.",
    "storage_parking_garage_object": "Есть признаки, что объект является кладовкой, машиноместом, паркингом или гаражом.",
}

_FLAT_ALWAYS_QUESTIONS = [
    "Уточнить точный корпус/адрес и срок сдачи, если это новостройка.",
    "Уточнить отделку, состояние и что входит в цену.",
    "Уточнить форму собственности и готовность документов.",
    "Уточнить обременения, ипотеку, маткапитал, альтернативную сделку.",
    "Уточнить вид из окон, шум, этажность и лифты.",
    "Уточнить платежи, УК, коммунальные расходы.",
    "Уточнить причину продажи и торг.",
]


class FlatSaleDeterministicAnalysisProvider:
    profile = "flat_sale"
    analysis_version = "flat-sale-v0"
    model_provider = "deterministic"
    model_name = "flat-sale-rules-v0"

    target_min_area_m2 = 25.0
    target_max_area_m2 = 90.0
    target_max_price = 15_000_000.0
    target_freshness_hours = 72.0
    suspicious_low_price_per_m2 = 100_000.0
    expensive_price_per_m2 = FLAT_SALE_EXPENSIVE_PRICE_PER_M2

    def analyze(
        self,
        *,
        listing: Listing,
        snapshot: ListingSnapshot | None,
        input_hash: str,
        config: AnalysisConfig | None = None,
    ) -> ListingAnalysisResult:
        del input_hash
        config = _provider_config(self.profile, config)
        rules = _configured_provider(self, config)
        title = listing.title or (snapshot.title if snapshot is not None else "")
        price = (
            listing.price
            if listing.price is not None
            else (snapshot.price if snapshot is not None else None)
        )
        area_m2 = listing.area_m2
        address = listing.address or ""
        published_label = listing.published_label or (
            snapshot.published_label if snapshot is not None else ""
        )
        published_at = listing.published_at or (
            snapshot.published_at if snapshot is not None else None
        )
        published_at_utc = _as_utc(published_at)
        freshness_status = _freshness_status(
            published_at_utc, max_age_hours=config.max_age_hours
        )
        price_per_m2 = (
            round(price / area_m2, 2)
            if _positive(price) and _positive(area_m2)
            else None
        )
        floor_info = _parse_flat_floor(title)
        detected_flat_type = _detect_flat_type(listing, snapshot)
        target_fit = rules._target_fit(
            price=price, area_m2=area_m2, freshness_status=freshness_status
        )
        facts = {
            "external_id": listing.external_id,
            "title": title,
            "url": listing.url,
            "price": price,
            "area_m2": area_m2,
            "price_per_m2": price_per_m2,
            "address": address,
            "published_label": published_label,
            "published_at": published_at_utc.isoformat()
            if published_at_utc is not None
            else None,
            "freshness_status": freshness_status,
            "has_snapshot": snapshot is not None,
            "snapshot_id": snapshot.id if snapshot is not None else None,
            "detected_flat_type": detected_flat_type,
            "floor_info": floor_info,
            "target_fit": target_fit,
        }
        _add_analysis_config_facts(facts, config)
        flags = rules._risk_flags(
            price=price,
            area_m2=area_m2,
            address=address,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            detected_flat_type=detected_flat_type,
            floor_info=floor_info,
            price_per_m2=price_per_m2,
            has_snapshot=snapshot is not None,
        )
        sanity = apply_common_sanity_guards(
            profile=self.profile,
            title=title,
            text=_analysis_text(listing, snapshot),
            price=price,
            area_m2=area_m2,
            price_per_m2=price_per_m2,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            suspicious_total_price=config.suspicious_total_price,
            suspicious_low_price_per_m2=config.suspicious_low_price_per_m2,
            max_price_per_m2=config.max_price_per_m2,
        )
        _extend_flags(flags, sanity.flags)
        facts["sanity"] = sanity.facts
        risks = {
            "flags": flags,
            "items": [
                {"flag": flag, "description": _FLAT_RISK_DESCRIPTIONS[flag]}
                for flag in flags
            ],
            "assumptions": {
                "suspicious_low_price_per_m2": rules.suspicious_low_price_per_m2,
                "expensive_price_per_m2": rules.expensive_price_per_m2,
                "note": "Пороги flat-sale-v0 являются простыми допущениями, а не рыночной оценкой.",
            },
        }
        questions = {"items": _flat_questions_for(flags=flags)}
        score = rules._score(facts=facts, flags=flags)
        if sanity.score_cap is not None:
            score = min(score, sanity.score_cap)
        verdict = _flat_verdict(score=score)
        verdict = _apply_verdict_cap(verdict, sanity.verdict_cap)

        return ListingAnalysisResult(
            score=score,
            verdict=verdict,
            facts_json=facts,
            risks_json=risks,
            questions_json=questions,
            report_md=_flat_report_md(
                external_id=listing.external_id,
                score=score,
                verdict=verdict,
                facts=facts,
                risks=risks,
                questions=questions,
            ),
            model_provider=self.model_provider,
            model_name=self.model_name,
        )

    def _target_fit(
        self, *, price: float | None, area_m2: float | None, freshness_status: str
    ) -> dict:
        area_fit = (
            None
            if area_m2 is None
            else self.target_min_area_m2 <= area_m2 <= self.target_max_area_m2
        )
        price_fit = None if price is None else price <= self.target_max_price
        freshness_fit = (
            None
            if freshness_status == "unknown"
            else freshness_status in {"fresh", "recent"}
        )
        known = [
            value for value in (area_fit, price_fit, freshness_fit) if value is not None
        ]
        if not known:
            overall = "unknown"
        elif all(known) and len(known) == 3:
            overall = "good"
        elif any(known):
            overall = "partial"
        else:
            overall = "poor"
        return {
            "area_fit": area_fit,
            "price_fit": price_fit,
            "freshness_fit": freshness_fit,
            "overall": overall,
            "target_min_area_m2": self.target_min_area_m2,
            "target_max_area_m2": self.target_max_area_m2,
            "target_max_price": self.target_max_price,
            "target_freshness_hours": self.target_freshness_hours,
        }

    def _risk_flags(
        self,
        *,
        price: float | None,
        area_m2: float | None,
        address: str,
        published_at: datetime | None,
        freshness_status: str,
        detected_flat_type: str,
        floor_info: dict,
        price_per_m2: float | None,
        has_snapshot: bool,
    ) -> list[str]:
        flags: list[str] = []
        if price is None:
            flags.append("missing_price")
        if area_m2 is None:
            flags.append("missing_area")
        if not address:
            flags.append("missing_address")
        if published_at is None:
            flags.append("missing_published_at")
        if freshness_status == "stale":
            flags.append("stale_publication")
        if price is not None and price > self.target_max_price:
            flags.append("over_budget")
        if area_m2 is not None and area_m2 < self.target_min_area_m2:
            flags.append("area_too_small")
        if area_m2 is not None and area_m2 > self.target_max_area_m2:
            flags.append("area_too_large")
        if floor_info["is_first_floor"] is True:
            flags.append("first_floor")
        if floor_info["is_last_floor"] is True:
            flags.append("last_floor")
        if floor_info["floor"] is None:
            flags.append("unknown_floor")
        if detected_flat_type == "unknown":
            flags.append("unknown_flat_type")
        if not has_snapshot:
            flags.append("no_snapshot")
        if price_per_m2 is not None and price_per_m2 < self.suspicious_low_price_per_m2:
            flags.append("suspicious_low_price")
        if price_per_m2 is not None and price_per_m2 > self.expensive_price_per_m2:
            flags.append("expensive_price_per_m2")
        return flags

    def _score(self, *, facts: dict, flags: list[str]) -> int:
        target_fit = facts["target_fit"]
        score = 50
        if facts["freshness_status"] in {"fresh", "recent"}:
            score += 10
        if target_fit["price_fit"] is True:
            score += 10
        if target_fit["area_fit"] is True:
            score += 10
        if facts["address"]:
            score += 5
        if facts["has_snapshot"]:
            score += 5
        if facts["detected_flat_type"] != "unknown":
            score += 5
        if "missing_published_at" in flags:
            score -= 10
        if "stale_publication" in flags:
            score -= 15
        if "over_budget" in flags:
            score -= 15
        if "area_too_small" in flags or "area_too_large" in flags:
            score -= 10
        if "first_floor" in flags:
            score -= 8
        if "last_floor" in flags:
            score -= 6
        if "unknown_floor" in flags:
            score -= 5
        if "unknown_flat_type" in flags:
            score -= 5
        if "no_snapshot" in flags:
            score -= 10
        if "expensive_price_per_m2" in flags:
            score -= 10
        return max(0, min(100, score))


_FLAT_RENT_TERM_KEYWORDS = {
    "has_deposit_hint": ("залог", "депозит"),
    "has_commission_hint": ("комиссия",),
    "has_no_commission_hint": ("без комиссии", "комиссии нет"),
    "has_utilities_hint": (
        "коммунальные",
        "счетчики",
        "счётчики",
        "ку отдельно",
        "ку включ",
        "к/у",
        "к.у.",
    ),
    "has_furniture_hint": ("мебель", "меблир", "диван", "кровать", "шкаф"),
    "has_appliances_hint": ("техника", "холодильник", "стиральная", "посудомоечная"),
    "has_pets_hint": ("животн", "с питомц", "можно с кош", "можно с собак"),
    "has_children_hint": ("дети", "с детьми"),
    "has_long_term_hint": ("длительный срок", "долгосрочно"),
    "has_short_term_hint": ("посуточно", "краткосрочно", "на сутки"),
}

_FLAT_RENT_RISK_DESCRIPTIONS = {
    "missing_price": "Не указана месячная аренда.",
    "missing_area": "Не указана площадь квартиры.",
    "missing_address": "Не указан адрес или понятная локация.",
    "missing_published_at": "Не удалось определить дату публикации.",
    "stale_publication": "Публикация старше целевого окна свежести 72 часа.",
    "over_budget": "Аренда выше целевого бюджета v0.",
    "area_too_small": "Площадь меньше целевого диапазона v0.",
    "area_too_large": "Площадь больше целевого диапазона v0.",
    "first_floor": "Первый этаж требует дополнительной проверки.",
    "last_floor": "Последний этаж требует дополнительной проверки.",
    "unknown_floor": "Этаж не удалось определить из заголовка.",
    "unknown_flat_type": "Тип квартиры не удалось определить из текста объявления.",
    "no_snapshot": "Нет сохранённого снапшота объявления.",
    "expensive_rent_per_m2": "Аренда за м² выше простого порога v0.",
    "suspicious_low_rent_per_m2": "Аренда за м² ниже простого порога v0; нужна ручная проверка.",
    "short_term_rent": "Есть признаки краткосрочной или посуточной аренды.",
    "deposit_unknown": "Не найден явный hint про залог или депозит.",
    "commission_unknown": "Не найден явный hint про комиссию или её отсутствие.",
    "utilities_unknown": "Не найден явный hint про коммунальные платежи или счётчики.",
    "furniture_unknown": "Не найден явный hint про мебель.",
    "suspicious_total_price": "Общая цена выглядит слишком низкой для аренды и площади; возможна ошибка парсинга или цена за часть объекта.",
    "suspicious_low_price_per_m2": "Ставка за м² подозрительно низкая; нужна ручная проверка цены и площади.",
    "suspicious_high_price_per_m2": "Ставка за м² подозрительно высокая для профиля; нужна ручная проверка цены и площади.",
    "missing_area_sanity_cap": "Без площади детерминированный скоринг не должен давать сильный вердикт.",
    "stale_publication_sanity_cap": "Старое объявление не должно получать сильный вердикт без ручной проверки.",
    "area_parser_mismatch": "Площадь в заголовке заметно отличается от распарсенной площади объявления.",
    "storage_parking_garage_object": "Есть признаки, что объект является кладовкой, машиноместом, паркингом или гаражом.",
}

_FLAT_RENT_BASE_QUESTIONS = [
    "Уточнить итоговый ежемесячный платеж.",
    "Уточнить, включены ли коммунальные платежи и счетчики.",
    "Уточнить размер залога, условия удержания и возврата.",
    "Уточнить комиссию.",
    "Уточнить срок аренды и возможность долгосрочного договора.",
    "Уточнить состав мебели и техники.",
    "Уточнить, можно ли с детьми и животными.",
    "Уточнить интернет, парковку, лифты, шум и соседей.",
    "Уточнить, кто собственник и как оформляется договор.",
]


class FlatRentDeterministicAnalysisProvider:
    profile = "flat_rent"
    analysis_version = "flat-rent-v0"
    model_provider = "deterministic"
    model_name = "flat-rent-rules-v0"

    target_min_area_m2 = 20.0
    target_max_area_m2 = 90.0
    target_max_monthly_rent = 100_000.0
    target_freshness_hours = 72.0
    suspicious_low_rent_per_m2 = 600.0
    expensive_rent_per_m2 = FLAT_RENT_EXPENSIVE_RENT_PER_M2

    def analyze(
        self,
        *,
        listing: Listing,
        snapshot: ListingSnapshot | None,
        input_hash: str,
        config: AnalysisConfig | None = None,
    ) -> ListingAnalysisResult:
        del input_hash
        config = _provider_config(self.profile, config)
        rules = _configured_provider(self, config)
        title = listing.title or (snapshot.title if snapshot is not None else "")
        price = (
            listing.price
            if listing.price is not None
            else (snapshot.price if snapshot is not None else None)
        )
        area_m2 = listing.area_m2
        address = listing.address or ""
        published_label = listing.published_label or (
            snapshot.published_label if snapshot is not None else ""
        )
        published_at = listing.published_at or (
            snapshot.published_at if snapshot is not None else None
        )
        published_at_utc = _as_utc(published_at)
        freshness_status = _freshness_status(
            published_at_utc, max_age_hours=config.max_age_hours
        )
        rent_per_m2 = (
            round(price / area_m2, 2)
            if _positive(price) and _positive(area_m2)
            else None
        )
        floor_info = _parse_flat_floor(title)
        detected_flat_type = _detect_flat_type(listing, snapshot)
        rental_terms_hints = _flat_rent_terms_hints(listing, snapshot)
        target_fit = rules._target_fit(
            price=price, area_m2=area_m2, freshness_status=freshness_status
        )
        facts = {
            "external_id": listing.external_id,
            "title": title,
            "url": listing.url,
            "price": price,
            "area_m2": area_m2,
            "rent_per_m2": rent_per_m2,
            "address": address,
            "published_label": published_label,
            "published_at": published_at_utc.isoformat()
            if published_at_utc is not None
            else None,
            "freshness_status": freshness_status,
            "has_snapshot": snapshot is not None,
            "snapshot_id": snapshot.id if snapshot is not None else None,
            "detected_flat_type": detected_flat_type,
            "floor_info": floor_info,
            "rental_terms_hints": rental_terms_hints,
            "target_fit": target_fit,
        }
        _add_analysis_config_facts(facts, config)
        flags = rules._risk_flags(
            price=price,
            area_m2=area_m2,
            address=address,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            detected_flat_type=detected_flat_type,
            floor_info=floor_info,
            rent_per_m2=rent_per_m2,
            has_snapshot=snapshot is not None,
            rental_terms_hints=rental_terms_hints,
        )
        sanity = apply_common_sanity_guards(
            profile=self.profile,
            title=title,
            text=_analysis_text(listing, snapshot),
            price=price,
            area_m2=area_m2,
            price_per_m2=rent_per_m2,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            suspicious_total_price=config.suspicious_total_price,
            suspicious_low_price_per_m2=config.suspicious_low_price_per_m2,
            max_price_per_m2=config.max_price_per_m2,
        )
        _extend_flags(flags, sanity.flags)
        facts["sanity"] = sanity.facts
        risks = {
            "flags": flags,
            "items": [
                {"flag": flag, "description": _FLAT_RENT_RISK_DESCRIPTIONS[flag]}
                for flag in flags
            ],
            "assumptions": {
                "target_min_area_m2": rules.target_min_area_m2,
                "target_max_area_m2": rules.target_max_area_m2,
                "target_max_monthly_rent": rules.target_max_monthly_rent,
                "target_freshness_hours": rules.target_freshness_hours,
                "suspicious_low_rent_per_m2": rules.suspicious_low_rent_per_m2,
                "expensive_rent_per_m2": rules.expensive_rent_per_m2,
                "note": "Пороги flat-rent-v0 являются простыми допущениями, а не рыночной оценкой.",
            },
        }
        questions = {
            "items": _flat_rent_questions_for(flags=flags, hints=rental_terms_hints)
        }
        score = rules._score(facts=facts, flags=flags)
        if sanity.score_cap is not None:
            score = min(score, sanity.score_cap)
        verdict = _flat_verdict(score=score)
        verdict = _apply_verdict_cap(verdict, sanity.verdict_cap)

        return ListingAnalysisResult(
            score=score,
            verdict=verdict,
            facts_json=facts,
            risks_json=risks,
            questions_json=questions,
            report_md=_flat_rent_report_md(
                external_id=listing.external_id,
                score=score,
                verdict=verdict,
                facts=facts,
                risks=risks,
                questions=questions,
            ),
            model_provider=self.model_provider,
            model_name=self.model_name,
        )

    def _target_fit(
        self, *, price: float | None, area_m2: float | None, freshness_status: str
    ) -> dict:
        area_fit = (
            None
            if area_m2 is None
            else self.target_min_area_m2 <= area_m2 <= self.target_max_area_m2
        )
        price_fit = None if price is None else price <= self.target_max_monthly_rent
        freshness_fit = (
            None
            if freshness_status == "unknown"
            else freshness_status in {"fresh", "recent"}
        )
        known = [
            value for value in (area_fit, price_fit, freshness_fit) if value is not None
        ]
        if not known:
            overall = "unknown"
        elif all(known) and len(known) == 3:
            overall = "good"
        elif any(known):
            overall = "partial"
        else:
            overall = "poor"
        return {
            "area_fit": area_fit,
            "price_fit": price_fit,
            "freshness_fit": freshness_fit,
            "overall": overall,
            "target_min_area_m2": self.target_min_area_m2,
            "target_max_area_m2": self.target_max_area_m2,
            "target_max_monthly_rent": self.target_max_monthly_rent,
            "target_freshness_hours": self.target_freshness_hours,
        }

    def _risk_flags(
        self,
        *,
        price: float | None,
        area_m2: float | None,
        address: str,
        published_at: datetime | None,
        freshness_status: str,
        detected_flat_type: str,
        floor_info: dict,
        rent_per_m2: float | None,
        has_snapshot: bool,
        rental_terms_hints: dict,
    ) -> list[str]:
        flags: list[str] = []
        if price is None:
            flags.append("missing_price")
        if area_m2 is None:
            flags.append("missing_area")
        if not address:
            flags.append("missing_address")
        if published_at is None:
            flags.append("missing_published_at")
        if freshness_status == "stale":
            flags.append("stale_publication")
        if price is not None and price > self.target_max_monthly_rent:
            flags.append("over_budget")
        if area_m2 is not None and area_m2 < self.target_min_area_m2:
            flags.append("area_too_small")
        if area_m2 is not None and area_m2 > self.target_max_area_m2:
            flags.append("area_too_large")
        if floor_info["is_first_floor"] is True:
            flags.append("first_floor")
        if floor_info["is_last_floor"] is True:
            flags.append("last_floor")
        if floor_info["floor"] is None:
            flags.append("unknown_floor")
        if detected_flat_type == "unknown":
            flags.append("unknown_flat_type")
        if not has_snapshot:
            flags.append("no_snapshot")
        if rent_per_m2 is not None and rent_per_m2 > self.expensive_rent_per_m2:
            flags.append("expensive_rent_per_m2")
        if rent_per_m2 is not None and rent_per_m2 < self.suspicious_low_rent_per_m2:
            flags.append("suspicious_low_rent_per_m2")
        if rental_terms_hints["has_short_term_hint"]:
            flags.append("short_term_rent")
        if not rental_terms_hints["has_deposit_hint"]:
            flags.append("deposit_unknown")
        if not (
            rental_terms_hints["has_commission_hint"]
            or rental_terms_hints["has_no_commission_hint"]
        ):
            flags.append("commission_unknown")
        if not rental_terms_hints["has_utilities_hint"]:
            flags.append("utilities_unknown")
        if not rental_terms_hints["has_furniture_hint"]:
            flags.append("furniture_unknown")
        return flags

    def _score(self, *, facts: dict, flags: list[str]) -> int:
        target_fit = facts["target_fit"]
        hints = facts["rental_terms_hints"]
        score = 50
        if facts["freshness_status"] in {"fresh", "recent"}:
            score += 10
        if target_fit["price_fit"] is True:
            score += 10
        if target_fit["area_fit"] is True:
            score += 10
        if facts["address"]:
            score += 5
        if facts["has_snapshot"]:
            score += 5
        if facts["detected_flat_type"] != "unknown":
            score += 5
        if hints["has_furniture_hint"] or hints["has_appliances_hint"]:
            score += 5
        if hints["has_no_commission_hint"]:
            score += 5
        if "missing_published_at" in flags:
            score -= 10
        if "stale_publication" in flags:
            score -= 15
        if "over_budget" in flags:
            score -= 15
        if "area_too_small" in flags or "area_too_large" in flags:
            score -= 10
        if "first_floor" in flags:
            score -= 8
        if "last_floor" in flags:
            score -= 6
        if "unknown_floor" in flags:
            score -= 5
        if "unknown_flat_type" in flags:
            score -= 5
        if "no_snapshot" in flags:
            score -= 10
        if "expensive_rent_per_m2" in flags:
            score -= 10
        if "short_term_rent" in flags:
            score -= 10
        if "deposit_unknown" in flags:
            score -= 5
        if "commission_unknown" in flags:
            score -= 5
        return max(0, min(100, score))


def _provider_config(profile: str, config: AnalysisConfig | None) -> AnalysisConfig:
    return config or AnalysisConfig.from_search_filters(profile=profile)


def _configured_provider(provider: Any, config: AnalysisConfig) -> Any:
    configured = copy(provider)
    _apply_analysis_config(configured, config)
    return configured


def _apply_analysis_config(provider: Any, config: AnalysisConfig) -> None:
    if config.min_area_m2 is not None:
        provider.target_min_area_m2 = config.min_area_m2
    if config.max_area_m2 is not None:
        provider.target_max_area_m2 = config.max_area_m2
    if config.max_price is not None:
        if hasattr(provider, "target_max_monthly_rent"):
            provider.target_max_monthly_rent = config.max_price
        else:
            provider.target_max_price = config.max_price
    provider.target_freshness_hours = config.max_age_hours
    if config.suspicious_low_price_per_m2 is not None:
        if hasattr(provider, "suspicious_low_rent_per_m2"):
            provider.suspicious_low_rent_per_m2 = config.suspicious_low_price_per_m2
        else:
            provider.suspicious_low_price_per_m2 = config.suspicious_low_price_per_m2
    if config.max_price_per_m2 is not None:
        if hasattr(provider, "expensive_rent_per_m2"):
            provider.expensive_rent_per_m2 = config.max_price_per_m2
        else:
            provider.expensive_price_per_m2 = config.max_price_per_m2


def _add_analysis_config_facts(facts: dict, config: AnalysisConfig) -> None:
    facts["analysis_config"] = config.facts_metadata()


_INVESTMENT_PROFILE_META = {
    "commercial_sale_investment": {
        "version": "commercial-sale-investment-v0",
        "model": "commercial-sale-investment-rules-v0",
        "asset": "commercial",
        "questions": [
            "Уточнить реальную рыночную аренду.",
            "Проверить, что цена объявления является ценой покупки, а не арендной ставкой.",
            "Уточнить операционные расходы собственника.",
            "Уточнить НДС/УСН/налоговый режим, если важно.",
            "Уточнить коммунальные платежи и эксплуатационные расходы.",
            "Уточнить вакантность.",
            "Уточнить первоначальный CAPEX.",
            "Проверить отдельный вход, мокрую точку, вентиляцию, мощность.",
            "Проверить юридическую возможность целевого использования.",
        ],
    },
    "flat_sale_investment": {
        "version": "flat-sale-investment-v0",
        "model": "flat-sale-investment-rules-v0",
        "asset": "flat",
        "questions": [
            "Уточнить реальную долгосрочную арендную ставку.",
            "Уточнить ежемесячные расходы собственника.",
            "Уточнить ремонт/CAPEX перед сдачей.",
            "Уточнить вакантность между арендаторами.",
            "Проверить, что цена является ценой покупки.",
            "Проверить ликвидность локации и транспортную доступность вручную.",
        ],
    },
}


class InvestmentAnalysisProvider:
    model_provider = "deterministic"

    def __init__(self, profile: str) -> None:
        meta = _INVESTMENT_PROFILE_META[profile]
        self.profile = profile
        self.analysis_version = meta["version"]
        self.model_name = meta["model"]
        self.expected_asset_type = meta["asset"]
        self.base_questions = meta["questions"]

    def analyze(
        self,
        *,
        listing: Listing,
        snapshot: ListingSnapshot | None,
        input_hash: str,
        config: AnalysisConfig | None = None,
        market_evidence_context: SelectedMarketEvidenceContext | None = None,
    ) -> ListingAnalysisResult:
        del snapshot, input_hash
        config = _provider_config(self.profile, config)
        purchase_price = config.investment_purchase_price
        purchase_source = (
            "filters_json.investment_purchase_price"
            if purchase_price is not None
            else None
        )
        purchase_confirmation = False
        pre_flags: list[str] = []
        allow_listing_price_fallback = (
            config.investment_allow_listing_price_as_purchase_price is True
        )
        if config.deal_type == "rent":
            pre_flags.append("deal_type_rent_not_sale")
        elif (
            purchase_price is None
            and allow_listing_price_fallback
            and config.investment_price_basis == "listing_price_as_purchase_price"
            and config.deal_type != "rent"
        ):
            purchase_price = listing.price
            purchase_source = "listing.price"
            purchase_confirmation = True
            pre_flags.append("purchase_price_source_requires_human_confirmation")
        if config.deal_type is None:
            pre_flags.append("deal_type_missing")
        if (
            config.asset_type is not None
            and config.asset_type != self.expected_asset_type
        ):
            pre_flags.append("asset_type_profile_mismatch")

        quality_assessment = (
            assess_comparable_quality(
                context=market_evidence_context,
                expected_asset_type=self.expected_asset_type,
                target_area_m2=listing.area_m2,
                target_location_key=market_evidence_context.config.location_key,
                as_of=market_evidence_context.retrieval_as_of_datetime,
            )
            if config.use_market_evidence is True
            and market_evidence_context is not None
            else None
        )
        market_estimate = (
            estimate_market_rent(
                context=market_evidence_context,
                area_m2=listing.area_m2,
                quality_assessment=quality_assessment,
            )
            if config.use_market_evidence is True
            and market_evidence_context is not None
            else None
        )
        rent_source = (
            "manual" if config.estimated_monthly_rent is not None else "missing"
        )
        estimated_rent = config.estimated_monthly_rent
        market_flags: list[str] = []
        score_cap: int | None = None
        verdict_cap: str | None = None
        if config.use_market_evidence is False:
            market_flags.append("market_evidence_disabled")
        elif config.use_market_evidence is True:
            if market_estimate is None or market_estimate.comp_count == 0:
                if config.estimated_monthly_rent is None:
                    market_flags.append("market_evidence_missing")
            elif config.estimated_monthly_rent is None:
                if (
                    market_estimate.monthly_rent is not None
                    and market_estimate.usable_comp_count
                    >= market_evidence_context.config.min_comps
                ):
                    estimated_rent = market_estimate.monthly_rent
                    rent_source = "market_evidence"
                else:
                    market_flags.append("insufficient_market_comps")
            if market_estimate is not None:
                market_flags.extend(market_estimate.risk_flags)
                if config.estimated_monthly_rent is None:
                    if market_estimate.usable_comp_count == 1:
                        market_flags.append("single_market_comp")
                        score_cap = 65
                        verdict_cap = "review"
                    elif (
                        market_estimate.usable_comp_count
                        < market_evidence_context.config.min_comps
                    ):
                        market_flags.append("insufficient_market_comps")
                        score_cap = 70
                        verdict_cap = "review"
                    if (
                        market_estimate.confidence is not None
                        and market_estimate.confidence
                        < market_evidence_context.config.min_confidence
                    ):
                        market_flags.append("low_confidence_market_comps")
                        score_cap = 70 if score_cap is None else min(score_cap, 70)
                        verdict_cap = "review"
                elif market_estimate.monthly_rent:
                    delta = (
                        abs(
                            config.estimated_monthly_rent - market_estimate.monthly_rent
                        )
                        / market_estimate.monthly_rent
                    )
                    if (
                        delta
                        > market_evidence_context.config.manual_mismatch_threshold_pct
                    ):
                        market_flags.append("manual_rent_differs_from_market_evidence")
            for limitation in market_evidence_context.limitations:
                market_flags.append(limitation)
            if (
                market_evidence_context.config.matching_policy
                == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
            ):
                if market_estimate is not None and market_estimate.comp_count == 0:
                    market_flags.append("market_evidence_location_key_no_matches")
                if (
                    config.estimated_monthly_rent is None
                    and market_estimate is not None
                    and market_estimate.usable_comp_count
                    < market_evidence_context.config.min_comps
                ):
                    market_flags.append("cross_listing_low_sample")
                external_ids = {
                    i.listing_external_id
                    for i in market_evidence_context.items
                    if i.listing_external_id
                    != market_evidence_context.target_listing_external_id
                }
                selected_urls = {
                    i.source_url_normalized
                    for i in market_evidence_context.items
                    if i.source_url_normalized
                }
                has_cross_listing = bool(external_ids)
                low_diversity = bool(market_evidence_context.items) and (
                    len({i.listing_external_id for i in market_evidence_context.items})
                    < 2
                    and len(selected_urls) < 2
                )
                if rent_source == "market_evidence" and has_cross_listing:
                    market_flags.append("cross_listing_evidence_requires_human_review")
                    verdict_cap = _strongest_verdict_cap(verdict_cap, "medium")
            if quality_assessment is not None:
                market_flags.extend(quality_assessment.summary.review_reasons)
                if (
                    quality_assessment.summary.force_review
                    and config.estimated_monthly_rent is None
                ):
                    verdict_cap = _strongest_verdict_cap(verdict_cap, "review")
                if (
                    quality_assessment.summary.evidence_confidence_cap is not None
                    and rent_source == "market_evidence"
                ):
                    score_cap = min(score_cap or 100, 70)
            if (
                market_evidence_context.config.matching_policy
                == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
            ):
                if low_diversity:
                    market_flags.append("cross_listing_low_diversity")
                    if rent_source == "market_evidence":
                        verdict_cap = _strongest_verdict_cap(verdict_cap, "review")
        metrics = calculate_investment_metrics(
            purchase_price=purchase_price,
            purchase_price_source=purchase_source,
            estimated_monthly_rent=estimated_rent,
            opex_ratio=config.opex_ratio,
            opex_monthly=config.opex_monthly,
            vacancy_rate=config.vacancy_rate,
            capex_initial=config.capex_initial,
            min_gross_yield=config.min_gross_yield,
            min_noi_yield=config.min_noi_yield,
            max_payback_years=config.max_payback_years,
        )
        flags = list(dict.fromkeys([*pre_flags, *metrics.flags, *market_flags]))
        if (
            config.min_gross_yield is None
            and config.min_noi_yield is None
            and config.max_payback_years is None
        ):
            flags.append("all_thresholds_missing")
        if config.min_gross_yield is not None and (
            metrics.gross_yield_on_total_outlay is None
            or metrics.gross_yield_on_total_outlay < config.min_gross_yield
        ):
            flags.append("gross_yield_below_threshold")
        if config.min_noi_yield is not None and (
            metrics.noi_yield_on_total_outlay is None
            or metrics.noi_yield_on_total_outlay < config.min_noi_yield
        ):
            flags.append("noi_yield_below_threshold")
        if config.max_payback_years is not None and (
            metrics.payback_years is None
            or metrics.payback_years > config.max_payback_years
        ):
            flags.append("payback_above_threshold")
        if metrics.purchase_price is None or metrics.estimated_monthly_rent is None:
            flags.append("insufficient_investment_assumptions")

        score = 50
        complete = (
            metrics.purchase_price is not None
            and metrics.estimated_monthly_rent is not None
            and metrics.noi_annual is not None
        )
        if complete:
            score += 10
        threshold_flags = {
            "gross_yield_below_threshold",
            "noi_yield_below_threshold",
            "payback_above_threshold",
        }
        score += 10 * sum(
            1
            for f in ("min_gross_yield", "min_noi_yield", "max_payback_years")
            if f in metrics.assumptions
        )
        score -= 15 * len(threshold_flags.intersection(flags))
        score -= 10 * sum(
            1
            for f in (
                "vacancy_rate_missing_assumed_zero",
                "capex_missing_assumed_zero",
                "opex_missing",
            )
            if f in flags
        )
        score -= 25 * sum(
            1
            for f in (
                "negative_or_zero_noi",
                "invalid_purchase_price",
                "invalid_estimated_monthly_rent",
            )
            if f in flags
        )
        score -= (
            15 if "purchase_price_source_requires_human_confirmation" in flags else 0
        )
        if score_cap is not None:
            score = min(score, score_cap)
        score = max(0, min(100, score))
        verdict = (
            "strong"
            if score >= 80 and not threshold_flags.intersection(flags)
            else "medium"
            if score >= 60
            else "weak"
        )
        cap = None
        for flag, flag_cap in {
            "missing_investment_purchase_price": "review",
            "missing_estimated_monthly_rent": "review",
            "invalid_purchase_price": "review",
            "invalid_estimated_monthly_rent": "review",
            "negative_or_zero_noi": "review",
            "deal_type_rent_not_sale": "review",
            "asset_type_profile_mismatch": "review",
            "opex_missing": "weak",
            "vacancy_rate_missing_assumed_zero": "medium",
            "capex_missing_assumed_zero": "medium",
            "all_thresholds_missing": "medium",
            "purchase_price_source_requires_human_confirmation": "medium",
        }.items():
            if flag in flags:
                cap = _strongest_verdict_cap(cap, flag_cap)
        if (
            "vacancy_rate_missing_assumed_zero" in flags
            and "capex_missing_assumed_zero" in flags
        ):
            cap = _strongest_verdict_cap(cap, "weak")
        if verdict_cap is not None:
            cap = _strongest_verdict_cap(cap, verdict_cap)
        if metrics.noi_annual is None:
            cap = _strongest_verdict_cap(cap, "weak")
        verdict = _apply_verdict_cap(verdict, cap)

        market_evidence_enabled = config.use_market_evidence is True
        stored_market_evidence_selected = market_evidence_context is not None and bool(
            market_evidence_context.items
        )
        market_evidence_used_as_rent_source = rent_source == "market_evidence"
        market_evidence_used_for_comparison = (
            config.estimated_monthly_rent is not None
            and market_estimate is not None
            and market_estimate.monthly_rent is not None
        )

        facts = {
            "investment_profile": self.profile,
            "investment_metrics": {
                **metrics.to_dict(),
                "rent_estimate_source": rent_source,
                **(
                    {
                        "market_evidence": _market_evidence_facts(
                            market_evidence_context,
                            market_estimate,
                            config.estimated_monthly_rent,
                            rent_source == "market_evidence",
                            "cross_listing_low_diversity" in flags,
                            quality_assessment,
                        )
                    }
                    if config.use_market_evidence is True
                    and market_evidence_context is not None
                    else {}
                ),
            },
            "manual_assumptions_only": config.use_market_evidence is not True,
            "market_evidence_enabled": market_evidence_enabled,
            "stored_market_evidence_selected": stored_market_evidence_selected,
            "market_evidence_used_as_rent_source": market_evidence_used_as_rent_source,
            "market_evidence_used_for_comparison": market_evidence_used_for_comparison,
            "market_comps_used": market_evidence_used_as_rent_source,
            "external_research_used": False,
            "live_external_research_used": False,
            "stored_market_evidence_used": (
                market_evidence_used_as_rent_source
                or market_evidence_used_for_comparison
            ),
            "market_evidence_origin": "stored_external_research"
            if config.use_market_evidence is True
            else None,
            "llm_used": False,
            "rag_used": False,
            "agent_used": False,
            "purchase_price_source": purchase_source,
            "purchase_price_requires_human_confirmation": purchase_confirmation,
            "threshold_basis": "gross and NOI thresholds compare against total initial outlay",
        }
        _add_analysis_config_facts(facts, config)
        risks = {"flags": list(dict.fromkeys(flags))}
        questions = {
            "items": _investment_questions(
                base_questions=self.base_questions,
                market_evidence_enabled=market_evidence_enabled,
                used_as_rent_source=market_evidence_used_as_rent_source,
                used_for_comparison=market_evidence_used_for_comparison,
                flags=risks["flags"],
                market_evidence_context=market_evidence_context,
            )
        }
        report = self._report(
            metrics.to_dict(),
            risks["flags"],
            questions["items"],
            purchase_source,
            purchase_confirmation,
            cap,
            market_evidence_enabled=market_evidence_enabled,
            used_as_rent_source=market_evidence_used_as_rent_source,
            used_for_comparison=market_evidence_used_for_comparison,
        )
        return ListingAnalysisResult(
            score=score,
            verdict=verdict,
            facts_json=facts,
            risks_json=risks,
            questions_json=questions,
            report_md=report,
            model_provider=self.model_provider,
            model_name=self.model_name,
        )

    def _report(
        self,
        metrics: dict,
        flags: list[str],
        questions: list[str],
        source: str | None,
        confirmation: bool,
        cap: str | None,
        *,
        market_evidence_enabled: bool,
        used_as_rent_source: bool,
        used_for_comparison: bool,
    ) -> str:
        warning = (
            " Listing price was used as purchase price because the operator explicitly allowed it; human confirmation is required."
            if confirmation
            else ""
        )
        if used_as_rent_source:
            evidence_line = (
                "Deterministic v0 analysis: stored SQL-backed market evidence was used as rent source. "
                "It uses no LLM, no ResearchAgent, and no live external calls during scoring."
            )
        elif used_for_comparison:
            evidence_line = (
                "Deterministic v0 analysis: manual rent remained primary and stored market evidence was used for comparison. "
                "It uses no LLM, no ResearchAgent, and no live external calls during scoring."
            )
        elif market_evidence_enabled:
            evidence_line = (
                "Deterministic v0 analysis had market evidence enabled, but no stored comps were used as the rent source. "
                "It uses no LLM, no ResearchAgent, and no live external calls during scoring."
            )
        else:
            evidence_line = (
                "Deterministic v0 analysis uses manual assumptions only. It uses no comps, no LLM, "
                "no ResearchAgent, and no live external calls during scoring."
            )
        return "\n".join(
            [
                f"# Investment analysis: {self.profile}",
                "",
                evidence_line,
                "This is not an appraisal, not a market valuation, and not a buy/sell recommendation.",
                f"Purchase price source: {source or 'missing'}.{warning}",
                "Formulas: annual gross income = rent * 12; vacancy loss = gross * vacancy; NOI = effective gross income - opex; total outlay = purchase price + CAPEX; yields divide by price and total outlay; payback = total outlay / NOI.",
                "Threshold basis: min_gross_yield and min_noi_yield compare against total initial outlay.",
                f"Missing assumptions: {', '.join(metrics['missing_assumptions']) or 'none'}.",
                f"Risk caps applied: {cap or 'none'}.",
                f"Flags: {', '.join(flags) or 'none'}.",
                "Human-review questions:",
                *[f"- {q}" for q in questions],
            ]
        )


def _investment_questions(
    *,
    base_questions: list[str],
    market_evidence_enabled: bool,
    used_as_rent_source: bool,
    used_for_comparison: bool,
    flags: list[str],
    market_evidence_context: SelectedMarketEvidenceContext | None = None,
) -> list[str]:
    questions = list(base_questions)
    if not market_evidence_enabled:
        questions.append(
            "При необходимости вручную проверить рыночные арендные comps вне расчета."
        )
    elif used_as_rent_source:
        questions.append(
            "Проверить выбранные stored SQL-backed rent comps и их применимость к объекту."
        )
    elif used_for_comparison:
        questions.append(
            "Сравнить ручную арендную ставку с выбранными stored rent comps; ручная ставка остается основной."
        )
    else:
        questions.append(
            "Проверить, почему stored market evidence не дало достаточную арендную оценку."
        )

    weak_flags = {
        "market_evidence_missing",
        "insufficient_market_comps",
        "single_market_comp",
        "low_confidence_market_comps",
        "missing_area_for_market_rent",
        "unsupported_market_rent_strategy",
    }
    if weak_flags.intersection(flags):
        questions.append(
            "Провести ручную проверку рыночной аренды из-за слабых, недостаточных или неприменимых comps."
        )
    if (
        market_evidence_context is not None
        and market_evidence_context.config.matching_policy
        == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
    ):
        questions.extend(
            [
                "Проверить применимость cross-listing market comps к объекту.",
                "Проверить, что location_key корректно отражает микролокацию объекта.",
                "Проверить, что selected comps относятся к тому же asset/deal type.",
                "Проверить cross-listing comps вручную, так как comp quality scoring ещё не реализован.",
            ]
        )
        if {
            "market_evidence_location_key_missing",
            "market_evidence_location_key_no_matches",
            "cross_listing_low_sample",
        }.intersection(flags):
            questions.append("Недостаточно market evidence по выбранному location_key.")
            questions.append(
                "Нужна ручная арендная ставка или дополнительное market research."
            )
    if "missing_area_for_market_rent" in flags:
        questions.append(
            "Уточнить площадь объекта, чтобы конвертировать rent-per-m2 comps в месячную аренду."
        )
    return list(dict.fromkeys(questions))


def _market_evidence_facts(
    context: SelectedMarketEvidenceContext,
    estimate,
    manual_rent: float | None,
    used_as_rent_source: bool = False,
    low_diversity: bool = False,
    quality_assessment=None,
) -> dict:
    facts = {
        "enabled": True,
        "used": estimate is not None and estimate.monthly_rent is not None,
        "used_as_rent_source": estimate is not None
        and estimate.monthly_rent is not None
        and manual_rent is None,
        "used_for_comparison": estimate is not None
        and estimate.monthly_rent is not None
        and manual_rent is not None,
        "backend": "sql",
        "retrieval_as_of_date": context.retrieval_as_of_date.isoformat(),
        "market_rent_strategy": context.config.rent_strategy,
        "market_comp_count": estimate.comp_count
        if estimate is not None
        else len(context.items),
        "market_usable_comp_count": estimate.usable_comp_count
        if estimate is not None
        else 0,
        "market_estimate_confidence": estimate.confidence
        if estimate is not None
        else None,
        "market_evidence_item_ids": estimate.item_ids if estimate is not None else [],
        "market_evidence_content_hashes": estimate.content_hashes
        if estimate is not None
        else [],
        "market_source_urls": estimate.source_urls if estimate is not None else [],
        "excluded_counts_by_reason": context.excluded_counts_by_reason,
        "matching_policy": context.config.matching_policy,
        "location_key": context.config.location_key,
        "cross_listing_reuse_enabled": context.config.matching_policy
        == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY,
        "comp_quality_scoring_used": quality_assessment is not None,
        **({"comparable_quality": comparable_quality_facts(quality_assessment)} if quality_assessment is not None else {}),
        "selected_listing_external_ids": [i.listing_external_id for i in context.items],
        "selected_same_listing_count": sum(
            1
            for i in context.items
            if i.listing_external_id == context.target_listing_external_id
        ),
        "selected_external_listing_count": sum(
            1
            for i in context.items
            if i.listing_external_id != context.target_listing_external_id
        ),
        "selected_distinct_listing_external_id_count": len(
            {i.listing_external_id for i in context.items if i.listing_external_id}
        ),
        "selected_distinct_source_url_count": len(
            {i.source_url_normalized for i in context.items if i.source_url_normalized}
        ),
        "cross_listing_low_diversity": low_diversity,
        "cross_listing_verdict_cap_applied": (
            used_as_rent_source
            and context.config.matching_policy
            == MARKET_EVIDENCE_POLICY_SAME_LOCATION_KEY
            and any(
                i.listing_external_id != context.target_listing_external_id
                for i in context.items
            )
        ),
    }
    if estimate is not None:
        facts["market_estimated_monthly_rent"] = estimate.monthly_rent
        facts["market_estimated_rent_per_m2"] = estimate.rent_per_m2
    if manual_rent is not None:
        facts["manual_estimated_monthly_rent"] = manual_rent
        if estimate is not None and estimate.monthly_rent:
            facts["manual_vs_market_delta_pct"] = round(
                abs(manual_rent - estimate.monthly_rent) / estimate.monthly_rent, 4
            )
    return facts


def get_analysis_provider(profile: str) -> AnalysisProvider:
    if profile == "default":
        return DeterministicAnalysisProvider()
    if profile == "commercial_rent":
        return CommercialRentDeterministicAnalysisProvider()
    if profile == "flat_sale":
        return FlatSaleDeterministicAnalysisProvider()
    if profile == "flat_rent":
        return FlatRentDeterministicAnalysisProvider()
    if profile in {"commercial_sale_investment", "flat_sale_investment"}:
        return InvestmentAnalysisProvider(profile)
    raise ValueError(f"unsupported analysis profile: {profile}")


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _freshness_status(
    published_at: datetime | None, *, max_age_hours: float = 72.0
) -> str:
    if published_at is None:
        return "unknown"
    age_hours = (datetime.now(UTC) - published_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return "stale"
    if age_hours <= 24:
        return "fresh"
    return "recent"


def _analysis_text(listing: Listing, snapshot: ListingSnapshot | None) -> str:
    parts: list[str] = [
        listing.title or "",
        listing.address or "",
        listing.published_label or "",
    ]
    if snapshot is not None:
        parts.extend([snapshot.title or "", snapshot.published_label or ""])
        parts.extend(_payload_text(snapshot.payload_json))
    return " ".join(part for part in parts if part).casefold()


def _payload_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_payload_text(item))
        return parts
    if isinstance(value, list):
        parts = []
        for item in value:
            parts.extend(_payload_text(item))
        return parts
    return []


def _detect_commercial_type(listing: Listing, snapshot: ListingSnapshot | None) -> str:
    text = _analysis_text(listing, snapshot)
    for commercial_type, keywords in _COMMERCIAL_TYPE_KEYWORDS:
        if _has_any(text, keywords):
            return commercial_type
    return "unknown"


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _questions_for(*, flags: list[str], facts: dict) -> list[str]:
    questions: list[str] = []
    if (
        "missing_area" in flags
        or "area_too_small" in flags
        or "area_too_large" in flags
    ):
        questions.append("Уточнить точную арендуемую площадь.")
    if (
        "missing_price" in flags
        or "ambiguous_price_area" in flags
        or "sublease_or_partial_area_ambiguity" in flags
    ):
        questions.append("Уточнить, цена указана за всё помещение или за часть.")
    questions.extend(_ALWAYS_QUESTIONS)
    if "sublease_or_partial_area_ambiguity" in flags:
        questions.append("Уточнить, это прямой договор или субаренда.")
    if facts["detected_commercial_type"] in {
        "retail",
        "free_purpose",
        "office",
        "unknown",
    }:
        questions.append(
            "Уточнить, подходит ли помещение под офис, сервис, ПВЗ или шоурум."
        )
    return list(dict.fromkeys(questions))


def _verdict(*, score: int, flags: list[str]) -> str:
    major_missing = {"missing_price", "missing_area", "missing_published_at"}
    if score < 35 or major_missing.intersection(flags):
        return "review"
    if score >= 75:
        return "strong"
    if score >= 55:
        return "medium"
    return "weak"


def _report_md(
    *,
    external_id: str,
    score: int,
    verdict: str,
    facts: dict,
    risks: dict,
    questions: dict,
) -> str:
    risk_lines = [item["description"] for item in risks["items"]] or [
        "Критичных детерминированных рисков не найдено."
    ]
    good_lines = _good_lines(facts=facts, risks=risks)
    suitable_for = _suitable_for(facts["detected_commercial_type"])
    summary = _summary(facts=facts, score=score, verdict=verdict)
    return "\n".join(
        [
            f"# Анализ объекта {external_id}",
            "",
            "## Вердикт",
            "",
            f"{verdict}, score {score}/100.",
            "",
            "## Кратко",
            "",
            summary,
            "",
            "## Факты",
            "",
            f"* Тип: {facts['detected_commercial_type']}",
            f"* Цена: {_fmt_money(facts['price'])}",
            f"* Площадь: {_fmt_area(facts['area_m2'])}",
            f"* Цена за м²: {_fmt_money(facts['price_per_m2'])}",
            f"* Адрес: {facts['address'] or 'не указан'}",
            f"* Свежесть: {facts['freshness_status']}",
            "",
            "## Что хорошо",
            "",
            *[f"* {line}" for line in good_lines],
            "",
            "## Риски",
            "",
            *[f"* {line}" for line in risk_lines],
            "",
            "## Что уточнить перед звонком",
            "",
            *[f"* {item}" for item in questions["items"]],
            "",
            "## Подходит для",
            "",
            f"* {suitable_for}",
        ]
    )


def _summary(*, facts: dict, score: int, verdict: str) -> str:
    title = facts["title"] or "Объект без названия"
    return f"{title}. Детерминированный профиль оценил объект как {verdict} ({score}/100) на основе уже сохранённых данных объявления."


def _good_lines(*, facts: dict, risks: dict) -> list[str]:
    flags = set(risks["flags"])
    lines: list[str] = []
    if facts["freshness_status"] in {"fresh", "recent"}:
        lines.append("Объявление попадает в целевое окно свежести.")
    if facts["target_fit"]["price_fit"] is True:
        lines.append("Цена не выше целевого бюджета.")
    if facts["target_fit"]["area_fit"] is True:
        lines.append("Площадь в целевом диапазоне.")
    if facts["detected_commercial_type"] in {"office", "retail", "free_purpose"}:
        lines.append(
            "Тип помещения выглядит релевантным для коммерческой аренды под сервисный формат."
        )
    if not lines and flags:
        lines.append(
            "Плюсы требуют ручной проверки из-за неполных или рискованных данных."
        )
    return lines or ["Базовые факты заполнены, явных keyword-рисков нет."]


def _suitable_for(commercial_type: str) -> str:
    if commercial_type == "office":
        return "офис / сервис"
    if commercial_type == "retail":
        return "магазин / ПВЗ / шоурум"
    if commercial_type == "free_purpose":
        return "офис / сервис / ПВЗ / шоурум"
    if commercial_type == "warehouse":
        return "склад"
    if commercial_type == "production":
        return "производство"
    return "неизвестно"


def _flat_verdict(*, score: int) -> str:
    if score >= 75:
        return "strong"
    if score >= 55:
        return "medium"
    if score >= 35:
        return "weak"
    return "review"


def _detect_flat_type(listing: Listing, snapshot: ListingSnapshot | None) -> str:
    text = _analysis_text(listing, snapshot)
    for flat_type, pattern in _FLAT_ROOM_MARKER_RE_BY_TYPE:
        if pattern.search(text):
            return flat_type
    return "unknown"


def _parse_flat_floor(title: str) -> dict:
    match = _FLAT_FLOOR_RE.search(title or "")
    if match is None:
        return {
            "floor": None,
            "total_floors": None,
            "is_first_floor": None,
            "is_last_floor": None,
        }
    floor = int(match.group(1))
    total_floors = int(match.group(2))
    if floor <= 0 or total_floors <= 0 or floor > total_floors:
        return {
            "floor": None,
            "total_floors": None,
            "is_first_floor": None,
            "is_last_floor": None,
        }
    return {
        "floor": floor,
        "total_floors": total_floors,
        "is_first_floor": floor == 1,
        "is_last_floor": floor == total_floors,
    }


def _flat_questions_for(*, flags: list[str]) -> list[str]:
    questions: list[str] = []
    if "missing_address" in flags:
        questions.append(
            "Уточнить точный корпус/адрес и срок сдачи, если это новостройка."
        )
    questions.extend(_FLAT_ALWAYS_QUESTIONS)
    if "first_floor" in flags:
        questions.append(
            "Для первого этажа: уточнить уровень окон, проходной трафик, безопасность, коммерческие помещения рядом."
        )
    if "last_floor" in flags:
        questions.append(
            "Для последнего этажа: уточнить крышу/техэтаж/шум оборудования."
        )
    if "unknown_floor" in flags:
        questions.append(
            "Уточнить этаж, общее количество этажей, лифт и расположение квартиры."
        )
    if "missing_price" in flags or "over_budget" in flags:
        questions.append(
            "Уточнить финальную цену, торг и дополнительные расходы сделки."
        )
    if (
        "missing_area" in flags
        or "area_too_small" in flags
        or "area_too_large" in flags
    ):
        questions.append("Уточнить точную площадь, планировку и долю полезной площади.")
    return list(dict.fromkeys(questions))


def _flat_report_md(
    *,
    external_id: str,
    score: int,
    verdict: str,
    facts: dict,
    risks: dict,
    questions: dict,
) -> str:
    risk_lines = [item["description"] for item in risks["items"]] or [
        "Критичных детерминированных рисков не найдено."
    ]
    good_lines = _flat_good_lines(facts=facts, risks=risks)
    floor_info = facts["floor_info"]
    floor = (
        f"{floor_info['floor']}/{floor_info['total_floors']}"
        if floor_info["floor"] is not None
        else "не указан"
    )
    return "\n".join(
        [
            f"# Анализ квартиры {external_id}",
            "",
            "## Вердикт",
            "",
            f"{verdict}, score {score}/100.",
            "Вердикт flat-sale-v0 определяется только по score thresholds: "
            "strong >= 75, medium >= 55, weak >= 35, review < 35.",
            "",
            "## Кратко",
            "",
            _flat_summary(facts=facts, score=score, verdict=verdict),
            "",
            "## Факты",
            "",
            f"* Тип квартиры: {facts['detected_flat_type']}",
            f"* Цена: {_fmt_money(facts['price'])}",
            f"* Площадь: {_fmt_area(facts['area_m2'])}",
            f"* Цена за м²: {_fmt_money(facts['price_per_m2'])}",
            f"* Адрес: {facts['address'] or 'не указан'}",
            f"* Этаж: {floor}",
            f"* Свежесть: {facts['freshness_status']}",
            "",
            "## Что хорошо",
            "",
            *[f"* {line}" for line in good_lines],
            "",
            "## Риски",
            "",
            *[f"* {line}" for line in risk_lines],
            "",
            "## Что уточнить перед звонком",
            "",
            *[f"* {item}" for item in questions["items"]],
            "",
            "## Подходит для",
            "",
            f"* {_flat_suitable_for(facts=facts)}",
        ]
    )


def _flat_summary(*, facts: dict, score: int, verdict: str) -> str:
    title = facts["title"] or "Квартира без названия"
    return f"{title}. Детерминированный профиль flat-sale-v0 оценил объявление как {verdict} ({score}/100) на основе уже сохранённых данных объявления. Пороги цены за м² — простые допущения v0, а не рыночная оценка."


def _flat_good_lines(*, facts: dict, risks: dict) -> list[str]:
    flags = set(risks["flags"])
    lines: list[str] = []
    if facts["freshness_status"] in {"fresh", "recent"}:
        lines.append("Объявление попадает в целевое окно свежести 72 часа.")
    if facts["target_fit"]["price_fit"] is True:
        lines.append("Цена не выше целевого бюджета v0.")
    if facts["target_fit"]["area_fit"] is True:
        lines.append("Площадь в целевом диапазоне v0.")
    if facts["detected_flat_type"] != "unknown":
        lines.append("Тип квартиры удалось определить из текста объявления.")
    if not lines and flags:
        lines.append(
            "Плюсы требуют ручной проверки из-за неполных или рискованных данных."
        )
    return lines or ["Базовые факты заполнены, явных v0-рисков нет."]


def _flat_suitable_for(*, facts: dict) -> str:
    flat_type = facts["detected_flat_type"]
    area_m2 = facts["area_m2"]
    if flat_type in {"studio", "one_room"}:
        return "первое жильё / инвестиция / аренда"
    if flat_type in {"two_room", "three_room", "multi_room"} or (
        area_m2 is not None and area_m2 >= 55
    ):
        return "семья / первое жильё"
    return "неизвестно"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "не указана"
    return f"{value:,.0f} ₽".replace(",", " ")


def _fmt_area(value: float | None) -> str:
    if value is None:
        return "не указана"
    return f"{value:g} м²"


def _flat_rent_terms_hints(
    listing: Listing, snapshot: ListingSnapshot | None
) -> dict[str, bool]:
    text = _analysis_text(listing, snapshot)
    return {
        key: _has_any(text, keywords)
        for key, keywords in _FLAT_RENT_TERM_KEYWORDS.items()
    }


def _flat_rent_questions_for(*, flags: list[str], hints: dict) -> list[str]:
    questions = list(_FLAT_RENT_BASE_QUESTIONS)
    if "first_floor" in flags:
        questions.append(
            "Для первого этажа: уточнить безопасность, окна, шум и приватность."
        )
    if "last_floor" in flags:
        questions.append(
            "Для последнего этажа: уточнить крышу, техэтаж и шум оборудования."
        )
    if "over_budget" in flags or "missing_price" in flags:
        questions.append("Уточнить финальную ставку аренды и все обязательные платежи.")
    if "short_term_rent" in flags or hints["has_short_term_hint"]:
        questions.append(
            "Уточнить, возможен ли долгосрочный договор вместо краткосрочной аренды."
        )
    if not hints["has_utilities_hint"]:
        questions.append("Уточнить отдельно КУ, счетчики, интернет и сезонные платежи.")
    if not (hints["has_commission_hint"] or hints["has_no_commission_hint"]):
        questions.append("Уточнить, есть ли комиссия агенту и в каком размере.")
    return list(dict.fromkeys(questions))


def _flat_rent_report_md(
    *,
    external_id: str,
    score: int,
    verdict: str,
    facts: dict,
    risks: dict,
    questions: dict,
) -> str:
    risk_lines = [item["description"] for item in risks["items"]] or [
        "Критичных детерминированных рисков не найдено."
    ]
    good_lines = _flat_rent_good_lines(facts=facts, risks=risks)
    floor_info = facts["floor_info"]
    floor = (
        f"{floor_info['floor']}/{floor_info['total_floors']}"
        if floor_info["floor"] is not None
        else "не указан"
    )
    hints = facts["rental_terms_hints"]
    return "\n".join(
        [
            f"# Анализ аренды квартиры {external_id}",
            "",
            "## Вердикт",
            "",
            f"{verdict}, score {score}/100.",
            "Вердикт flat-rent-v0 определяется только по score thresholds: "
            "strong >= 75, medium >= 55, weak >= 35, review < 35.",
            "",
            "## Кратко",
            "",
            _flat_rent_summary(facts=facts, score=score, verdict=verdict),
            "",
            "## Факты",
            "",
            f"* Тип квартиры: {facts['detected_flat_type']}",
            f"* Аренда в месяц: {_fmt_money(facts['price'])}",
            f"* Площадь: {_fmt_area(facts['area_m2'])}",
            f"* Аренда за м²: {_fmt_money(facts['rent_per_m2'])}",
            f"* Адрес: {facts['address'] or 'не указан'}",
            f"* Этаж: {floor}",
            f"* Свежесть: {facts['freshness_status']}",
            f"* Мебель/техника: мебель={_fmt_bool(hints['has_furniture_hint'])}, техника={_fmt_bool(hints['has_appliances_hint'])}",
            f"* Залог/комиссия/КУ: залог={_fmt_hint_found(hints['has_deposit_hint'])}, комиссия={_fmt_flat_rent_commission_hint(hints)}, КУ={_fmt_hint_found(hints['has_utilities_hint'])}",
            "",
            "## Что хорошо",
            "",
            *[f"* {line}" for line in good_lines],
            "",
            "## Риски",
            "",
            *[f"* {line}" for line in risk_lines],
            "",
            "## Что уточнить перед звонком",
            "",
            *[f"* {item}" for item in questions["items"]],
            "",
            "## Подходит для",
            "",
            f"* {_flat_rent_suitable_for(facts=facts)}",
        ]
    )


def _flat_rent_summary(*, facts: dict, score: int, verdict: str) -> str:
    title = facts["title"] or "Квартира без названия"
    return f"{title}. Детерминированный профиль flat-rent-v0 оценил объявление как {verdict} ({score}/100) только по уже сохранённым данным объявления. Пороги аренды за м² — простые допущения v0, а не рыночная оценка."


def _flat_rent_good_lines(*, facts: dict, risks: dict) -> list[str]:
    flags = set(risks["flags"])
    hints = facts["rental_terms_hints"]
    lines: list[str] = []
    if facts["freshness_status"] in {"fresh", "recent"}:
        lines.append("Объявление попадает в целевое окно свежести 72 часа.")
    if facts["target_fit"]["price_fit"] is True:
        lines.append("Аренда не выше целевого бюджета v0.")
    if facts["target_fit"]["area_fit"] is True:
        lines.append("Площадь в целевом диапазоне v0.")
    if facts["detected_flat_type"] != "unknown":
        lines.append("Тип квартиры удалось определить из текста объявления.")
    if hints["has_no_commission_hint"]:
        lines.append("Есть hint об отсутствии комиссии.")
    if hints["has_furniture_hint"] or hints["has_appliances_hint"]:
        lines.append("Есть hint о мебели или технике.")
    if not lines and flags:
        lines.append(
            "Плюсы требуют ручной проверки из-за неполных или рискованных данных."
        )
    return lines or ["Базовые факты заполнены, явных v0-рисков нет."]


def _flat_rent_suitable_for(*, facts: dict) -> str:
    hints = facts["rental_terms_hints"]
    flat_type = facts["detected_flat_type"]
    if hints["has_short_term_hint"]:
        return "временная аренда"
    if flat_type in {"studio", "one_room"}:
        return "один человек / пара / долгосрочная аренда"
    if flat_type in {"two_room", "three_room", "multi_room"}:
        return "пара / семья / долгосрочная аренда"
    if hints["has_long_term_hint"]:
        return "долгосрочная аренда"
    return "неизвестно"


def _fmt_flat_rent_commission_hint(hints: dict) -> str:
    if hints["has_no_commission_hint"]:
        return "без комиссии указано"
    if hints["has_commission_hint"]:
        return "найдена"
    return "не найдена"


def _fmt_hint_found(value: bool) -> str:
    return "найдено" if value else "не найдено"


def _fmt_bool(value: bool) -> str:
    return "да" if value else "нет"
