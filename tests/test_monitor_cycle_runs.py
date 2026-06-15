from datetime import datetime, timedelta

from sqlalchemy import inspect

from app.models.monitor_cycle_run import (
    MONITOR_CYCLE_STATUS_FAILED,
    MONITOR_CYCLE_STATUS_RUNNING,
    MONITOR_CYCLE_STATUS_SUCCESS,
    MONITOR_CYCLE_STATUSES,
    MonitorCycleRun,
)
from app.services.monitor_cycle_runs import (
    MonitorCycleRunMetrics,
    MonitorCycleRunService,
    collect_cycle_metrics,
    sanitize_monitor_cycle_error,
)


def test_monitor_cycle_run_model_registration_and_indexes(db_session):
    inspector = inspect(db_session.bind)
    assert "monitor_cycle_runs" in inspector.get_table_names()
    index_names = {idx["name"] for idx in inspector.get_indexes("monitor_cycle_runs")}
    assert "ix_monitor_cycle_runs_started_at" in index_names
    assert "ix_monitor_cycle_runs_status_started_at" in index_names
    assert MONITOR_CYCLE_STATUS_RUNNING in MONITOR_CYCLE_STATUSES
    assert MONITOR_CYCLE_STATUS_SUCCESS in MONITOR_CYCLE_STATUSES
    assert MONITOR_CYCLE_STATUS_FAILED in MONITOR_CYCLE_STATUSES


def test_monitor_cycle_start_and_finish_success_nullable_metrics(db_session):
    service = MonitorCycleRunService(session_factory=lambda: db_session)
    started = datetime.utcnow() - timedelta(seconds=2)
    run_id = service.start_cycle(started_at=started, worker_status_file="/secret/path/worker_status.json")
    row = db_session.get(MonitorCycleRun, run_id)
    assert row.status == "running"
    assert row.started_at == started
    assert row.finished_at is None
    assert row.analysis_failed is None
    assert row.worker_status_file == "worker_status.json"

    service.finish_cycle(
        run_id,
        status="success",
        finished_at=datetime.utcnow(),
        metrics=MonitorCycleRunMetrics(searches_processed=2, searches_total=2, searches_failed=0, alerts_sent_created=0),
    )
    row = db_session.get(MonitorCycleRun, run_id)
    assert row.status == "success"
    assert row.finished_at is not None
    assert row.duration_ms is not None
    assert row.searches_processed == 2
    assert row.alerts_sent_created == 0
    assert row.analysis_failed is None


def test_monitor_cycle_finish_partial_when_search_failures(db_session):
    service = MonitorCycleRunService(session_factory=lambda: db_session)
    run_id = service.start_cycle()
    service.finish_cycle(run_id, status="success", metrics=MonitorCycleRunMetrics(searches_failed=1))
    assert db_session.get(MonitorCycleRun, run_id).status == "partial"


def test_monitor_cycle_finish_failed_sanitizes_error(db_session):
    service = MonitorCycleRunService(session_factory=lambda: db_session)
    run_id = service.start_cycle()
    secret = (
        "Traceback (most recent call last):\n"
        "https://script.google.com/macros/s/fake-secret-deployment-id/exec\n"
        "Bearer fake-token api_key=fake-secret Authorization: Bearer fake-token X-API-Key: fake-secret"
    )
    service.finish_cycle(run_id, status="failed", error=RuntimeError(secret))
    row = db_session.get(MonitorCycleRun, run_id)
    assert row.status == "failed"
    assert row.error_type == "RuntimeError"
    assert "Traceback" not in row.last_error
    assert "fake-secret-deployment-id" not in row.last_error
    assert "fake-token" not in row.last_error
    assert "fake-secret" not in row.last_error
    assert "redacted" in row.last_error.lower()


def test_monitor_cycle_ledger_failure_isolation(caplog):
    class BrokenSession:
        def __enter__(self):
            raise RuntimeError("api_key=fake-secret")
        def __exit__(self, exc_type, exc, tb):
            return False

    service = MonitorCycleRunService(session_factory=lambda: BrokenSession())
    assert service.start_cycle() is None
    service.finish_cycle(None, status="success")
    assert "fake-secret" not in caplog.text


def test_collect_cycle_metrics_one_top_level_result_list():
    metrics = collect_cycle_metrics([
        {"search": "a", "new": 1, "updated": 0},
        {"search": "b", "error": "boom"},
    ], started_at=datetime.utcnow())
    assert metrics.searches_total == 2
    assert metrics.searches_processed == 2
    assert metrics.searches_failed == 1
    assert metrics.listings_created == 1
    assert metrics.analysis_failed is None


def test_sanitize_monitor_cycle_error_redacts_required_secrets():
    text = sanitize_monitor_cycle_error(
        "https://script.google.com/macros/s/fake-secret-deployment-id/exec "
        "Bearer fake-token api_key=fake-secret Authorization: Bearer fake-token X-API-Key: fake-secret"
    )
    assert "fake-secret-deployment-id" not in text
    assert "fake-token" not in text
    assert "fake-secret" not in text
