"""Tests for the in-memory token-bucket rate limiter and the account
lockout tracker. Both use an injected fake clock so timing behavior is
deterministic -- no real sleeps.
"""

from __future__ import annotations

import pytest

from security.rate_limit import AccountLockoutTracker, InMemoryTokenBucketRateLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- InMemoryTokenBucketRateLimiter ---------------------------------------------


def test_allows_up_to_capacity_then_denies() -> None:
    clock = _FakeClock()
    limiter = InMemoryTokenBucketRateLimiter(capacity=3, refill_per_second=1, clock=clock)

    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is False


def test_refills_over_time() -> None:
    clock = _FakeClock()
    limiter = InMemoryTokenBucketRateLimiter(capacity=1, refill_per_second=1, clock=clock)

    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is False

    clock.advance(1.0)
    assert limiter.allow("user-1") is True


def test_does_not_refill_past_capacity() -> None:
    clock = _FakeClock()
    limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_per_second=100, clock=clock)

    clock.advance(1000)  # would refill far past capacity if unclamped
    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is True
    assert limiter.allow("user-1") is False


def test_different_keys_are_independent() -> None:
    clock = _FakeClock()
    limiter = InMemoryTokenBucketRateLimiter(capacity=1, refill_per_second=1, clock=clock)

    assert limiter.allow("user-a") is True
    assert limiter.allow("user-a") is False
    assert limiter.allow("user-b") is True


def test_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError, match="capacity"):
        InMemoryTokenBucketRateLimiter(capacity=0, refill_per_second=1)


# --- AccountLockoutTracker -------------------------------------------------------


def test_not_locked_out_initially() -> None:
    tracker = AccountLockoutTracker(max_failures=3, lockout_seconds=60)
    assert tracker.is_locked_out("user-1") is False


def test_not_locked_out_below_the_failure_threshold() -> None:
    clock = _FakeClock()
    tracker = AccountLockoutTracker(max_failures=3, lockout_seconds=60, clock=clock)

    tracker.record_failure("user-1")
    tracker.record_failure("user-1")
    assert tracker.is_locked_out("user-1") is False


def test_locked_out_at_the_failure_threshold() -> None:
    clock = _FakeClock()
    tracker = AccountLockoutTracker(max_failures=3, lockout_seconds=60, clock=clock)

    tracker.record_failure("user-1")
    tracker.record_failure("user-1")
    tracker.record_failure("user-1")
    assert tracker.is_locked_out("user-1") is True


def test_lockout_expires_after_the_cooldown() -> None:
    clock = _FakeClock()
    tracker = AccountLockoutTracker(max_failures=2, lockout_seconds=60, clock=clock)

    tracker.record_failure("user-1")
    tracker.record_failure("user-1")
    assert tracker.is_locked_out("user-1") is True

    clock.advance(60)
    assert tracker.is_locked_out("user-1") is False


def test_record_success_resets_the_failure_count() -> None:
    clock = _FakeClock()
    tracker = AccountLockoutTracker(max_failures=3, lockout_seconds=60, clock=clock)

    tracker.record_failure("user-1")
    tracker.record_failure("user-1")
    tracker.record_success("user-1")
    tracker.record_failure("user-1")
    tracker.record_failure("user-1")

    assert tracker.is_locked_out("user-1") is False


def test_lockouts_are_per_account() -> None:
    clock = _FakeClock()
    tracker = AccountLockoutTracker(max_failures=1, lockout_seconds=60, clock=clock)

    tracker.record_failure("user-a")
    assert tracker.is_locked_out("user-a") is True
    assert tracker.is_locked_out("user-b") is False
