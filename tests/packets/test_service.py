"""Orchestration: happy path, reject-then-retry-then-succeed, and
all-attempts-fail -- the service must never return an unvalidated draft,
and every rejection must be recorded for the caller to log/audit.
"""

from __future__ import annotations

from datetime import date

from packets.drafter import ScriptedPacketDrafter
from packets.prompt import (
    ACTUAL_ALLOWED_TOKEN,
    EXPECTED_ALLOWED_TOKEN,
    PATIENT_TOKEN,
    PromptInput,
    required_figure_lines,
)
from packets.service import generate_packet_draft
from packets.templates import DEFAULT_TEMPLATE

_EXPECTED_LINE, _ACTUAL_LINE, _SHORTFALL_LINE = required_figure_lines()

_GOOD_DRAFT = (
    f"Dear Sir/Madam, {PATIENT_TOKEN} was underpaid on this claim.\n"
    f"{_EXPECTED_LINE}\n{_ACTUAL_LINE}\n{_SHORTFALL_LINE}\nSincerely,"
)
_RAW_FIGURE_DRAFT = "Dear Sir/Madam, the patient was underpaid $999.99. Sincerely,"
_SWAPPED_LABEL_DRAFT = (
    f"Dear Sir/Madam, {PATIENT_TOKEN} was underpaid on this claim.\n"
    # Expected/actual swapped -- the real bug F-15 catches: both tokens
    # are individually valid and both substitute to allowed amounts, but
    # each is now paired with the wrong label.
    f"Expected allowed amount: {ACTUAL_ALLOWED_TOKEN}\n"
    f"Actual amount paid: {EXPECTED_ALLOWED_TOKEN}\n"
    f"{_SHORTFALL_LINE}\nSincerely,"
)


def _make_input() -> PromptInput:
    return PromptInput(
        payer_claim_control_number="PAYERCTRL0001",
        procedure_code="99213",
        date_of_service=date(2023, 1, 10),
        expected_allowed="100.00",
        actual_allowed="50.00",
        shortfall="50.00",
        root_cause="MPPR_NOT_APPLIED",
        evidence="99213: expected 100.00, actual 50.00, shortfall 50.00",
        patient_name="JANE DOE",
        patient_member_id="MBR-1",
    )


def test_happy_path_succeeds_on_first_attempt() -> None:
    drafter = ScriptedPacketDrafter([_GOOD_DRAFT])

    result = generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter)

    assert result.success is True
    assert result.attempts == 1
    assert result.rejections == ()
    assert result.final_text is not None
    assert "100.00" in result.final_text
    assert "50.00" in result.final_text
    assert "JANE DOE" in result.final_text
    assert "{{" not in result.final_text


def test_raw_currency_figure_is_rejected_then_retried_and_succeeds() -> None:
    drafter = ScriptedPacketDrafter([_RAW_FIGURE_DRAFT, _GOOD_DRAFT])

    result = generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter)

    assert result.success is True
    assert result.attempts == 2
    assert len(result.rejections) == 1
    assert "999.99" in result.rejections[0].reason
    assert result.final_text is not None
    assert "999.99" not in result.final_text


def test_all_attempts_failing_never_returns_a_draft() -> None:
    drafter = ScriptedPacketDrafter([_RAW_FIGURE_DRAFT, _RAW_FIGURE_DRAFT])

    result = generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter, max_attempts=2)

    assert result.success is False
    assert result.final_text is None
    assert result.attempts == 2
    assert len(result.rejections) == 2


def test_the_llm_never_sees_the_patient_name_even_though_the_final_letter_does() -> None:
    drafter = ScriptedPacketDrafter([_GOOD_DRAFT])

    generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter)

    assert "JANE DOE" not in drafter.prompts_received[0]
    assert "MBR-1" not in drafter.prompts_received[0]


def test_swapped_figure_labels_are_rejected() -> None:
    """F-15 (docs/audit/REGISTER.md): a real, allowed figure placed
    against the wrong label -- both individual tokens are valid and both
    substitute to values in the allowed set, so plain set-membership
    validation alone would have accepted this. Only checking the exact
    label-to-token pairing catches it."""
    drafter = ScriptedPacketDrafter([_SWAPPED_LABEL_DRAFT, _GOOD_DRAFT])

    result = generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter)

    assert result.success is True
    assert result.attempts == 2
    assert len(result.rejections) == 1
    assert "missing or mislabeled" in result.rejections[0].reason


def test_bare_integer_hallucination_is_rejected() -> None:
    """F-14 (docs/audit/REGISTER.md): a hallucinated whole-dollar figure
    with no $ and no decimal point previously matched neither regex
    alternative and sailed through undetected."""
    draft_with_bare_integer = "Dear Sir/Madam, the patient was underpaid by about 50. Sincerely,"
    drafter = ScriptedPacketDrafter([draft_with_bare_integer, _GOOD_DRAFT])

    result = generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter)

    assert result.success is True
    assert result.attempts == 2
    assert len(result.rejections) == 1
    assert "50" in result.rejections[0].reason


def test_the_procedure_code_alone_is_never_flagged_as_a_hallucinated_figure() -> None:
    """The bare-integer detection F-14 added must not flag the one bare
    digit string this system knows for certain will legitimately appear
    -- the procedure code itself."""
    draft_mentioning_only_the_procedure_code = (
        f"Dear Sir/Madam, regarding procedure 99213.\n"
        f"{_EXPECTED_LINE}\n{_ACTUAL_LINE}\n{_SHORTFALL_LINE}\nSincerely,"
    )
    drafter = ScriptedPacketDrafter([draft_mentioning_only_the_procedure_code])

    result = generate_packet_draft(_make_input(), DEFAULT_TEMPLATE, drafter)

    assert result.success is True
    assert result.attempts == 1
    assert result.rejections == ()
