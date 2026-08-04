"""Worklist ranking: sort by deadline proximity first, dollar value
second -- an expired appeal window is permanently lost money, so urgency
beats size. Already-expired items (negative days-until-deadline) sort to
the very front rather than being dropped -- they're the most operationally
urgent to see, even though the window itself can no longer be re-filed
(a write-off/escalation decision, not a reason to hide them).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.deadlines import days_until_deadline


@dataclass(frozen=True, slots=True)
class WorklistItem:
    finding_id: uuid.UUID
    shortfall: Decimal
    deadline: date


def rank_worklist(items: Sequence[WorklistItem], as_of: date) -> tuple[WorklistItem, ...]:
    return tuple(
        sorted(
            items,
            key=lambda item: (days_until_deadline(item.deadline, as_of), -item.shortfall),
        )
    )
