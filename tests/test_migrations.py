import os
import subprocess
import uuid

import pytest
from sqlalchemy.engine import make_url


def test_alembic_upgrade_head_passes_on_postgresql():
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
