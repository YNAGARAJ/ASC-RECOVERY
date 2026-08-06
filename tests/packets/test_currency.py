"""Phase 7 gate: the currency validator rejects a deliberately corrupted
draft."""

from __future__ import annotations

from decimal import Decimal

from packets.currency import extract_currency_figures, validate_currency


def test_extracts_dollar_prefixed_and_bare_decimal_figures() -> None:
    text = "The allowed amount was $1,234.56 but only 100.00 was paid."
    figures = extract_currency_figures(text)
    assert figures == [Decimal("1234.56"), Decimal("100.00")]


def test_bare_integers_are_detected_as_currency_by_default() -> None:
    """F-14 (docs/audit/REGISTER.md): a bare integer with no `$` and no
    decimal point previously matched neither regex alternative -- a
    hallucinated whole-dollar figure written that way sailed through
    both the raw-draft and post-substitution gates undetected. Detecting
    it by default (rather than only in an ad hoc "monetary context")
    means a caller must now explicitly `exclude` any bare digit string
    it knows for certain is legitimately non-monetary -- see
    test_exclude_suppresses_a_known_non_monetary_number below."""
    text = "Procedure 99213 was billed on 2023-01-10, claim ref PAYERCTRL0001."
    figures = extract_currency_figures(text)
    assert Decimal("99213") in figures
    assert Decimal("2023") in figures


def test_exclude_suppresses_a_known_non_monetary_number() -> None:
    text = "Procedure 99213 was billed for the claim."
    figures = extract_currency_figures(text, exclude=frozenset({"99213"}))
    assert figures == []


def test_a_hallucinated_bare_integer_is_still_caught_alongside_an_excluded_procedure_code() -> None:
    text = "Procedure 99213 was underpaid by roughly 50."
    figures = extract_currency_figures(text, exclude=frozenset({"99213"}))
    assert figures == [Decimal("50")]


def test_dollar_amount_without_cents_normalizes_by_value() -> None:
    # "$50" (no decimal) must still compare equal to an allowed "50.00".
    figures = extract_currency_figures("We are owed $50 for this line.")
    assert figures == [Decimal("50")]
    assert Decimal("50") == Decimal("50.00")


def test_valid_draft_matching_the_finding_record_passes() -> None:
    allowed = frozenset({Decimal("100.00"), Decimal("50.00"), Decimal("50.00")})
    text = "The contracted rate was $100.00 but only $50.00 was paid, a shortfall of $50.00."
    result = validate_currency(text, allowed)
    assert result.valid is True
    assert result.unmatched == ()


def test_deliberately_corrupted_draft_is_rejected() -> None:
    """The gate: a figure the LLM invented (not present in the finding
    record) must be caught and the draft rejected, not silently sent."""
    allowed = frozenset({Decimal("100.00"), Decimal("50.00")})
    corrupted = "The contracted rate was $100.00 but only $50.00 was paid, a shortfall of $75.00."

    result = validate_currency(corrupted, allowed)

    assert result.valid is False
    assert Decimal("75.00") in result.unmatched
