from dataclasses import dataclass, field
from typing import Protocol

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
