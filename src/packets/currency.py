"""Currency figure extraction and validation -- the hard boundary from
CLAUDE.md: "No LLM ever computes or restates a dollar amount... figures
are injected deterministically and validated after generation."

The regex requires either a leading `$` or an exact two-decimal-digit
suffix before treating a number as currency -- this leans on
`domain.money.Money`'s own invariant (always exactly 2 decimal places,
ROUND_HALF_UP) so a bare procedure code ("99213") or a date component
("2023") is never mistaken for a dollar figure.

Comparison is by parsed `Decimal` value, not by string, so `$50`, `50.00`,
and `$1,234.56` all normalize correctly -- `Decimal("50") ==
Decimal("50.00")` in Python, so formatting differences in the LLM's prose
don't cause false rejections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CURRENCY_PATTERN = re.compile(
    r"-?\$\s?\d[\d,]*(?:\.\d+)?"  # $-prefixed: decimals optional
    r"|-?\d[\d,]*\.\d{2}\b"  # no $, but an exact 2-decimal suffix
)


def extract_currency_figures(text: str) -> list[Decimal]:
    figures: list[Decimal] = []
    for match in _CURRENCY_PATTERN.finditer(text):
        raw = match.group().replace("$", "").replace(",", "").strip()
        try:
            figures.append(Decimal(raw))
        except InvalidOperation:
            continue
    return figures


@dataclass(frozen=True, slots=True)
class CurrencyValidationResult:
    valid: bool
    unmatched: tuple[Decimal, ...]


def validate_currency(text: str, allowed_amounts: frozenset[Decimal]) -> CurrencyValidationResult:
    found = extract_currency_figures(text)
    unmatched = tuple(figure for figure in found if figure not in allowed_amounts)
    return CurrencyValidationResult(valid=len(unmatched) == 0, unmatched=unmatched)
