from datetime import datetime, timedelta, timezone
import json
import os

from app.workers.status import (
    build_worker_status,
    read_worker_status,
    summarize_worker_status,
    write_worker_status_atomic,
)


def _dt(second: int = 0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, second, tzinfo=timezone.utc)


def test_worker_status_write_creates_valid_json(tmp_path):
    path = tmp_path / "nested" / "worker_status.json"
    payload = build_worker_status(
        cycle_started_at=_dt(0),
        cycle_finished_at=_dt(1),
        cycle_ok=True,
        searches_processed=2,
        result_count=2,
        parser_stats={"engine_used": "camoufox", "browser_driver_crash_count": 1},
    )

    write_worker_status_atomic(path, payload)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cycle_ok"] is True
    assert data["searches_processed"] == 2
    assert data["engine_used"] == "camoufox"
    assert data["browser_driver_crash_count"] == 1
    assert "\n  \"browser_driver_crash_count\"" in path.read_text(encoding="utf-8")


def test_worker_status_write_is_atomic_or_uses_replace(monkeypatch, tmp_path):
    path = tmp_path / "worker_status.json"
    calls = []
    real_replace = os.replace

    def tracking_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr("app.workers.status.os.replace", tracking_replace)

    write_worker_status_atomic(path, {"updated_at": "2026-01-01T12:00:00Z"})

    assert calls
    assert calls[0][1] == path
    assert path.exists()


def test_worker_status_read_missing_returns_missing_state(tmp_path):
    status = read_worker_status(tmp_path / "missing.json")

    assert status["state"] == "missing"
    assert status["payload"] is None


def test_worker_status_read_corrupt_returns_corrupt_state(tmp_path):
    path = tmp_path / "worker_status.json"
    path.write_text("{not json", encoding="utf-8")

    status = read_worker_status(path)

    assert status["state"] == "corrupt"
    assert status["payload"] is None
    assert status["error"]


def test_worker_status_read_invalid_utf8_returns_corrupt_state(tmp_path):
    path = tmp_path / "worker_status.json"
    path.write_bytes(b"\xff\xfe\x00\x00")

    status = read_worker_status(path)

    assert status["state"] == "corrupt"
    assert status["payload"] is None
    assert status["error"]


def test_worker_status_summary_fresh_ok(tmp_path):
    status = {
        "state": "exists",
        "path": str(tmp_path / "worker_status.json"),
        "payload": {"updated_at": "2026-01-01T12:00:00Z", "cycle_ok": True},
        "error": "",
    }

    summary = summarize_worker_status(
        status,
        now=_dt(30),
        stale_after_seconds=180,
    )

    assert summary["age_seconds"] == 30
    assert summary["stale"] is False
    assert summary["badge"] == {"label": "Fresh", "color": "green"}


def test_worker_status_summary_stale(tmp_path):
    status = {
        "state": "exists",
        "path": str(tmp_path / "worker_status.json"),
        "payload": {"updated_at": "2026-01-01T12:00:00Z", "cycle_ok": True},
        "error": "",
    }

    summary = summarize_worker_status(
        status,
        now=_dt(0) + timedelta(seconds=181),
        stale_after_seconds=180,
    )

    assert summary["stale"] is True
    assert summary["badge"] == {"label": "Stale", "color": "yellow"}


def test_worker_status_summary_failed_cycle(tmp_path):
    status = {
        "state": "exists",
        "path": str(tmp_path / "worker_status.json"),
        "payload": {"updated_at": "2026-01-01T12:00:00Z", "cycle_ok": False},
        "error": "",
    }

    summary = summarize_worker_status(
        status,
        now=_dt(10),
        stale_after_seconds=180,
    )

    assert summary["cycle_ok"] is False
    assert summary["badge"] == {"label": "Last cycle failed", "color": "red"}


def test_worker_status_does_not_include_secret_like_values():
    payload = build_worker_status(
        cycle_started_at=_dt(0),
        cycle_finished_at=_dt(1),
        cycle_ok=False,
        error=RuntimeError(
            "failed url=https://user:password@example.com/path token=secret"
        ),
        parser_stats={
            "engine_used": "camoufox",
            "proxy_urls": "http://user:pass@proxy.example:8080",
            "api_key": "secret-key",
        },
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "user:password" not in serialized
    assert "http://user:pass@proxy.example:8080" not in serialized
    assert "secret-key" not in serialized
    assert "token=secret" not in serialized
    assert "token=[redacted]" in serialized
    assert "[redacted-url]" in serialized
    assert len(payload["cycle_error"]) <= 500
