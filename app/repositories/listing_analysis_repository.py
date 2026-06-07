from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_analysis import ALLOWED_ANALYSIS_STATUSES, ListingAnalysis
from app.models.listing_snapshot import ListingSnapshot


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ListingAnalysisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_latest_for_listing(
        self, external_id: str, profile: str | None = None
    ) -> ListingAnalysis | None:
        stmt = select(ListingAnalysis).where(ListingAnalysis.listing_external_id == external_id)
        if profile is not None:
            stmt = stmt.where(ListingAnalysis.profile == profile)
        return self.db.scalar(stmt.order_by(ListingAnalysis.created_at.desc(), ListingAnalysis.id.desc()))

    def get_latest_snapshot_for_listing(self, external_id: str) -> ListingSnapshot | None:
        return self.db.scalar(
            select(ListingSnapshot)
            .where(ListingSnapshot.external_id == external_id)
            .order_by(ListingSnapshot.observed_at.desc(), ListingSnapshot.id.desc())
        )

    def create_or_update_analysis(
        self,
        *,
        listing_external_id: str,
        snapshot_id: int | None,
        profile: str,
        status: str,
        analysis_version: str,
        input_hash: str,
        model_provider: str | None = None,
        model_name: str | None = None,
        score: float | None = None,
        verdict: str | None = None,
        facts_json: dict | None = None,
        risks_json: dict | None = None,
        questions_json: dict | None = None,
        report_md: str = "",
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> ListingAnalysis:
        self._validate_status(status)
        existing = self.db.scalar(
            select(ListingAnalysis).where(
                ListingAnalysis.listing_external_id == listing_external_id,
                ListingAnalysis.profile == profile,
                ListingAnalysis.analysis_version == analysis_version,
                ListingAnalysis.input_hash == input_hash,
            )
        )
        if existing is None:
            self._mark_previous_inputs_stale(
                listing_external_id=listing_external_id,
                profile=profile,
                analysis_version=analysis_version,
                input_hash=input_hash,
            )
            existing = ListingAnalysis(
                listing_external_id=listing_external_id,
                profile=profile,
                analysis_version=analysis_version,
                input_hash=input_hash,
            )
            self.db.add(existing)

        existing.snapshot_id = snapshot_id
        existing.status = status
        existing.model_provider = model_provider
        existing.model_name = model_name
        existing.score = score
        existing.verdict = verdict
        existing.facts_json = facts_json or {}
        existing.risks_json = risks_json or {}
        existing.questions_json = questions_json or {}
        existing.report_md = report_md
        existing.error_type = error_type
        existing.error_message = error_message
        existing.updated_at = _now()
        self.db.flush()
        return existing

    def list_alerted_listings_without_analysis(self, limit: int) -> list[Listing]:
        if limit <= 0:
            return []

        alerted = (
            select(
                AlertSent.listing_external_id.label("external_id"),
                func.min(AlertSent.id).label("first_alert_id"),
            )
            .group_by(AlertSent.listing_external_id)
            .subquery()
        )
        analyzed_external_ids = select(ListingAnalysis.listing_external_id).distinct()
        stmt = (
            select(Listing)
            .join(alerted, alerted.c.external_id == Listing.external_id)
            .where(Listing.external_id.not_in(analyzed_external_ids))
            .order_by(alerted.c.first_alert_id.asc(), Listing.id.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def mark_running(self, analysis: ListingAnalysis) -> ListingAnalysis:
        analysis.status = "running"
        analysis.error_type = None
        analysis.error_message = None
        analysis.updated_at = _now()
        self.db.flush()
        return analysis

    def mark_success(
        self,
        analysis: ListingAnalysis,
        *,
        score: float | None,
        verdict: str | None,
        facts_json: dict,
        risks_json: dict,
        questions_json: dict,
        report_md: str,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> ListingAnalysis:
        analysis.status = "success"
        analysis.score = score
        analysis.verdict = verdict
        analysis.facts_json = facts_json
        analysis.risks_json = risks_json
        analysis.questions_json = questions_json
        analysis.report_md = report_md
        analysis.model_provider = model_provider
        analysis.model_name = model_name
        analysis.error_type = None
        analysis.error_message = None
        analysis.updated_at = _now()
        self.db.flush()
        return analysis

    def mark_failed(
        self,
        analysis: ListingAnalysis,
        *,
        error_type: str,
        error_message: str,
    ) -> ListingAnalysis:
        analysis.status = "failed"
        analysis.error_type = error_type[:128]
        analysis.error_message = error_message
        analysis.updated_at = _now()
        self.db.flush()
        return analysis

    def _mark_previous_inputs_stale(
        self,
        *,
        listing_external_id: str,
        profile: str,
        analysis_version: str,
        input_hash: str,
    ) -> None:
        for analysis in self.db.scalars(
            select(ListingAnalysis).where(
                ListingAnalysis.listing_external_id == listing_external_id,
                ListingAnalysis.profile == profile,
                ListingAnalysis.analysis_version == analysis_version,
                ListingAnalysis.input_hash != input_hash,
                ListingAnalysis.status != "stale",
            )
        ):
            analysis.status = "stale"
            analysis.updated_at = _now()

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in ALLOWED_ANALYSIS_STATUSES:
            raise ValueError(f"unsupported listing analysis status: {status}")
