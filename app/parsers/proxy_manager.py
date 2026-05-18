"""Proxy pool with round-robin selection and exponential-backoff quarantine."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List, Optional

__all__ = ["ProxyManager"]


@dataclass
class _ProxyEntry:
    url: str
    failures: int = 0
    quarantine_until: float = 0.0

    @property
    def is_available(self) -> bool:
        return time.monotonic() > self.quarantine_until


class ProxyManager:
    """Round-robin proxy pool with per-IP failure tracking and quarantine.

    Quarantine duration grows with consecutive failures:
      failures=1 → base_sec * 1
      failures=2 → base_sec * 2
      failures=3+ → base_sec * 3  (capped)
    When all proxies are quarantined, get_proxy() returns None
    and the caller falls back to direct (no-proxy) mode.
    """

    def __init__(
        self,
        proxy_urls: List[str],
        quarantine_seconds: int = 7200,
    ) -> None:
        self._proxies = [_ProxyEntry(url=u) for u in proxy_urls]
        self._quarantine_seconds = quarantine_seconds
        self._lock = threading.Lock()
        self._index = 0
        # Observability counters — cumulative since process start
        self._hits: int = 0          # total successful proxy uses
        self._failures: int = 0      # total failures reported
        self._quarantine_events: int = 0  # total times any proxy was quarantined

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_proxy(self) -> Optional[str]:
        """Return next available proxy URL, or None if all are quarantined."""
        with self._lock:
            available = [p for p in self._proxies if p.is_available]
            if not available:
                return None
            entry = available[self._index % len(available)]
            self._index += 1
            self._hits += 1
            return entry.url

    def report_success(self, proxy_url: str) -> None:
        """Decrease failure counter for proxy_url (min 0)."""
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.failures = max(0, p.failures - 1)
                    return

    def report_failure(self, proxy_url: str) -> None:
        """Increment failure counter and quarantine proxy_url."""
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.failures += 1
                    multiplier = min(p.failures, 3)
                    delay = self._quarantine_seconds * multiplier
                    p.quarantine_until = time.monotonic() + delay
                    self._failures += 1
                    self._quarantine_events += 1
                    return

    @property
    def total(self) -> int:
        return len(self._proxies)

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._proxies if p.is_available)

    def stats(self) -> dict:
        """Return current pool health snapshot for logging/metrics."""
        with self._lock:
            return {
                "total": self.total,
                "available": self.available_count,
                "quarantined": self.total - self.available_count,
                "hits": self._hits,
                "failures": self._failures,
                "quarantine_events": self._quarantine_events,
            }
