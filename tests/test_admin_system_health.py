from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import func, select, text

from app.models.agent_task import AgentTask
from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.search_job import SearchJob
from app.workers.status import build_worker_status
from tests.test_admin_ui import make_raw_client


def _client(monkeypatch, tmp_path, *, allow_query_api_key=False):
    status_path = tmp_path / "secret-token-worker-status.json"
    from app.core.config import settings

    monkeypatch.setattr(settings, "monitor_worker_status_path", str(status_path))
    monkeypatch.setattr(settings, "monitor_worker_stale_after_seconds", 60)
    return make_raw_client(monkeypatch, allow_query_api_key=allow_query_api_key)


def test_admin_system_auth_read_only_and_query_key_safety(monkeypatch, tmp_path):
    client, Session = _client(monkeypatch, tmp_path, allow_query_api_key=False)
    assert client.get("/admin/system").status_code == 403
    with Session() as s:
        s.add(SearchJob(name="active", source_url="https://www.avito.ru/x"))
        s.add(Listing(external_id="l1", url="https://www.avito.ru/1", title="t"))
        s.add(AlertDeliveryAttempt(listing_external_id="l1", channel="telegram", dedupe_key="telegram:new:l1", payload_hash="a" * 64, status="failed", last_error="Authorization: Bearer secret-token-value"))
        s.commit()
        before = {
            "search_jobs": s.scalar(select(func.count()).select_from(SearchJob)),
            "listings": s.scalar(select(func.count()).select_from(Listing)),
            "attempts": s.scalar(select(func.count()).select_from(AlertDeliveryAttempt)),
        }
    resp = client.get("/admin/system", headers={"X-API-Key": "read"})
    assert resp.status_code == 200
    assert "System health" in resp.text
    assert "<form" not in resp.text.lower()
    assert "api_key=" not in resp.text
    assert "secret-token-value" not in resp.text
    assert "secret-token-worker-status" not in resp.text
    assert client.post("/admin/system", headers={"X-API-Key": "tech"}).status_code in {404, 405}
    with Session() as s:
        after = {
            "search_jobs": s.scalar(select(func.count()).select_from(SearchJob)),
            "listings": s.scalar(select(func.count()).select_from(Listing)),
            "attempts": s.scalar(select(func.count()).select_from(AlertDeliveryAttempt)),
        }
    assert after == before


