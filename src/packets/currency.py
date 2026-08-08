"""Currency figure extraction and validation -- the hard boundary from
CLAUDE.md: "No LLM ever computes or restates a dollar amount... figures
are injected deterministically and validated after generation."

The regex matches a leading `$`, an exact two-decimal-digit suffix, OR
(F-14, docs/audit/REGISTER.md) a bare integer with neither -- a
hallucinated whole-dollar figure written that way (no `$`, no decimal
point) previously matched nothing at all and sailed through both the
raw-draft and post-substitution gates undetected. Every real dollar
value this system ever injects has exactly 2 decimal places
(`domain.money.Money`'s own invariant), so a *correct* substituted
figure is always caught by the second alternative regardless; the third
alternative exists purely to catch what the LLM might invent on its own.

Catching bare integers means a legitimate non-monetary number (a
procedure code, most commonly) would otherwise be flagged as a
hallucinated currency figure -- callers pass the one such number they
know for certain will legitimately appear via `exclude`, so the exact
string of `PromptInput.procedure_code` is never treated as currency, but
nothing else gets a free pass.

`exclude` masks out whole matching substrings from `text` before the
regex ever runs, rather than filtering individually matched fragments
after the fact (Phase 9, `docs/MASTER-BUILD-PROMPT-V2.md`) -- needed
once a second known-safe, deterministically-injected value could contain
an *internal* decimal point of its own (an ICD-10 diagnosis code like
`E11.9`): the bare-integer alternative's `\b` boundary matches right
after the `.`, so `extract_currency_figures` on unmasked text would
still find a spurious `9` even with `"E11.9"` naively added to `exclude`
-- the matched fragment is `"9"`, never the whole code, so exact-fragment
filtering can't catch it. Masking the *known-safe strings themselves*
out of the text first sidesteps this regardless of what a future
excluded value happens to look like.

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
    r"|-?\b\d[\d,]*\b"  # bare integer: no $, no decimal point (F-14)
)


def extract_currency_figures(text: str, *, exclude: frozenset[str] = frozenset()) -> list[Decimal]:
    masked = text
    for safe in exclude:
        if safe:
            masked = masked.replace(safe, "")
    figures: list[Decimal] = []
    for match in _CURRENCY_PATTERN.finditer(masked):
        raw = match.group().strip()
        cleaned = raw.replace("$", "").replace(",", "")
        try:
            figures.append(Decimal(cleaned))
        except InvalidOperation:
            continue
    return figures


@dataclass(frozen=True, slots=True)
class CurrencyValidationResult:
    valid: bool
    unmatched: tuple[Decimal, ...]


def validate_currency(
    text: str, allowed_amounts: frozenset[Decimal], *, exclude: frozenset[str] = frozenset()
) -> CurrencyValidationResult:
    found = extract_currency_figures(text, exclude=exclude)
    unmatched = tuple(figure for figure in found if figure not in allowed_amounts)
    return CurrencyValidationResult(valid=len(unmatched) == 0, unmatched=unmatched)
