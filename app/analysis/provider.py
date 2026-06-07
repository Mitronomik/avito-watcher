from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Any, Protocol

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
        self, *, listing: Listing, snapshot: ListingSnapshot | None, input_hash: str
    ) -> ListingAnalysisResult:
        """Analyze already parsed listing data without external calls."""


class DeterministicAnalysisProvider:
    profile = "default"
    analysis_version = "mock-v1"
    model_provider = "mock"
    model_name = "deterministic-local"

    def analyze(
        self, *, listing: Listing, snapshot: ListingSnapshot | None, input_hash: str
    ) -> ListingAnalysisResult:
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
        self, *, listing: Listing, snapshot: ListingSnapshot | None, input_hash: str
    ) -> ListingAnalysisResult:
        del input_hash
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
        freshness_status = _freshness_status(published_at_utc)
        detected_type = _detect_commercial_type(listing, snapshot)
        price_per_m2 = (
            round(price / area_m2, 2)
            if _positive(price) and _positive(area_m2)
            else None
        )

        target_fit = self._target_fit(
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

        flags = self._risk_flags(
            price=price,
            area_m2=area_m2,
            address=address,
            published_at=published_at_utc,
            freshness_status=freshness_status,
            detected_type=detected_type,
            text=_analysis_text(listing, snapshot),
            has_snapshot=snapshot is not None,
        )
        risks = {
            "flags": flags,
            "items": [
                {"flag": flag, "description": _RISK_DESCRIPTIONS[flag]}
                for flag in flags
            ],
        }
        questions = {"items": _questions_for(flags=flags, facts=facts)}
        score = self._score(facts=facts, flags=flags)
        verdict = _verdict(score=score, flags=flags)

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
    expensive_price_per_m2 = 350_000.0

    def analyze(
        self, *, listing: Listing, snapshot: ListingSnapshot | None, input_hash: str
    ) -> ListingAnalysisResult:
        del input_hash
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
        freshness_status = _freshness_status(published_at_utc)
        price_per_m2 = (
            round(price / area_m2, 2)
            if _positive(price) and _positive(area_m2)
            else None
        )
        floor_info = _parse_flat_floor(title)
        detected_flat_type = _detect_flat_type(listing, snapshot)
        target_fit = self._target_fit(
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
        flags = self._risk_flags(
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
        risks = {
            "flags": flags,
            "items": [
                {"flag": flag, "description": _FLAT_RISK_DESCRIPTIONS[flag]}
                for flag in flags
            ],
            "assumptions": {
                "suspicious_low_price_per_m2": self.suspicious_low_price_per_m2,
                "expensive_price_per_m2": self.expensive_price_per_m2,
                "note": "Пороги flat-sale-v0 являются простыми допущениями, а не рыночной оценкой.",
            },
        }
        questions = {"items": _flat_questions_for(flags=flags)}
        score = self._score(facts=facts, flags=flags)
        verdict = _flat_verdict(score=score)

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
    "has_utilities_hint": ("ку", "коммунальные", "счетчики", "счётчики"),
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
}

_FLAT_RENT_BASE_QUESTIONS = [
    "Уточнить итоговый ежемесячный платеж.",
    "Уточнить, включены ли коммунальные платежи и счетчики.",
    "Уточнить размер залога и условия возврата.",
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
    expensive_rent_per_m2 = 3_000.0

    def analyze(
        self, *, listing: Listing, snapshot: ListingSnapshot | None, input_hash: str
    ) -> ListingAnalysisResult:
        del input_hash
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
        freshness_status = _freshness_status(published_at_utc)
        rent_per_m2 = (
            round(price / area_m2, 2)
            if _positive(price) and _positive(area_m2)
            else None
        )
        floor_info = _parse_flat_floor(title)
        detected_flat_type = _detect_flat_type(listing, snapshot)
        rental_terms_hints = _flat_rent_terms_hints(listing, snapshot)
        target_fit = self._target_fit(
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
        flags = self._risk_flags(
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
        risks = {
            "flags": flags,
            "items": [
                {"flag": flag, "description": _FLAT_RENT_RISK_DESCRIPTIONS[flag]}
                for flag in flags
            ],
            "assumptions": {
                "target_min_area_m2": self.target_min_area_m2,
                "target_max_area_m2": self.target_max_area_m2,
                "target_max_monthly_rent": self.target_max_monthly_rent,
                "target_freshness_hours": self.target_freshness_hours,
                "suspicious_low_rent_per_m2": self.suspicious_low_rent_per_m2,
                "expensive_rent_per_m2": self.expensive_rent_per_m2,
                "note": "Пороги flat-rent-v0 являются простыми допущениями, а не рыночной оценкой.",
            },
        }
        questions = {"items": _flat_rent_questions_for(flags=flags, hints=rental_terms_hints)}
        score = self._score(facts=facts, flags=flags)
        verdict = _flat_verdict(score=score)

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

def get_analysis_provider(profile: str) -> AnalysisProvider:
    if profile == "default":
        return DeterministicAnalysisProvider()
    if profile == "commercial_rent":
        return CommercialRentDeterministicAnalysisProvider()
    if profile == "flat_sale":
        return FlatSaleDeterministicAnalysisProvider()
    if profile == "flat_rent":
        return FlatRentDeterministicAnalysisProvider()
    raise ValueError(f"unsupported analysis profile: {profile}")


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _freshness_status(published_at: datetime | None) -> str:
    if published_at is None:
        return "unknown"
    age_hours = (datetime.now(UTC) - published_at).total_seconds() / 3600
    if age_hours <= 24:
        return "fresh"
    if age_hours <= 72:
        return "recent"
    return "stale"


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
        questions.append("Уточнить точный корпус/адрес и срок сдачи, если это новостройка.")
    questions.extend(_FLAT_ALWAYS_QUESTIONS)
    if "first_floor" in flags:
        questions.append(
            "Для первого этажа: уточнить уровень окон, проходной трафик, безопасность, коммерческие помещения рядом."
        )
    if "last_floor" in flags:
        questions.append("Для последнего этажа: уточнить крышу/техэтаж/шум оборудования.")
    if "unknown_floor" in flags:
        questions.append("Уточнить этаж, общее количество этажей, лифт и расположение квартиры.")
    if "missing_price" in flags or "over_budget" in flags:
        questions.append("Уточнить финальную цену, торг и дополнительные расходы сделки.")
    if "missing_area" in flags or "area_too_small" in flags or "area_too_large" in flags:
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
        lines.append("Плюсы требуют ручной проверки из-за неполных или рискованных данных.")
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
        questions.append("Уточнить, возможен ли долгосрочный договор вместо краткосрочной аренды.")
    if not hints["has_utilities_hint"]:
        questions.append("Уточнить отдельно КУ, счетчики, интернет и сезонные платежи.")
    if not hints["has_deposit_hint"]:
        questions.append("Уточнить сумму залога, условия удержания и возврата.")
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
            f"* Залог/комиссия/КУ: залог={_fmt_bool(hints['has_deposit_hint'])}, комиссия={_fmt_bool(hints['has_commission_hint'])}, без комиссии={_fmt_bool(hints['has_no_commission_hint'])}, КУ={_fmt_bool(hints['has_utilities_hint'])}",
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
        lines.append("Плюсы требуют ручной проверки из-за неполных или рискованных данных.")
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


def _fmt_bool(value: bool) -> str:
    return "да" if value else "нет"