def test_admin_system_worker_status_states_and_parser_diagnostics(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    assert "Missing status file" in client.get("/admin/system", headers={"X-API-Key": "read"}).text
    status_path = tmp_path / "secret-token-worker-status.json"
    status_path.write_text("{bad", encoding="utf-8")
    corrupt = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Corrupt status file" in corrupt
    assert "Expecting property" in corrupt

    fresh = build_worker_status(
        cycle_started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        cycle_finished_at=datetime.now(timezone.utc),
        cycle_ok=True,
        searches_processed=2,
        result_count=7,
        parser_stats={"engine_used": "playwright", "proxy_failure_count": 1, "authorization": "secret"},
    )
    status_path.write_text(json.dumps(fresh), encoding="utf-8")
    ok = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Fresh" in ok
    assert "Cycle OK" in ok
    assert "engine_used" in ok and "playwright" in ok
    assert "proxy_failure_count" in ok

    failed = build_worker_status(
        cycle_started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        cycle_finished_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        cycle_ok=False,
        error="password=super-secret boom",
    )
    status_path.write_text(json.dumps(failed), encoding="utf-8")
    failed_page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Last cycle failed" in failed_page
    assert "super-secret" not in failed_page



def test_admin_system_redacts_apps_script_delivery_errors(monkeypatch, tmp_path):
    client, Session = _client(monkeypatch, tmp_path)
    deployment_id = "AKfycbx_unique_system_secret_deployment_id_123456789"
    apps_script_url = f"https://script.google.com/macros/s/{deployment_id}/exec"
    with Session() as s:
        s.add(
            AlertDeliveryAttempt(
                listing_external_id="gas",
                channel="jsonl",
                dedupe_key="jsonl:new:gas",
                payload_hash="c" * 64,
                status="failed",
                error_type="WebhookError",
                last_error=f"POST failed for {apps_script_url}: status=500",
            )
        )
        s.commit()

    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "WebhookError" in page
    assert "failed" in page
    assert "https://script.google.com/.../exec" in page
    assert deployment_id not in page
    assert apps_script_url not in page


def test_admin_system_delivery_invariants_agents_analyses_and_alembic(monkeypatch, tmp_path):
    client, Session = _client(monkeypatch, tmp_path)
    with Session() as s:
        s.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        s.execute(text("INSERT INTO alembic_version (version_num) VALUES ('abc123')"))
        s.add(SearchJob(name="err", source_url="https://www.avito.ru/x", last_error="boom"))
        s.add(AlertDeliveryAttempt(listing_external_id="ok", channel="telegram", dedupe_key="telegram:new:ok", payload_hash="b" * 64, status="success", sent_at=datetime.utcnow()))
        s.add(AlertSent(listing_external_id="bad", channel="telegram", dedupe_key="telegram:new:bad"))
        s.add(AlertDeliveryAttempt(listing_external_id="bad", channel="telegram", dedupe_key="telegram:new:bad", payload_hash="bad", status="failed", sent_at=datetime.utcnow(), search_name="manual_retry:test", error_type="Auth", last_error="api_key=actual-secret"))
        s.add(AgentTask(task_type="review", status="failed", dedupe_key="task1", error_type="Oops", payload_json={"token": "raw"}, result_json={"secret": "raw"}))
        s.add(ListingAnalysis(listing_external_id="bad", profile="default", status="failed", input_hash="h", error_type="LLM", error_message="token=actual-secret"))
        s.commit()
    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Delivery integrity issues (all time)" in page
    assert "Resolved delivery history (all time)" in page
    assert "Retry scheduling indicators (all time)" in page
    assert "success_without_alert_sent" in page
    assert "non_success_after_alert_sent" in page
    assert "resolved_non_success_with_later_alert_sent" in page
    assert "next_retry_at_non_null" in page
    assert "non_success_with_alert_sent" not in page
    assert "bad_payload_hash_count" in page
    assert "/admin/alerts/delivery-attempts/" in page
    assert "Agent tasks" in page and "review" in page
    assert "Analysis summary" in page
    assert "abc123" in page
    assert "actual-secret" not in page
    assert "payload_json" not in page and "result_json" not in page


def test_admin_system_monitor_cycle_history_redaction_stale_and_unknown(monkeypatch, tmp_path):
    from app.models.monitor_cycle_run import MonitorCycleRun

    client, Session = _client(monkeypatch, tmp_path)
    old_started = datetime.utcnow() - timedelta(hours=2)
    secret_error = (
        "https://script.google.com/macros/s/fake-secret-deployment-id/exec "
        "Authorization: Bearer fake-token api_key=fake-secret X-API-Key: fake-secret"
    )
    with Session() as s:
        s.add(MonitorCycleRun(started_at=old_started, status="running", worker_status_file="worker_status.json"))
        s.add(
            MonitorCycleRun(
                started_at=datetime.utcnow() - timedelta(minutes=5),
                finished_at=datetime.utcnow() - timedelta(minutes=4),
                duration_ms=1000,
                status="failed",
                searches_processed=0,
                searches_total=0,
                error_type="RuntimeError",
                last_error=secret_error,
                worker_status_file="/path/with/token/worker_status.json",
            )
        )
        s.commit()
        before = s.scalar(select(func.count()).select_from(MonitorCycleRun))
    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Monitor cycle history" in page
    assert "last 24h cycles total" in page
    assert "stale running" in page
    assert "possible crash" in page
    assert "unknown" in page
    assert "fake-secret-deployment-id" not in page
    assert "fake-token" not in page
    assert "fake-secret" not in page
    assert "worker_status.json" in page
    assert "/path/with/token" not in page
    with Session() as s:
        after = s.scalar(select(func.count()).select_from(MonitorCycleRun))
    assert after == before

def test_admin_system_backup_restore_retention_readiness_policy_only(monkeypatch, tmp_path):
    client, Session = _client(monkeypatch, tmp_path)
    with Session() as s:
        s.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        s.execute(text("INSERT INTO alembic_version (version_num) VALUES ('rev_pr22a')"))
        s.add(SearchJob(name="active", source_url="https://www.avito.ru/x"))
        s.add(Listing(external_id="vol", url="https://www.avito.ru/vol", title="volume"))
        s.commit()
    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Готовность backup / restore / retention" in page
    assert "docs/ops/backup_restore_retention_policy.md" in page
    assert "Restore procedure" in page and "documented" in page
    assert "Retention mode" in page and "policy-only" in page
    assert "Retention execution" in page and "disabled / not implemented" in page
    assert "Retention dry-run" in page and "available / read-only" in page
    assert "Latest backup" in page and "unknown" in page
    assert "Backup metadata source" in page and "not configured" in page
    assert "Data volume summary" in page and "listings: 1" in page and "search_jobs: 1" in page
    assert "Alembic" in page and "rev_pr22a" in page
    for existing in [
        "Overall status",
        "Worker cycle status",
        "Parser diagnostics",
        "Search jobs",
        "Alert Delivery health",
        "Delivery integrity issues (all time)",
        "Resolved delivery history (all time)",
        "Recent failed delivery attempts",
        "Agent tasks",
        "Analysis summary",
        "Monitor cycle history",
    ]:
        assert existing in page


def test_admin_system_backup_restore_retention_has_no_destructive_ui_or_secret_paths(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)
    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    lower = page.lower()
    assert "<form" not in lower
    assert "<button" not in lower
    for forbidden in [
        "Run backup",
        "Restore now",
        "Delete old data",
        "Apply retention",
        "Archive now",
        "Truncate",
        "Run retention",
        "Purge old data",
        "Execute retention",
        "Удалить",
        "Архивировать",
        "Очистить",
        "Запустить очистку",
    ]:
        assert forbidden.lower() not in lower
    for leaked in [
        ".env",
        "DATABASE_URL",
        "postgres://",
        "/home/",
        "/root/",
        "/var/lib/",
        "AKfycbx_fake_token_like_value",
        "script.google.com/macros/s/fake/exec",
        "webhook",
        "Authorization: Bearer",
        "X-API-Key:",
    ]:
        assert leaked.lower() not in lower
    assert client.post("/admin/system", headers={"X-API-Key": "read"}).status_code in {404, 405}


def test_admin_system_retention_dry_run_counts_and_safety(monkeypatch, tmp_path):
    from app.models.listing_detail_snapshot import ListingDetailSnapshot
    from app.models.listing_enrichment import ListingEnrichment
    from app.models.monitor_cycle_run import MonitorCycleRun
    from app.services.retention_dry_run import get_retention_dry_run_report

    client, Session = _client(monkeypatch, tmp_path)
    old_200 = datetime.utcnow() - timedelta(days=200)
    old_100 = datetime.utcnow() - timedelta(days=100)
    recent = datetime.utcnow() - timedelta(days=1)
    with Session() as s:
        s.add(AlertDeliveryAttempt(listing_external_id="old", channel="telegram", dedupe_key="telegram:new:old", payload_hash="d" * 64, status="failed", created_at=old_100))
        s.add(AlertDeliveryAttempt(listing_external_id="new", channel="telegram", dedupe_key="telegram:new:new", payload_hash="e" * 64, status="failed", created_at=recent))
        s.add(MonitorCycleRun(started_at=old_100, status="success"))
        s.add(MonitorCycleRun(started_at=recent, status="success"))
        s.add(AgentTask(task_type="review", status="success", dedupe_key="old-success", created_at=old_200))
        s.add(AgentTask(task_type="review", status="running", dedupe_key="old-running", created_at=old_200))
        s.add(AgentTask(task_type="review", status="failed", dedupe_key="new-failed", created_at=recent))
        s.add(ListingDetailSnapshot(listing_external_id="snap-old", source_kind="detail", content_hash="f" * 64, created_at=old_200))
        s.add(ListingDetailSnapshot(listing_external_id="snap-new", source_kind="detail", content_hash="g" * 64, created_at=recent))
        s.add(ListingEnrichment(listing_external_id="en-old", enrichment_type="x", source_type="snapshot", source_id=1, status="success", validation_status="valid", prompt_version="p", schema_version="s", extraction_profile="e", input_hash="h" * 64, source_content_hash="i" * 64, output_hash="j" * 64, created_at=old_200))
        s.add(ListingEnrichment(listing_external_id="en-new", enrichment_type="x", source_type="snapshot", source_id=2, status="success", validation_status="valid", prompt_version="p", schema_version="s", extraction_profile="e", input_hash="k" * 64, source_content_hash="l" * 64, output_hash="m" * 64, created_at=recent))
        s.commit()

    with Session() as s:
        report = get_retention_dry_run_report(s, datetime.utcnow())
    by_table = {row.table_name: row for row in report.rows}
    assert by_table["alert_delivery_attempts"].dry_run_candidate_count == 1
    assert by_table["alert_delivery_attempts"].total_count == 2
    assert by_table["monitor_cycle_runs"].dry_run_candidate_count == 1
    assert by_table["monitor_cycle_runs"].total_count == 2
    assert by_table["agent_tasks"].dry_run_candidate_count == 1
    assert by_table["agent_tasks"].total_count == 3
    assert by_table["listing_detail_snapshots"].dry_run_candidate_count == 1
    assert by_table["listing_detail_snapshots"].total_count == 2
    assert by_table["listing_enrichments"].dry_run_candidate_count == 1
    assert by_table["listing_enrichments"].total_count == 2

    page = client.get("/admin/system", headers={"X-API-Key": "read"}).text
    assert "Dry-run отчёт по retention" in page
    assert "Dry-run only." in page
    assert "No rows are deleted, archived, updated, or scheduled for deletion" in page
    assert "dry_run_candidate_count" in page
    assert "alert_delivery_attempts" in page and "monitor_cycle_runs" in page
    assert "listing_detail_snapshots" in page and "listing_enrichments" in page
    assert "agent_tasks" in page and "Terminal statuses only" in page
    assert "started_at" in page
    assert "old-running" not in page and "snap-old" not in page and "en-old" not in page
    assert "telegram:new:old" not in page
    assert "payload_json" not in page and "attributes_json" not in page
    assert "DELETE" not in page and "ARCHIVE" not in page
    assert "<form" not in page.lower() and "<button" not in page.lower()


def test_retention_dry_run_helper_empty_supported_tables_are_zero(monkeypatch, tmp_path):
    from app.services.retention_dry_run import get_retention_dry_run_report

    _, Session = _client(monkeypatch, tmp_path)
    with Session() as s:
        report = get_retention_dry_run_report(s, datetime.utcnow())
    by_table = {row.table_name: row for row in report.rows}
    assert by_table["alert_delivery_attempts"].dry_run_candidate_count == 0
    assert by_table["monitor_cycle_runs"].dry_run_candidate_count == 0
    assert by_table["agent_tasks"].dry_run_candidate_count == 0
    assert by_table["listing_detail_snapshots"].dry_run_candidate_count == 0
    assert by_table["listing_enrichments"].dry_run_candidate_count == 0
    assert all(row.status == "supported" for row in report.rows)
    assert all(row.dry_run_candidate_count is not None for row in report.rows)
    assert all(not hasattr(row, "ids") for row in report.rows)
    assert all("DELETE" not in row.notes.upper() and "ARCHIVE" not in row.notes.upper() for row in report.rows)
