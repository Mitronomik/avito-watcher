"""Proxy pool with round-robin selection and exponential-backoff quarantine."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
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
        self._lock = asyncio.Lock()
        self._index = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_proxy(self) -> Optional[str]:
        """Return next available proxy URL, or None if all are quarantined."""
        async with self._lock:
            available = [p for p in self._proxies if p.is_available]
            if not available:
                return None
            entry = available[self._index % len(available)]
            self._index += 1
            return entry.url

    async def report_success(self, proxy_url: str) -> None:
        """Decrease failure counter for proxy_url (min 0)."""
        async with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.failures = max(0, p.failures - 1)
                    return

    async def report_failure(self, proxy_url: str) -> None:
        """Increment failure counter and quarantine proxy_url."""
        async with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.failures += 1
                    multiplier = min(p.failures, 3)
                    delay = self._quarantine_seconds * multiplier
                    p.quarantine_until = time.monotonic() + delay
                    return

    @property
    def total(self) -> int:
        return len(self._proxies)

    @property
    def available_count(self) -> int:
        return sum(1 for p in self._proxies if p.is_available)
