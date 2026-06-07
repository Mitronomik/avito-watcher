from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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


def get_analysis_provider(profile: str) -> AnalysisProvider:
    if profile == "default":
        return DeterministicAnalysisProvider()
    if profile == "commercial_rent":
        return CommercialRentDeterministicAnalysisProvider()
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


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "не указана"
    return f"{value:,.0f} ₽".replace(",", " ")


def _fmt_area(value: float | None) -> str:
    if value is None:
        return "не указана"
    return f"{value:g} м²"
