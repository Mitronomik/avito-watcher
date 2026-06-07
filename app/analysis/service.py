import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.analysis.provider import AnalysisProvider, DeterministicAnalysisProvider
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_snapshot import ListingSnapshot
from app.models.search_job import SearchJob
from app.repositories.listing_analysis_repository import ListingAnalysisRepository
from app.repositories.listing_repository import ListingRepository
from app.repositories.listing_search_match_repository import ListingSearchMatchRepository


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def build_analysis_input(listing: Listing, snapshot: ListingSnapshot | None) -> dict:
    return {
        "listing": {
            "external_id": listing.external_id,
            "url": listing.url,
            "title": listing.title,
            "price": listing.price,
            "address": listing.address,
            "area_m2": listing.area_m2,
            "rooms": listing.rooms,
            "published_label": listing.published_label,
            "published_at": _dt(listing.published_at),
            "is_active": listing.is_active,
        },
        "snapshot": None
        if snapshot is None
        else {
            "id": snapshot.id,
            "external_id": snapshot.external_id,
            "title": snapshot.title,
            "price": snapshot.price,
            "published_label": snapshot.published_label,
            "published_at": _dt(snapshot.published_at),
            "payload_json": snapshot.payload_json,
            "observed_at": _dt(snapshot.observed_at),
        },
    }


def calculate_input_hash(listing: Listing, snapshot: ListingSnapshot | None) -> str:
    payload = build_analysis_input(listing, snapshot)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ListingAnalysisService:
    def __init__(
        self,
        db: Session,
        provider: AnalysisProvider | None = None,
    ) -> None:
        self.db = db
        self.provider = provider or DeterministicAnalysisProvider()
        self.analysis_repo = ListingAnalysisRepository(db)
        self.listing_repo = ListingRepository(db)

    def analyze_listing(self, external_id: str) -> ListingAnalysis:
        listing = self.listing_repo.get_by_external_id(external_id)
        if listing is None:
            raise ValueError(f"listing not found: {external_id}")
        return self._analyze_existing_listing(listing)

    def analyze_alerted_listings(self, limit: int) -> list[ListingAnalysis]:
        analyses: list[ListingAnalysis] = []
        for listing in self.analysis_repo.list_alerted_listings_without_analysis(
            limit,
            profile=self.provider.profile,
            analysis_version=self.provider.analysis_version,
            context_key="global",
        ):
            analyses.append(self._analyze_existing_listing(listing))
        return analyses

    def analyze_search_matches(self, search_job_id: int, limit: int) -> list[ListingAnalysis]:
        context_key = f"search:{search_job_id}"
        match_repo = ListingSearchMatchRepository(self.db)
        analyses: list[ListingAnalysis] = []
        for match in match_repo.list_matches_without_analysis(
            search_job_id=search_job_id,
            profile=self.provider.profile,
            analysis_version=self.provider.analysis_version,
            limit=limit,
        ):
            listing = self.listing_repo.get_by_external_id(match.listing_external_id)
            if listing is None:
                continue
            analyses.append(
                self._analyze_existing_listing(
                    listing, search_job_id=search_job_id, context_key=context_key
                )
            )
        return analyses

    def _analyze_existing_listing(
        self,
        listing: Listing,
        *,
        search_job_id: int | None = None,
        context_key: str = "global",
    ) -> ListingAnalysis:
        snapshot = self.analysis_repo.get_latest_snapshot_for_listing(
            listing.external_id
        )
        input_hash = calculate_input_hash(listing, snapshot)
        analysis = self.analysis_repo.create_or_update_analysis(
            listing_external_id=listing.external_id,
            snapshot_id=snapshot.id if snapshot is not None else None,
            profile=self.provider.profile,
            status="pending",
            analysis_version=self.provider.analysis_version,
            input_hash=input_hash,
            search_job_id=search_job_id,
            context_key=context_key,
            model_provider=self.provider.model_provider,
            model_name=self.provider.model_name,
        )
        self.analysis_repo.mark_running(analysis)

        try:
            result = self.provider.analyze(
                listing=listing, snapshot=snapshot, input_hash=input_hash
            )
        except Exception as exc:
            self.analysis_repo.mark_failed(
                analysis,
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
            return analysis

        self.analysis_repo.mark_success(
            analysis,
            score=result.score,
            verdict=result.verdict,
            facts_json=result.facts_json,
            risks_json=result.risks_json,
            questions_json=result.questions_json,
            report_md=result.report_md,
            model_provider=result.model_provider,
            model_name=result.model_name,
        )
        return analysis


def resolve_search_analysis_profile(search: SearchJob) -> str:
    filters = search.filters_json if isinstance(search.filters_json, dict) else {}
    profile = filters.get("analysis_profile")
    if isinstance(profile, str) and profile.strip():
        return profile.strip()
    return "default"
