"""Outcome tracking and confidence scoring -- the "outcome feedback loop"
Phase 12 (`docs/MASTER-BUILD-PROMPT.md`) calls the actual moat: every
finding eventually resolves to recovered, denied, abandoned, or expired,
and that history is what lets a *future* finding for the same payer and
root cause carry a real, earned confidence score instead of a guess.

Pure: no I/O. `calculate_confidence` takes already-queried historical
rows; the query itself (which findings count as "the same payer and root
cause") lives in `db.repository`.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from domain.money import Rate
from domain.variance import RootCause


class Outcome(enum.Enum):
    RECOVERED = "recovered"
    DENIED = "denied"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class OutcomeAlreadyRecordedError(Exception):
    """A finding's outcome is recorded once, by a human, and never
    silently overwritten -- correcting a mistake is a documented
    follow-up action outside this system, not an update that would erase
    which decision was actually made and when."""


class NothingToAppealError(Exception):
    """A CORRECT_NO_VARIANCE finding never had an underpayment to pursue
    in the first place -- there is no outcome to record against it."""


def validate_outcome_recording(root_cause: RootCause, existing_outcome: Outcome | None) -> None:
    """Raises if this finding cannot accept an outcome right now. Callers
    (the API/repository write path) must call this before persisting --
    it never touches the database itself."""
    if existing_outcome is not None:
        raise OutcomeAlreadyRecordedError(
            f"this finding's outcome is already recorded as {existing_outcome.value!r}"
        )
    if root_cause is RootCause.CORRECT_NO_VARIANCE:
        raise NothingToAppealError(
            "a CORRECT_NO_VARIANCE finding has no underpayment to record an outcome for"
        )


@dataclass(frozen=True, slots=True)
class HistoricalOutcome:
    outcome: Outcome


def calculate_confidence(historical: Sequence[HistoricalOutcome]) -> Rate | None:
    """The fraction of past, decided findings (already filtered by the
    caller's query to the same payer + root cause) that were actually
    recovered. `None` -- not zero -- when there's no history yet: a
    brand-new payer/root-cause combination has no track record, and
    reporting a fabricated 0% or 50% confidence would be exactly the kind
    of invented precision this system's money rules elsewhere are careful
    to avoid."""
    if not historical:
        return None
    recovered_count = sum(1 for h in historical if h.outcome is Outcome.RECOVERED)
    return Rate(Decimal(recovered_count) / Decimal(len(historical)))
