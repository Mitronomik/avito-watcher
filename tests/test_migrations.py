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


def test_pr38_migration_has_upgrade_and_downgrade_and_expected_columns():
    migration = Path("alembic/versions/0018_agent_task_orchestration_metadata.py").read_text()
    assert "def upgrade()" in migration
    assert "def downgrade()" in migration
    for name in (
        "orchestration_run_id",
        "workflow_id",
        "parent_task_id",
        "depends_on_task_id",
        "chain_depth",
        "blocking",
        "dependency_status",
        "orchestration_status",
    ):
        assert name in migration
