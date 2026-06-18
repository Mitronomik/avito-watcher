import os
import subprocess
import uuid

import pytest
from pathlib import Path
from sqlalchemy.engine import make_url


def test_alembic_upgrade_head_passes_on_postgresql():
    if os.getenv("RUN_ALEMBIC_SMOKE") != "1":
        pytest.skip(
            "set RUN_ALEMBIC_SMOKE=1 to run the PostgreSQL migration smoke test; "
            "GitHub CI already runs alembic upgrade head in a dedicated step"
        )

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("PostgreSQL DATABASE_URL is required for migration smoke test")

    psycopg = pytest.importorskip("psycopg")
    url = make_url(database_url)
    maintenance_url = url.set(drivername="postgresql", database="postgres")
    test_database = f"avito_watcher_alembic_{uuid.uuid4().hex}"
    test_url = url.set(database=test_database)

    try:
        with psycopg.connect(maintenance_url.render_as_string(hide_password=False), autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{test_database}"')
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not available for migration smoke test: {exc}")

    env = {**os.environ, "DATABASE_URL": test_url.render_as_string(hide_password=False)}
    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            check=False,
            env=env,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        with psycopg.connect(maintenance_url.render_as_string(hide_password=False), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity WHERE datname = %s",
                (test_database,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{test_database}"')


def test_pr38_migration_has_upgrade_and_downgrade_and_expected_operations():
    migration = Path("alembic/versions/0018_agent_task_orchestration_metadata.py").read_text()
    assert "def upgrade()" in migration
    assert "def downgrade()" in migration
    columns = (
        "orchestration_run_id",
        "workflow_id",
        "parent_task_id",
        "depends_on_task_id",
        "chain_depth",
        "blocking",
        "dependency_status",
        "orchestration_status",
    )
    indexes = (
        "ix_agent_tasks_orchestration_run_id",
        "ix_agent_tasks_workflow_id",
        "ix_agent_tasks_parent_task_id",
        "ix_agent_tasks_depends_on_task_id",
        "ix_agent_tasks_orchestration_status",
        "ix_agent_tasks_dependency_status",
    )
    fks = (
        "fk_agent_tasks_parent_task_id",
        "fk_agent_tasks_depends_on_task_id",
    )
    checks = (
        "ck_agent_tasks_dependency_status",
        "ck_agent_tasks_orchestration_status",
        "ck_agent_tasks_chain_depth_non_negative",
        "ck_agent_tasks_parent_not_self",
        "ck_agent_tasks_dependency_not_self",
    )

    assert "op.create_check_constraint" in migration
    for name in (*columns, *indexes, *fks, *checks):
        assert name in migration
    for name in indexes:
        assert f'op.drop_index("{name}"' in migration
    for name in fks:
        assert f'op.drop_constraint("{name}"' in migration
    for name in checks:
        assert f'op.drop_constraint("{name}"' in migration
    for name in columns:
        assert f'op.drop_column("agent_tasks", "{name}")' in migration


def test_agent_artifacts_migration_file_contains_required_constraints_indexes_and_downgrade():
    text = Path("alembic/versions/0019_agent_artifacts.py").read_text(encoding="utf-8")
    required = [
        'op.create_table(\n        "agent_artifacts"',
        "ck_agent_artifacts_artifact_type",
        "ck_agent_artifacts_redaction_status",
        "ck_agent_artifacts_input_hash_not_empty",
        "ck_agent_artifacts_content_hash_not_empty",
        "ck_agent_artifacts_schema_version_not_empty",
        "ix_agent_artifacts_artifact_type",
        "ix_agent_artifacts_listing_external_id",
        "ix_agent_artifacts_listing_analysis_id",
        "ix_agent_artifacts_search_job_id",
        "ix_agent_artifacts_context_key",
        "ix_agent_artifacts_source_task_id",
        "ix_agent_artifacts_orchestration_run_id",
        "ix_agent_artifacts_input_hash",
        "ix_agent_artifacts_content_hash",
        "ix_agent_artifacts_created_at",
        "ix_agent_artifacts_context_latest",
        'sa.ForeignKey("agent_tasks.id")',
        "op.drop_index",
        'op.drop_table("agent_artifacts")',
    ]
    for expected in required:
        assert expected in text
