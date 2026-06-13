from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.listing_enrichment import ListingEnrichment


class ListingEnrichmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_success_by_identity(
        self,
        *,
        enrichment_type: str,
        source_type: str,
        source_id: int,
        model: str,
        prompt_version: str,
        schema_version: str,
        extraction_profile: str,
        input_hash: str,
    ) -> ListingEnrichment | None:
        return self.db.scalar(
            select(ListingEnrichment).where(
                ListingEnrichment.enrichment_type == enrichment_type,
                ListingEnrichment.source_type == source_type,
                ListingEnrichment.source_id == source_id,
                ListingEnrichment.model == model,
                ListingEnrichment.prompt_version == prompt_version,
                ListingEnrichment.schema_version == schema_version,
                ListingEnrichment.extraction_profile == extraction_profile,
                ListingEnrichment.input_hash == input_hash,
                ListingEnrichment.status == "success",
            )
        )

    def create_success_or_get(self, **kwargs) -> tuple[ListingEnrichment, bool]:
        existing = self.get_success_by_identity(
            enrichment_type=kwargs["enrichment_type"],
            source_type=kwargs["source_type"],
            source_id=kwargs["source_id"],
            model=kwargs["model"],
            prompt_version=kwargs["prompt_version"],
            schema_version=kwargs["schema_version"],
            extraction_profile=kwargs["extraction_profile"],
            input_hash=kwargs["input_hash"],
        )
        if existing is not None:
            return existing, False
        try:
            with self.db.begin_nested():
                row = ListingEnrichment(**kwargs)
                self.db.add(row)
                self.db.flush()
            return row, True
        except IntegrityError:
            existing = self.get_success_by_identity(
                enrichment_type=kwargs["enrichment_type"],
                source_type=kwargs["source_type"],
                source_id=kwargs["source_id"],
                model=kwargs["model"],
                prompt_version=kwargs["prompt_version"],
                schema_version=kwargs["schema_version"],
                extraction_profile=kwargs["extraction_profile"],
                input_hash=kwargs["input_hash"],
            )
            if existing is None:
                raise
            return existing, False
