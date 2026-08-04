"""Appeal-deadline (timely-filing) math.

Deliberately plain `date` arithmetic, never `datetime`/timezone-aware --
a `date` has no timezone concept at all, which is what makes this
"correct across timezones": there's no timezone to get wrong. Python's
`date + timedelta` is already calendar-aware (leap years included), so
no special-casing is needed there either. Any "current date" this module
needs is an explicit parameter (`as_of`), never an implicit
`datetime.now()` -- same testability principle as
`security.rate_limit`'s injectable `clock`.
"""

from __future__ import annotations

from datetime import date, timedelta


def calculate_appeal_deadline(date_of_service: date, timely_filing_days: int) -> date:
    if timely_filing_days < 0:
        raise ValueError(f"timely_filing_days must be non-negative, got {timely_filing_days}")
    return date_of_service + timedelta(days=timely_filing_days)


def days_until_deadline(deadline: date, as_of: date) -> int:
    """Negative once the deadline has passed."""
    return (deadline - as_of).days


def is_expired(deadline: date, as_of: date) -> bool:
    return as_of > deadline
