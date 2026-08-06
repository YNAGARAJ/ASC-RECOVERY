"""Phase 7 gate: no patient identifiers in any LLM prompt -- asserted
directly on the captured `BuiltPrompt.text` for a range of inputs,
including distinctive names/member-ids chosen so a false negative (the
name coincidentally matching template boilerplate) can't happen by luck.
"""

from __future__ import annotations

from datetime import date

import pytest

from packets.prompt import (
    ACTUAL_ALLOWED_TOKEN,
    CLAIM_REFERENCE_TOKEN,
    DATE_OF_SERVICE_TOKEN,
    EXPECTED_ALLOWED_TOKEN,
    MEMBER_ID_TOKEN,
    PATIENT_TOKEN,
    SHORTFALL_TOKEN,
    PromptInput,
    build_prompt,
    render_final_text,
)
from packets.templates import DEFAULT_TEMPLATE

_CASES = [
    ("ZBIGNIEW KOWALCZYK-VANDERMEER", "MBR-XQ99871234"),
    ("PATIENT ONE", "TESTMBR000001"),
    ("O'BRIEN-SALUTATION", "MBR-CLOSING-000"),  # deliberately echoes template words
]

_CLAIM_REFERENCE = "PAYERCTRL0001"
_DATE_OF_SERVICE = date(2023, 1, 10)


def _make_input(patient_name: str | None, patient_member_id: str | None) -> PromptInput:
    return PromptInput(
        payer_claim_control_number=_CLAIM_REFERENCE,
        procedure_code="99213",
        date_of_service=_DATE_OF_SERVICE,
        expected_allowed="100.00",
        actual_allowed="50.00",
        shortfall="50.00",
        root_cause="MPPR_NOT_APPLIED",
        evidence="99213: expected 100.00, actual 50.00, shortfall 50.00",
        patient_name=patient_name,
        patient_member_id=patient_member_id,
    )


@pytest.mark.parametrize(("patient_name", "member_id"), _CASES)
def test_prompt_text_never_contains_the_patient_name_or_member_id(
    patient_name: str, member_id: str
) -> None:
    built = build_prompt(_make_input(patient_name, member_id), DEFAULT_TEMPLATE)

    assert patient_name not in built.text
    assert member_id not in built.text
    # The placeholder tokens the LLM is instructed to use instead ARE
    # present -- proving this isn't just an empty/truncated prompt.
    assert PATIENT_TOKEN in built.text
    assert MEMBER_ID_TOKEN in built.text


def test_prompt_text_never_contains_the_claim_reference_or_date_of_service() -> None:
    """F-13 (docs/audit/REGISTER.md): both are HIPAA Safe Harbor
    identifiers (claim/account number #16/#18, date of service #3) --
    an earlier version of this module sent them to the LLM as literal
    text and its own docstring wrongly called them "not PHI"."""
    built = build_prompt(_make_input("JANE DOE", "MBR-1"), DEFAULT_TEMPLATE)

    assert _CLAIM_REFERENCE not in built.text
    assert _DATE_OF_SERVICE.isoformat() not in built.text
    assert CLAIM_REFERENCE_TOKEN in built.text
    assert DATE_OF_SERVICE_TOKEN in built.text


def test_prompt_placeholders_map_carries_the_real_values_for_later_substitution() -> None:
    built = build_prompt(_make_input("JANE DOE", "MBR-1"), DEFAULT_TEMPLATE)

    assert built.placeholders[PATIENT_TOKEN] == "JANE DOE"
    assert built.placeholders[MEMBER_ID_TOKEN] == "MBR-1"
    assert built.placeholders[CLAIM_REFERENCE_TOKEN] == _CLAIM_REFERENCE
    assert built.placeholders[DATE_OF_SERVICE_TOKEN] == _DATE_OF_SERVICE.isoformat()
    assert built.placeholders[EXPECTED_ALLOWED_TOKEN] == "100.00"
    assert built.placeholders[ACTUAL_ALLOWED_TOKEN] == "50.00"
    assert built.placeholders[SHORTFALL_TOKEN] == "50.00"


def test_missing_patient_name_falls_back_to_a_generic_reference_not_none() -> None:
    built = build_prompt(_make_input(None, None), DEFAULT_TEMPLATE)
    assert built.placeholders[PATIENT_TOKEN] == "the patient"
    assert built.placeholders[MEMBER_ID_TOKEN] == ""


def test_render_final_text_substitutes_every_placeholder() -> None:
    draft = (
        f"Dear Sir/Madam, {PATIENT_TOKEN} ({MEMBER_ID_TOKEN}) was underpaid: "
        f"expected {EXPECTED_ALLOWED_TOKEN}, paid {ACTUAL_ALLOWED_TOKEN}, "
        f"shortfall {SHORTFALL_TOKEN}."
    )
    placeholders = {
        PATIENT_TOKEN: "JANE DOE",
        MEMBER_ID_TOKEN: "MBR-1",
        EXPECTED_ALLOWED_TOKEN: "100.00",
        ACTUAL_ALLOWED_TOKEN: "50.00",
        SHORTFALL_TOKEN: "50.00",
    }

    rendered = render_final_text(draft, placeholders)

    assert rendered == (
        "Dear Sir/Madam, JANE DOE (MBR-1) was underpaid: "
        "expected 100.00, paid 50.00, shortfall 50.00."
    )
    assert "{{" not in rendered
