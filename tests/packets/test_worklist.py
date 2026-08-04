from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from packets.worklist import WorklistItem, rank_worklist

_AS_OF = date(2023, 1, 1)


def _item(days_out: int, shortfall: str) -> WorklistItem:
    return WorklistItem(
        finding_id=uuid.uuid4(),
        shortfall=Decimal(shortfall),
        deadline=_AS_OF + timedelta(days=days_out),
    )


def test_more_urgent_deadline_sorts_first_regardless_of_dollar_amount() -> None:
    urgent_small = _item(days_out=1, shortfall="10.00")
    distant_large = _item(days_out=30, shortfall="10000.00")

    ranked = rank_worklist([distant_large, urgent_small], as_of=_AS_OF)

    assert ranked == (urgent_small, distant_large)


def test_same_deadline_breaks_tie_by_larger_dollar_amount_first() -> None:
    small = _item(days_out=5, shortfall="50.00")
    large = _item(days_out=5, shortfall="500.00")

    ranked = rank_worklist([small, large], as_of=_AS_OF)

    assert ranked == (large, small)


def test_already_expired_items_sort_to_the_front() -> None:
    expired = _item(days_out=-3, shortfall="10.00")
    upcoming = _item(days_out=2, shortfall="10.00")

    ranked = rank_worklist([upcoming, expired], as_of=_AS_OF)

    assert ranked == (expired, upcoming)


def test_empty_worklist_ranks_to_empty() -> None:
    assert rank_worklist([], as_of=_AS_OF) == ()
