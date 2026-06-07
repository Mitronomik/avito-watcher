import hashlib
import inspect
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.analysis.config import AnalysisConfig
from app.analysis.provider import AnalysisProvider, DeterministicAnalysisProvider
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_snapshot import ListingSnapshot
from app.models.search_job import SearchJob
from app.repositories.listing_analysis_repository import ListingAnalysisRepository
from app.repositories.listing_repository import ListingRepository
from app.repositories.listing_search_match_repository import ListingSearchMatchRepository
from app.repositories.search_repository import SearchRepository


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def build_analysis_input(
    listing: Listing,
    snapshot: ListingSnapshot | None,
    *,
    profile: str = "default",
    analysis_version: str = "mock-v1",
    context_key: str = "global",
    config: AnalysisConfig | None = None,
) -> dict:
    config = config or AnalysisConfig.from_search_filters(profile=profile)
    return {
        "profile": profile,
        "analysis_version": analysis_version,
        "context_key": context_key,
        "analysis_config_hash": config.hash(),
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


def calculate_input_hash(
    listing: Listing,
    snapshot: ListingSnapshot | None,
    *,
    profile: str = "default",
    analysis_version: str = "mock-v1",
    context_key: str = "global",
    config: AnalysisConfig | None = None,
) -> str:
    payload = build_analysis_input(
        listing,
        snapshot,
        profile=profile,
        analysis_version=analysis_version,
        context_key=context_key,
        config=config,
    )
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
        self.search_repo = SearchRepository(db)

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
        config = self._config_for_search(search_job_id)
        analyses: list[ListingAnalysis] = []
        for match in self.list_search_matches_needing_analysis(
            search_job_id=search_job_id, limit=limit
        ):
            listing = self.listing_repo.get_by_external_id(match.listing_external_id)
            if listing is None:
                continue
            analyses.append(
                self._analyze_existing_listing(
                    listing,
                    search_job_id=search_job_id,
                    context_key=context_key,
                    config=config,
                )
            )
        return analyses

    def analyze_search_listing(
        self, search_job_id: int, external_id: str
    ) -> ListingAnalysis | None:
        search = self.search_repo.get(search_job_id)
        if search is None:
            return None
        listing = self.listing_repo.get_by_external_id(external_id)
        if listing is None:
            return None
        return self._analyze_existing_listing(
            listing,
            search_job_id=search_job_id,
            context_key=f"search:{search_job_id}",
            config=self._config_for_search(search_job_id),
        )

    def get_current_search_listing_analysis(
        self, search_job_id: int, external_id: str
    ) -> ListingAnalysis | None:
        search = self.search_repo.get(search_job_id)
        if search is None:
            return None
        listing = self.listing_repo.get_by_external_id(external_id)
        if listing is None:
            return None
        context_key = f"search:{search_job_id}"
        config = self._config_for_search(search_job_id)
        input_hash = self._calculate_current_input_hash(
            listing=listing, context_key=context_key, config=config
        )
        return self.analysis_repo.get_by_input_hash(
            listing_external_id=listing.external_id,
            profile=self.provider.profile,
            analysis_version=self.provider.analysis_version,
            input_hash=input_hash,
            context_key=context_key,
        )

    def list_search_matches_needing_analysis(
        self, search_job_id: int, limit: int
    ) -> list:
        if limit <= 0:
            return []
        context_key = f"search:{search_job_id}"
        config = self._config_for_search(search_job_id)
        pending = []
        for match in ListingSearchMatchRepository(self.db).list_matches_for_search(
            search_job_id
        ):
            listing = self.listing_repo.get_by_external_id(match.listing_external_id)
            if listing is None:
                continue
            input_hash = self._calculate_current_input_hash(
                listing=listing, context_key=context_key, config=config
            )
            existing = self.analysis_repo.get_by_input_hash(
                listing_external_id=listing.external_id,
                profile=self.provider.profile,
                analysis_version=self.provider.analysis_version,
                input_hash=input_hash,
                context_key=context_key,
            )
            if existing is not None:
                continue
            pending.append(match)
            if len(pending) >= limit:
                break
        return pending

    def _analyze_existing_listing(
        self,
        listing: Listing,
        *,
        search_job_id: int | None = None,
        context_key: str = "global",
        config: AnalysisConfig | None = None,
    ) -> ListingAnalysis:
        snapshot = self.analysis_repo.get_latest_snapshot_for_listing(
            listing.external_id
        )
        config = config or AnalysisConfig.from_search_filters(profile=self.provider.profile)
        input_hash = self._calculate_current_input_hash(
            listing=listing, snapshot=snapshot, context_key=context_key, config=config
        )
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
            analyze_kwargs = {
                "listing": listing,
                "snapshot": snapshot,
                "input_hash": input_hash,
            }
            if "config" in inspect.signature(self.provider.analyze).parameters:
                analyze_kwargs["config"] = config
            result = self.provider.analyze(**analyze_kwargs)
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

    def _calculate_current_input_hash(
        self,
        *,
        listing: Listing,
        context_key: str,
        config: AnalysisConfig,
        snapshot: ListingSnapshot | None = None,
    ) -> str:
        if snapshot is None:
            snapshot = self.analysis_repo.get_latest_snapshot_for_listing(
                listing.external_id
            )
        return calculate_input_hash(
            listing,
            snapshot,
            profile=self.provider.profile,
            analysis_version=self.provider.analysis_version,
            context_key=context_key,
            config=config,
        )


    def _config_for_search(self, search_job_id: int) -> AnalysisConfig:
        search = self.search_repo.get(search_job_id)
        filters = search.filters_json if search is not None else None
        if not isinstance(filters, dict):
            filters = None
        return AnalysisConfig.from_search_filters(
            profile=self.provider.profile, filters_json=filters
        )


def resolve_search_analysis_profile(search: SearchJob) -> str:
    filters = search.filters_json if isinstance(search.filters_json, dict) else {}
    profile = filters.get("analysis_profile")
    if isinstance(profile, str) and profile.strip():
        return profile.strip()
    return "default"
