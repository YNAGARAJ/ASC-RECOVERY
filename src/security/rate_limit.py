"""Rate limiting and account lockout.

`RateLimiter` is a token-bucket-per-key limiter abstracted behind a
Protocol (same port pattern as security.kms) so a Redis-backed adapter can
replace `InMemoryTokenBucketRateLimiter` without changing any caller -- the
in-memory adapter only enforces limits within a single process, which is
not sufficient once the app runs as more than one instance.

`AccountLockoutTracker` is a separate, purpose-built mechanism: N
consecutive failed login attempts locks the account out for a cooldown
period, independent of the general-purpose request rate limiter above.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class RateLimiter(Protocol):
    def allow(self, key: str) -> bool:
        """True if this call is allowed to proceed under the limit for `key`."""
        ...


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class InMemoryTokenBucketRateLimiter:
    """Dev/single-process adapter. Each key gets its own bucket of
    `capacity` tokens, refilling at `refill_per_second`; each `allow()`
    call that finds at least one token available consumes one and
    succeeds, otherwise it fails."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_per_second <= 0:
            raise ValueError("refill_per_second must be positive")
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self._capacity), last_refill=now)
            self._buckets[key] = bucket
        else:
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
            bucket.last_refill = now

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True
        return False


@dataclass
class _LoginAttempts:
    failures: int = 0
    locked_until: float | None = None


class AccountLockoutTracker:
    """Locks an account out after `max_failures` consecutive failed login
    attempts, for `lockout_seconds`. `record_success` resets the failure
    count -- a lockout only follows *consecutive* failures."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        lockout_seconds: float = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures <= 0:
            raise ValueError("max_failures must be positive")
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._clock = clock
        self._attempts: dict[str, _LoginAttempts] = {}

    def is_locked_out(self, account_id: str) -> bool:
        record = self._attempts.get(account_id)
        if record is None or record.locked_until is None:
            return False
        return self._clock() < record.locked_until

    def record_failure(self, account_id: str) -> None:
        record = self._attempts.setdefault(account_id, _LoginAttempts())
        record.failures += 1
        if record.failures >= self._max_failures:
            record.locked_until = self._clock() + self._lockout_seconds

    def record_success(self, account_id: str) -> None:
        self._attempts.pop(account_id, None)

    def current_failure_count(self, account_id: str) -> int:
        """Consecutive failures recorded so far -- feeds F-11's
        `observability.alerts.evaluate_auth_anomaly_alert`
        (docs/audit/REGISTER.md) a real count instead of nothing. Reuses
        this tracker's own bookkeeping rather than adding a second,
        separate counter alongside it."""
        record = self._attempts.get(account_id)
        return record.failures if record is not None else 0
