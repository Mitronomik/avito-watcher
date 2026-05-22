import fcntl

import pytest

from app.workers.monitor import MonitorWorkerLock, main


def test_monitor_worker_lock_first_acquisition_succeeds(tmp_path):
    lock_path = tmp_path / "monitor" / "monitor_worker.lock"

    with MonitorWorkerLock(str(lock_path)):
        assert lock_path.exists()


def test_monitor_worker_lock_second_acquisition_fails_while_held(tmp_path):
    lock_path = tmp_path / "monitor_worker.lock"

    with MonitorWorkerLock(str(lock_path)):
        with pytest.raises(SystemExit) as exc_info:
            with MonitorWorkerLock(str(lock_path)):
                pass

    assert exc_info.value.code == 1


def test_main_exits_when_lock_is_already_held(monkeypatch, tmp_path):
    lock_path = tmp_path / "monitor_worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held_lock = lock_path.open("a+")
    fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    monkeypatch.setattr("app.workers.monitor.settings.monitor_worker_lock_path", str(lock_path))

    init_db_called = False
    build_parser_called = False

    def _init_db_stub():
        nonlocal init_db_called
        init_db_called = True

    def _build_parser_stub():
        nonlocal build_parser_called
        build_parser_called = True
        return object()

    monkeypatch.setattr("app.workers.monitor.init_db", _init_db_stub)
    monkeypatch.setattr("app.workers.monitor._build_parser", _build_parser_stub)

    try:
        with pytest.raises(SystemExit) as exc_info:
            main()
    finally:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_UN)
        held_lock.close()

    assert exc_info.value.code == 1
    assert init_db_called is False
    assert build_parser_called is False
