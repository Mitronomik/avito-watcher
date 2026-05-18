"""Tests for ProxyManager round-robin, quarantine, and observability counters."""
import time


from app.parsers.proxy_manager import ProxyManager


def make_pool(*urls: str, quarantine_seconds: int = 7200) -> ProxyManager:
    return ProxyManager(list(urls), quarantine_seconds=quarantine_seconds)


def test_get_proxy_returns_first_proxy():
    pm = make_pool("http://a:b@1.1.1.1:8000", "http://a:b@2.2.2.2:8000")

    first = pm.get_proxy()

    assert first in {"http://a:b@1.1.1.1:8000", "http://a:b@2.2.2.2:8000"}


def test_get_proxy_returns_none_when_empty():
    pm = make_pool()

    assert pm.get_proxy() is None


def test_report_failure_quarantines_proxy():
    pm = make_pool("http://a:b@1.1.1.1:8000")
    pm.report_failure("http://a:b@1.1.1.1:8000")

    assert pm.available_count == 0
    assert pm.get_proxy() is None


def test_report_success_decreases_failure_count():
    """report_success() decrements failures counter by 1 (min 0)."""
    pm = make_pool("http://a:b@1.1.1.1:8000")
    # Manually set failures=2 to verify decrement (not reset-to-zero)
    pm._proxies[0].failures = 2

    pm.report_success("http://a:b@1.1.1.1:8000")

    # report_success does: p.failures = max(0, p.failures - 1)
    assert pm._proxies[0].failures == 1


def test_quarantine_duration_grows_with_failures():
    pm = make_pool("http://a:b@1.1.1.1:8000", quarantine_seconds=100)
    url = "http://a:b@1.1.1.1:8000"

    pm.report_failure(url)
    first_until = pm._proxies[0].quarantine_until

    pm._proxies[0].quarantine_until = 0.0  # un-quarantine manually
    pm.report_failure(url)
    second_until = pm._proxies[0].quarantine_until

    # second quarantine must be longer than first (multiplier grows)
    assert second_until > first_until


def test_stats_tracks_hits_and_failures():
    pm = make_pool("http://a:b@1.1.1.1:8000", "http://a:b@2.2.2.2:8000")

    pm.get_proxy()
    pm.get_proxy()
    pm.report_failure("http://a:b@1.1.1.1:8000")
    pm.report_success("http://a:b@2.2.2.2:8000")

    s = pm.stats()
    assert s["total"] == 2
    assert s["hits"] == 2
    assert s["failures"] == 1
    assert s["quarantine_events"] == 1
    assert s["quarantined"] == 1
    assert s["available"] == 1


def test_available_count_respects_quarantine_expiry():
    pm = make_pool("http://a:b@1.1.1.1:8000", quarantine_seconds=0)
    pm.report_failure("http://a:b@1.1.1.1:8000")
    # quarantine_seconds=0 → quarantine_until = now + 0 → already expired
    time.sleep(0.01)

    assert pm.available_count == 1
