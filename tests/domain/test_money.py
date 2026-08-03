"""Tests for domain.money.Money and domain.money.Rate.

Written before any implementation exists (money.py currently only has typed
stub signatures raising NotImplementedError) -- these are expected to fail
until Step 2 implements the module, per the approved test-first plan.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain.money import Money, Rate

# --- Construction ------------------------------------------------------------


def test_construct_from_int() -> None:
    assert Money(5) == Money("5.00")


def test_construct_from_decimal() -> None:
    assert Money(Decimal("12.34")) == Money("12.34")


def test_construct_from_str() -> None:
    assert Money("0.01") == Money("0.01")


def test_construct_from_float_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money(1.5)  # type: ignore[arg-type]


def test_construct_from_bool_raises_type_error() -> None:
    # bool is a subtype of int, so this is not a static type error -- the
    # rejection is a deliberate runtime guard against the bool-as-int footgun.
    with pytest.raises(TypeError):
        Money(True)


# --- Quantization (ROUND_HALF_UP, 2 places) ----------------------------------


def test_quantize_rounds_half_up_positive() -> None:
    assert Money("1.005") == Money("1.01")


def test_quantize_rounds_down_when_below_half() -> None:
    assert Money("1.004") == Money("1.00")


def test_quantize_rounds_half_up_negative_away_from_zero() -> None:
    assert Money("-1.005") == Money("-1.01")


# --- Arithmetic ----------------------------------------------------------


def test_add() -> None:
    assert Money("1.10") + Money("2.20") == Money("3.30")


def test_subtract() -> None:
    assert Money("5.00") - Money("1.50") == Money("3.50")


def test_negate() -> None:
    assert -Money("5.00") == Money("-5.00")


def test_abs() -> None:
    assert abs(Money("-5.00")) == Money("5.00")


def test_add_bare_float_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money("1.00") + 0.5  # type: ignore[operator]


def test_add_bare_decimal_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money("1.00") + Decimal("1.00")  # type: ignore[operator]


def test_add_bare_int_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money("1.00") + 1  # type: ignore[operator]


# --- Multiplication by Rate ------------------------------------------------


def test_times_rate_quantizes_result() -> None:
    assert Money("10.00").times(Rate("0.615")) == Money("6.15")


def test_mul_operator_is_equivalent_to_times() -> None:
    assert Money("10.00") * Rate("0.50") == Money("5.00")


def test_times_bare_float_rate_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money("10.00").times(0.5)  # type: ignore[arg-type]


def test_mul_bare_str_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Money("10.00") * "0.5"  # type: ignore[operator]


# --- Comparison / ordering / hashing -----------------------------------------


def test_ordering_operators() -> None:
    assert Money("1.00") < Money("2.00")
    assert Money("2.00") <= Money("2.00")
    assert Money("3.00") > Money("2.00")
    assert Money("2.00") >= Money("2.00")


def test_sorted_on_mixed_list() -> None:
    values = [Money("3.00"), Money("1.00"), Money("2.00")]
    assert sorted(values) == [Money("1.00"), Money("2.00"), Money("3.00")]


def test_usable_as_dict_key() -> None:
    d = {Money("1.00"): "one"}
    assert d[Money("1.00")] == "one"


def test_usable_as_set_member() -> None:
    s = {Money("1.00"), Money("1.00"), Money("2.00")}
    assert len(s) == 2


# --- allocate() (largest-remainder pro-rata split) ---------------------------


def test_allocate_even_split() -> None:
    parts = Money("9.00").allocate(3)
    assert parts == (Money("3.00"), Money("3.00"), Money("3.00"))


def test_allocate_uneven_split_sums_exactly() -> None:
    parts = Money("10.00").allocate(3)
    assert len(parts) == 3
    assert sum(parts, Money.zero()) == Money("10.00")


def test_allocate_zero_parts_raises_value_error() -> None:
    with pytest.raises(ValueError):
        Money("10.00").allocate(0)


def test_allocate_negative_parts_raises_value_error() -> None:
    with pytest.raises(ValueError):
        Money("10.00").allocate(-1)


# --- Rate ------------------------------------------------------------------


def test_rate_percent_helper() -> None:
    assert Rate.percent(Decimal("50")) == Rate(Decimal("0.50"))


def test_rate_from_float_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Rate(0.5)  # type: ignore[arg-type]


# --- Convenience constructors -------------------------------------------------


def test_from_cents() -> None:
    assert Money.from_cents(150) == Money("1.50")


def test_zero() -> None:
    assert Money.zero() == Money("0.00")


def test_parse() -> None:
    assert Money.parse("42.00") == Money("42.00")


def test_str_representation() -> None:
    assert str(Money("-5.00")) == "-5.00"
