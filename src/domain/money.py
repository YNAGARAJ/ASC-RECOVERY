"""Money and Rate value types."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import total_ordering
from typing import Final

TWO_PLACES: Final[Decimal] = Decimal("0.01")
CENTS_PER_UNIT: Final[int] = 100


def _reject_non_decimal_source(value: object, type_name: str) -> None:
    if isinstance(value, bool):
        raise TypeError(f"{type_name} cannot be constructed from bool")
    if isinstance(value, float):
        raise TypeError(f"{type_name} cannot be constructed from float; use int, Decimal, or str")
    if not isinstance(value, int | Decimal | str):
        raise TypeError(f"{type_name} cannot be constructed from {type(value).__name__}")


@total_ordering
@dataclass(frozen=True, slots=True)
class Rate:
    """An unquantized ratio (e.g. a discount or reduction rate). Not currency."""

    _value: Decimal

    def __init__(self, value: int | Decimal | str) -> None:
        _reject_non_decimal_source(value, "Rate")
        object.__setattr__(self, "_value", Decimal(value))

    @classmethod
    def percent(cls, value: int | Decimal | str) -> Rate:
        base = cls(value)
        return cls(base._value / Decimal(CENTS_PER_UNIT))

    def as_decimal(self) -> Decimal:
        return self._value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Rate):
            return NotImplemented
        return self._value == other._value

    def __lt__(self, other: Rate) -> bool:
        if not isinstance(other, Rate):
            return NotImplemented
        return self._value < other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"Rate({self._value!r})"


@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    """A currency amount, always exactly 2 decimal places, ROUND_HALF_UP."""

    _amount: Decimal

    def __init__(self, amount: int | Decimal | str) -> None:
        _reject_non_decimal_source(amount, "Money")
        quantized = Decimal(amount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        object.__setattr__(self, "_amount", quantized)

    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    @classmethod
    def from_cents(cls, cents: int) -> Money:
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise TypeError(f"from_cents requires an int, got {type(cents).__name__}")
        return cls(Decimal(cents) / Decimal(CENTS_PER_UNIT))

    @classmethod
    def parse(cls, value: str) -> Money:
        if not isinstance(value, str):
            raise TypeError(f"parse requires a str, got {type(value).__name__}")
        return cls(value)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            raise TypeError(f"Money can only be added to Money, got {type(other).__name__}")
        return Money(self._amount + other._amount)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            raise TypeError(f"Money can only be subtracted from Money, got {type(other).__name__}")
        return Money(self._amount - other._amount)

    def __neg__(self) -> Money:
        return Money(-self._amount)

    def __abs__(self) -> Money:
        return Money(abs(self._amount))

    def times(self, rate: Rate) -> Money:
        if not isinstance(rate, Rate):
            raise TypeError(f"Money can only be multiplied by a Rate, got {type(rate).__name__}")
        return Money(self._amount * rate.as_decimal())

    def __mul__(self, rate: Rate) -> Money:
        return self.times(rate)

    def allocate(self, parts: int) -> tuple[Money, ...]:
        """Largest-remainder pro-rata split: sum(result) == self, always exactly."""
        if isinstance(parts, bool) or not isinstance(parts, int):
            raise TypeError(f"allocate requires an int, got {type(parts).__name__}")
        if parts <= 0:
            raise ValueError("allocate requires a positive number of parts")
        total_cents = int(self._amount * CENTS_PER_UNIT)
        base, remainder = divmod(total_cents, parts)
        shares_cents = (base + 1 if i < remainder else base for i in range(parts))
        return tuple(Money(Decimal(c) / Decimal(CENTS_PER_UNIT)) for c in shares_cents)

    def is_negative(self) -> bool:
        return self._amount < 0

    def as_decimal(self) -> Decimal:
        return self._amount

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount == other._amount

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._amount < other._amount

    def __hash__(self) -> int:
        return hash(self._amount)

    def __str__(self) -> str:
        return str(self._amount)

    def __repr__(self) -> str:
        return f"Money('{self._amount}')"
