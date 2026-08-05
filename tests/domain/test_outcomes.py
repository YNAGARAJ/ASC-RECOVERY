"""Outcome recording validation and confidence-score calculation -- pure,
no live database needed."""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.money import Rate
from domain.outcomes import (
    HistoricalOutcome,
    NothingToAppealError,
    Outcome,
    OutcomeAlreadyRecordedError,
    calculate_confidence,
    validate_outcome_recording,
)
from domain.variance import RootCause


def test_validate_outcome_recording_allows_a_fresh_finding() -> None:
    validate_outcome_recording(RootCause.UNDETERMINED_VARIANCE, existing_outcome=None)


def test_validate_outcome_recording_rejects_a_second_recording() -> None:
    with pytest.raises(OutcomeAlreadyRecordedError, match="recovered"):
        validate_outcome_recording(
            RootCause.UNDETERMINED_VARIANCE, existing_outcome=Outcome.RECOVERED
        )


def test_validate_outcome_recording_rejects_a_no_variance_finding() -> None:
    with pytest.raises(NothingToAppealError):
        validate_outcome_recording(RootCause.CORRECT_NO_VARIANCE, existing_outcome=None)


def test_calculate_confidence_with_no_history_returns_none() -> None:
    assert calculate_confidence([]) is None


def test_calculate_confidence_all_recovered_is_full_confidence() -> None:
    historical = [HistoricalOutcome(Outcome.RECOVERED) for _ in range(4)]
    assert calculate_confidence(historical) == Rate(Decimal("1"))


def test_calculate_confidence_none_recovered_is_zero() -> None:
    historical = [HistoricalOutcome(Outcome.DENIED) for _ in range(3)]
    assert calculate_confidence(historical) == Rate(Decimal("0"))


def test_calculate_confidence_mixed_outcomes() -> None:
    historical = [
        HistoricalOutcome(Outcome.RECOVERED),
        HistoricalOutcome(Outcome.RECOVERED),
        HistoricalOutcome(Outcome.RECOVERED),
        HistoricalOutcome(Outcome.DENIED),
    ]
    assert calculate_confidence(historical) == Rate(Decimal("0.75"))


def test_calculate_confidence_treats_abandoned_and_expired_as_not_recovered() -> None:
    historical = [
        HistoricalOutcome(Outcome.RECOVERED),
        HistoricalOutcome(Outcome.ABANDONED),
        HistoricalOutcome(Outcome.EXPIRED),
        HistoricalOutcome(Outcome.DENIED),
    ]
    assert calculate_confidence(historical) == Rate(Decimal("0.25"))
