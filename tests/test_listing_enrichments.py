from sqlalchemy import inspect

from app.db.base import Base


def test_listing_enrichments_table_registered(db_session):
    assert "listing_enrichments" in Base.metadata.tables
    cols = {
        c["name"] for c in inspect(db_session.bind).get_columns("listing_enrichments")
    }
    for name in [
        "listing_external_id",
        "enrichment_type",
        "source_type",
        "source_id",
        "structured_facts_json",
        "field_confidence_json",
        "evidence_json",
        "input_hash",
        "output_hash",
    ]:
        assert name in cols
    uniques = inspect(db_session.bind).get_unique_constraints("listing_enrichments")
    assert any(u["name"] == "uq_listing_enrichments_success_identity" for u in uniques)


def test_migration_is_scoped():
    text = open("alembic/versions/0012_listing_enrichments.py", encoding="utf-8").read()
    assert "def downgrade" in text
    assert "listing_enrichments" in text
    for forbidden in [
        "listing_analyses",
        "alerts_sent",
        "knowledge_notes",
        "listing_search_matches",
        "search_jobs",
    ]:
        assert forbidden not in text
