from pathlib import Path


def test_alert_sent_created_at_migration_has_upgrade_and_downgrade():
    migration = Path("alembic/versions/0009_alert_sent_created_at.py").read_text()

    assert 'revision = "0009_alert_sent_created_at"' in migration
    assert 'down_revision = "0008_agent_tasks"' in migration
    assert '"created_at"' in migration
    assert "UPDATE alerts_sent SET created_at = CURRENT_TIMESTAMP" in migration
    assert 'op.drop_column("alerts_sent", "created_at")' in migration
