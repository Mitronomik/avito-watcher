from sqlalchemy import inspect

from app.db.base import Base
from app.repositories.listing_detail_snapshots import ListingDetailSnapshotRepository


def test_model_registered_and_table_shape(db_session):
    assert "listing_detail_snapshots" in Base.metadata.tables
    inspected_columns = inspect(db_session.bind).get_columns("listing_detail_snapshots")
    columns = {column["name"] for column in inspected_columns}
    for name in ("listing_external_id", "source_kind", "fetch_status", "parse_status", "content_hash", "raw_text_excerpt"):
        assert name in columns
    indexes = {idx["name"] for idx in inspect(db_session.bind).get_indexes("listing_detail_snapshots")}
    assert "ix_listing_detail_snapshots_listing_external_id" in indexes
    assert "ix_listing_detail_snapshots_parse_status" in indexes
    listing_id_column = next(column for column in inspected_columns if column["name"] == "listing_id")
    listing_external_id_column = next(column for column in inspected_columns if column["name"] == "listing_external_id")
    assert listing_id_column["nullable"] is True
    assert listing_external_id_column["nullable"] is False
    assert inspect(db_session.bind).get_foreign_keys("listing_detail_snapshots") == []
    constraints = inspect(db_session.bind).get_check_constraints("listing_detail_snapshots")
    assert not any("fetch_status" in str(c.get("sqltext")) or "parse_status" in str(c.get("sqltext")) for c in constraints)
    uniques = inspect(db_session.bind).get_unique_constraints("listing_detail_snapshots")
    assert any(u["name"] == "uq_listing_detail_snapshots_external_hash" for u in uniques)


def test_repository_idempotency_latest_limit_and_orphan_external_id(db_session):
    repo = ListingDetailSnapshotRepository(db_session)
    first, created = repo.create_or_get_snapshot(
        listing_external_id="ext-1",
        listing_id=None,
        source_kind="fixture",
        parse_status="success",
        fetch_status="not_applicable",
        parser_version="listing-detail-v1",
        content_hash="a" * 64,
        title="one",
    )
    duplicate, duplicate_created = repo.create_or_get_snapshot(
        listing_external_id="ext-1",
        listing_id=None,
        source_kind="fixture",
        parse_status="success",
        fetch_status="not_applicable",
        parser_version="listing-detail-v1",
        content_hash="a" * 64,
        title="one duplicate",
    )
    second, second_created = repo.create_or_get_snapshot(
        listing_external_id="ext-1",
        listing_id=None,
        source_kind="fixture",
        parse_status="partial",
        fetch_status="not_applicable",
        parser_version="listing-detail-v1",
        content_hash="b" * 64,
        title="two",
    )
    db_session.commit()

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert second_created is True
    assert second.id != first.id
    assert repo.get_latest_successful_snapshot("ext-1").id == second.id
    assert len(repo.list_snapshots_for_listing("ext-1", limit=1)) == 1
    assert len(repo.list_snapshots_for_listing("ext-1", limit=500)) == 2


def test_migration_file_is_reversible_and_minimal():
    text = open("alembic/versions/0011_listing_detail_snapshots.py", encoding="utf-8").read()
    assert "def upgrade" in text
    assert "def downgrade" in text
    assert "listing_analyses" not in text
    assert "alerts_sent" not in text
    assert "agent_tasks" not in text
