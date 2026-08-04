"""Appeal-deadline math. Correct across timezones by construction (plain
`date` arithmetic has no timezone concept to get wrong) and across leap
years because Python's `date` arithmetic is already calendar-aware --
these tests prove the calendar-aware part with concrete boundary cases,
since "timezone-safe by construction" isn't something a unit test on a
timezone-free function can demonstrate any other way.
"""

from __future__ import annotations

from datetime import date

import pytest

from domain.deadlines import calculate_appeal_deadline, days_until_deadline, is_expired


def test_deadline_spans_a_leap_day_correctly() -> None:
    # 2024 is a leap year: Jan(31) + Feb(29) + Mar(31) = 91, so day 90 lands
    # on March 31, not April 1.
    deadline = calculate_appeal_deadline(date(2024, 1, 1), 90)
    assert deadline == date(2024, 3, 31)


def test_same_window_in_a_non_leap_year_lands_one_day_later() -> None:
    # 2023 is not a leap year: Jan(31) + Feb(28) + Mar(31) = 90 exactly, so
    # the same 90-day window lands on April 1 -- one calendar day later
    # than the leap-year case above, purely from Feb having 28 vs 29 days.
    deadline = calculate_appeal_deadline(date(2023, 1, 1), 90)
    assert deadline == date(2023, 4, 1)


def test_deadline_spanning_new_years_eve_rolls_into_next_year() -> None:
    deadline = calculate_appeal_deadline(date(2023, 12, 1), 45)
    assert deadline == date(2024, 1, 15)


def test_zero_day_window_deadline_is_the_service_date_itself() -> None:
    deadline = calculate_appeal_deadline(date(2023, 6, 15), 0)
    assert deadline == date(2023, 6, 15)


def test_negative_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        calculate_appeal_deadline(date(2023, 1, 1), -1)


def test_days_until_deadline_counts_down_to_zero_then_negative() -> None:
    deadline = date(2023, 4, 1)
    assert days_until_deadline(deadline, as_of=date(2023, 1, 1)) == 90
    assert days_until_deadline(deadline, as_of=date(2023, 4, 1)) == 0
    assert days_until_deadline(deadline, as_of=date(2023, 4, 2)) == -1


def test_is_expired_is_false_on_the_deadline_day_itself() -> None:
    deadline = date(2023, 4, 1)
    assert is_expired(deadline, as_of=date(2023, 4, 1)) is False
    assert is_expired(deadline, as_of=date(2023, 3, 31)) is False
    assert is_expired(deadline, as_of=date(2023, 4, 2)) is True
