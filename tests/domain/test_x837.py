"""Tests for domain.x837.parse_837.

Reuses the generic, transaction-set-agnostic segment/envelope utilities
from tests.domain.fixtures_x835 (seg/isa_segment/assemble/delimiters) --
not that module's 835-specific envelope_head/claim_segments (BPR/TRN/N1
payer info an 837 doesn't have at all), same reuse boundary
domain.x837 itself draws against domain.x835 (Entity/ParseIssue reused
directly, parsing control flow duplicated, not imported).
"""

from __future__ import annotations

from domain.x837 import parse_837
from tests.domain.fixtures_x835 import ELEMENT_SEP, SUB_ELEMENT_SEP, assemble, isa_segment, seg

_PATIENT_CONTROL_NUMBER = "CLAIM0001"


def _clm_segment(patient_control_number: str, charge: str = "500") -> str:
    filing_code = f"11{SUB_ELEMENT_SEP}B{SUB_ELEMENT_SEP}1"
    return seg(ELEMENT_SEP, "CLM", patient_control_number, charge, "", "", filing_code)


def _envelope_head_837(control_number: str = "0001") -> list[str]:
    return [
        isa_segment(ELEMENT_SEP, SUB_ELEMENT_SEP),
        seg(
            ELEMENT_SEP, "GS", "HC", "SENDERID", "RECEIVERID", "20230101", "1200", "1", "X",
            "005010X222A1",
        ),
        seg(ELEMENT_SEP, "ST", "837", control_number, "005010X222A1"),
    ]


def _envelope_tail_837(control_number: str = "0001") -> list[str]:
    return [
        seg(ELEMENT_SEP, "SE", "1", control_number),
        seg(ELEMENT_SEP, "GE", "1", "1"),
        seg(ELEMENT_SEP, "IEA", "1", "000000905"),
    ]


def _minimal_valid_837(
    *,
    patient_control_number: str = _PATIENT_CONTROL_NUMBER,
    extra_segments: list[str] | None = None,
) -> str:
    segments = [
        *_envelope_head_837(),
        _clm_segment(patient_control_number),
        *(extra_segments or []),
        *_envelope_tail_837(),
    ]
    return assemble(segments)


def test_parses_minimal_valid_envelope() -> None:
    result = parse_837(_minimal_valid_837())
    assert result.errors == ()
    assert len(result.transactions) == 1
    txn = result.transactions[0]
    assert txn.control_number == "0001"
    assert len(txn.claims) == 1
    assert txn.claims[0].patient_control_number == _PATIENT_CONTROL_NUMBER


def test_hi_diagnosis_codes_extracted() -> None:
    text = _minimal_valid_837(
        extra_segments=[
            seg(ELEMENT_SEP, "HI", f"ABK{SUB_ELEMENT_SEP}E119", f"ABF{SUB_ELEMENT_SEP}I10")
        ]
    )
    (claim,) = parse_837(text).transactions[0].claims
    assert claim.diagnosis_codes == ("E119", "I10")


def test_hi_with_no_codes_present_is_empty_tuple() -> None:
    (claim,) = parse_837(_minimal_valid_837()).transactions[0].claims
    assert claim.diagnosis_codes == ()


def test_hi_across_multiple_segments_accumulates() -> None:
    text = _minimal_valid_837(
        extra_segments=[
            seg(ELEMENT_SEP, "HI", f"ABK{SUB_ELEMENT_SEP}E119"),
            seg(ELEMENT_SEP, "HI", f"ABF{SUB_ELEMENT_SEP}I10"),
        ]
    )
    (claim,) = parse_837(text).transactions[0].claims
    assert claim.diagnosis_codes == ("E119", "I10")


def test_nm1_82_is_rendering_provider() -> None:
    text = _minimal_valid_837(
        extra_segments=[
            seg(
                ELEMENT_SEP, "NM1", "82", "1", "RENDERING", "PROVIDER", "", "", "", "XX",
                "1999999999",
            )
        ]
    )
    (claim,) = parse_837(text).transactions[0].claims
    assert claim.rendering_provider is not None
    assert claim.rendering_provider.entity_identifier_code == "82"
    assert claim.rendering_provider.name == "RENDERING"
    assert claim.rendering_provider.id_code == "1999999999"


def test_nm1_other_entity_codes_are_ignored() -> None:
    text = _minimal_valid_837(
        extra_segments=[
            seg(
                ELEMENT_SEP, "NM1", "85", "2", "BILLING PROVIDER", "", "", "", "", "XX",
                "1888888888",
            )
        ]
    )
    (claim,) = parse_837(text).transactions[0].claims
    assert claim.rendering_provider is None


def test_multiple_claims_in_one_transaction() -> None:
    segments = [
        *_envelope_head_837(),
        _clm_segment("CLAIM0001"),
        seg(ELEMENT_SEP, "HI", f"ABK{SUB_ELEMENT_SEP}E119"),
        _clm_segment("CLAIM0002", "300"),
        seg(ELEMENT_SEP, "HI", f"ABK{SUB_ELEMENT_SEP}I10"),
        *_envelope_tail_837(),
    ]
    result = parse_837(assemble(segments))
    (txn,) = result.transactions
    assert len(txn.claims) == 2
    by_pcn = {c.patient_control_number: c for c in txn.claims}
    assert by_pcn["CLAIM0001"].diagnosis_codes == ("E119",)
    assert by_pcn["CLAIM0002"].diagnosis_codes == ("I10",)


def test_missing_isa_header_is_a_parse_error() -> None:
    result = parse_837("not a real 837 file")
    assert result.transactions == ()
    assert len(result.errors) == 1
    assert result.errors[0].segment_tag == "ISA"


def test_clm_missing_patient_control_number_is_a_parse_error() -> None:
    segments = [*_envelope_head_837(), _clm_segment(""), *_envelope_tail_837()]
    result = parse_837(assemble(segments))
    assert result.errors
    assert result.errors[0].segment_tag == "CLM"
    (txn,) = result.transactions
    assert txn.claims == ()
