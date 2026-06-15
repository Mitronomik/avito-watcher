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
    assert "success_without_alert_sent" in page
    assert "non_success_with_alert_sent" in page
    assert "bad_payload_hash_count" in page
    assert "/admin/alerts/delivery-attempts/" in page
    assert "Agent tasks" in page and "review" in page
    assert "Analysis summary" in page
    assert "abc123" in page
    assert "actual-secret" not in page
    assert "payload_json" not in page and "result_json" not in page
