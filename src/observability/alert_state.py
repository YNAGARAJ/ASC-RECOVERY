"""Stateful, in-memory per-key counters feeding the pure evaluators in
observability.alerts with real, bounded, recent counts (F-11,
docs/audit/REGISTER.md) -- a threshold evaluator fed an ever-growing
lifetime total would trip exactly once, forever, the moment it's ever
exceeded, so every count here is scoped to a rolling time window instead.

Same in-memory/single-process scope note as security.rate_limit's
InMemoryTokenBucketRateLimiter/AccountLockoutTracker: a shared store is
needed before running more than one API replica, not built here.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RollingWindowCounter:
    """How many times `record(key)` was called for a given key within
    the last `window_seconds`, as of now."""

    def __init__(
        self, *, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def record(self, key: str) -> int:
        window = self._hits.setdefault(key, deque())
        window.append(self._clock())
        return self._prune(window)

    def count(self, key: str) -> int:
        """Current count within the window, without recording a new hit."""
        window = self._hits.get(key)
        if window is None:
            return 0
        return self._prune(window)

    def _prune(self, window: deque[float]) -> int:
        cutoff = self._clock() - self._window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        return len(window)


class IngestionOutcomeTracker:
    """Per-tenant rolling window of ingestion outcomes, feeding
    `observability.alerts.evaluate_ingestion_failure_alert` a real
    quarantined/total ratio instead of nothing."""

    def __init__(
        self, *, window_seconds: float = 3600, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._total = RollingWindowCounter(window_seconds=window_seconds, clock=clock)
        self._quarantined = RollingWindowCounter(window_seconds=window_seconds, clock=clock)

    def record(self, tenant_id: str, *, quarantined: bool) -> tuple[int, int]:
        """Returns (quarantined_count, total_count) within the window,
        after recording this outcome."""
        total = self._total.record(tenant_id)
        if quarantined:
            quarantined_count = self._quarantined.record(tenant_id)
        else:
            quarantined_count = self._quarantined.count(tenant_id)
        return quarantined_count, total
