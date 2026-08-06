"""Tests for F-11's stateful alert-feeding trackers
(docs/audit/REGISTER.md): RollingWindowCounter and IngestionOutcomeTracker.
"""

from __future__ import annotations

import pytest

from observability.alert_state import IngestionOutcomeTracker, RollingWindowCounter


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_record_counts_hits_within_the_window() -> None:
    clock = _FakeClock()
    counter = RollingWindowCounter(window_seconds=10, clock=clock)

    assert counter.record("actor-a") == 1
    assert counter.record("actor-a") == 2
    assert counter.record("actor-a") == 3


def test_hits_outside_the_window_are_pruned() -> None:
    clock = _FakeClock()
    counter = RollingWindowCounter(window_seconds=10, clock=clock)

    counter.record("actor-a")
    counter.record("actor-a")
    clock.now += 11
    assert counter.record("actor-a") == 1  # the two old hits aged out


def test_count_does_not_record_a_new_hit() -> None:
    clock = _FakeClock()
    counter = RollingWindowCounter(window_seconds=10, clock=clock)

    counter.record("actor-a")
    assert counter.count("actor-a") == 1
    assert counter.count("actor-a") == 1  # peeking again doesn't add another


def test_count_for_an_unknown_key_is_zero() -> None:
    counter = RollingWindowCounter(window_seconds=10)
    assert counter.count("never-seen") == 0


def test_different_keys_are_independent() -> None:
    counter = RollingWindowCounter(window_seconds=10)
    counter.record("actor-a")
    counter.record("actor-a")
    counter.record("actor-b")
    assert counter.count("actor-a") == 2
    assert counter.count("actor-b") == 1


def test_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="window_seconds"):
        RollingWindowCounter(window_seconds=0)


def test_ingestion_outcome_tracker_returns_quarantined_and_total() -> None:
    clock = _FakeClock()
    tracker = IngestionOutcomeTracker(window_seconds=3600, clock=clock)

    quarantined, total = tracker.record("tenant-a", quarantined=False)
    assert (quarantined, total) == (0, 1)

    quarantined, total = tracker.record("tenant-a", quarantined=True)
    assert (quarantined, total) == (1, 2)

    quarantined, total = tracker.record("tenant-a", quarantined=False)
    assert (quarantined, total) == (1, 3)


def test_ingestion_outcome_tracker_is_per_tenant() -> None:
    tracker = IngestionOutcomeTracker(window_seconds=3600)
    tracker.record("tenant-a", quarantined=True)
    quarantined, total = tracker.record("tenant-b", quarantined=False)
    assert (quarantined, total) == (0, 1)
