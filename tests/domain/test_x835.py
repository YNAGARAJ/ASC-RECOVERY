"""Tests for domain.x835.parse_835.

Written before any implementation exists (x835.py currently only has typed
stub signatures/dataclasses, and parse_835 raises NotImplementedError) --
these are expected to fail until Step 2 implements the module, per the
approved test-first plan.

The six malformed-input cases required by the Phase 1 gate are named
explicitly below (search "GATE-REQUIRED").
"""

from __future__ import annotations

from domain.money import Money
from domain.x835 import AdjustmentGroup, ClaimStatus, parse_835
from tests.domain.fixtures_x835 import (
    ELEMENT_SEP,
    SUB_ELEMENT_SEP,
    assemble,
    claim_segments,
    custom_delimiters_835,
    dangling_cas_triplet_835,
    denial_835,
    envelope_head,
    envelope_tail,
    malformed_bare_single_line,
    malformed_clp,
    malformed_missing_isa,
    malformed_mixed_line_endings_unix,
    malformed_mixed_line_endings_windows,
    malformed_truncated_file,
    minimal_valid_835,
    mismatched_reconciliation_835,
    non_numeric_charge_835,
    partial_batch_one_bad_claim,
    plb_segment,
    reversal_835,
    secondary_payer_835,
    seg,
)

# --- Envelope / basics -------------------------------------------------------


def test_parses_minimal_valid_envelope() -> None:
    result = parse_835(minimal_valid_835())
    assert result.errors == ()
    assert len(result.transactions) == 1
    txn = result.transactions[0]
    assert len(txn.claims) == 1


def test_bpr_and_trn_parsed() -> None:
    txn = parse_835(minimal_valid_835()).transactions[0]
    assert txn.bpr_total_paid == Money("500.00")
    assert txn.trn_trace_number == "TRACE0001"


def test_n1_payer_vs_payee_distinguished() -> None:
    txn = parse_835(minimal_valid_835()).transactions[0]
    assert txn.payer is not None and txn.payer.entity_identifier_code == "PR"
    assert txn.payee is not None and txn.payee.entity_identifier_code == "PE"


def test_custom_delimiters_read_from_isa_not_hardcoded() -> None:
    """Delimiters declared only via ISA positions (element '|', sub-element '^',
    terminator '\\') must still parse correctly -- proves the parser doesn't
    assume '*'/':'/'~'."""
    result = parse_835(custom_delimiters_835())
    assert result.errors == ()
    claim = result.transactions[0].claims[0]
    assert claim.total_charge == Money("500.00")


def test_nm1_patient_and_provider_parsed_with_synthetic_placeholders() -> None:
    claim = parse_835(minimal_valid_835()).transactions[0].claims[0]
    assert claim.patient is not None
    assert claim.patient.name == "PATIENT ONE"
    assert claim.patient.id_code == "TESTMBR000001"
    assert claim.rendering_provider is not None
    assert claim.rendering_provider.name == "TEST RENDERING PROVIDER"


def test_dtm_claim_and_line_level() -> None:
    claim = parse_835(minimal_valid_835()).transactions[0].claims[0]
    claim_level_qualifiers = {d.qualifier for d in claim.dates}
    assert {"232", "233"}.issubset(claim_level_qualifiers)
    line = claim.service_lines[0]
    assert line.service_date is not None


def test_lq_remarks_captured() -> None:
    line = parse_835(minimal_valid_835()).transactions[0].claims[0].service_lines[0]
    assert "N362" in line.remarks


def test_plb_negative_takeback() -> None:
    txn = parse_835(minimal_valid_835()).transactions[0]
    assert len(txn.plb_adjustments) == 1
    assert txn.plb_adjustments[0].amount == Money("-10.00")


# --- SVC composite procedure:modifier, CAS repeating triplets ---------------


def test_svc_composite_procedure_and_single_modifier() -> None:
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0100",
            "1",
            "1000.00",
            "750.00",
            "0.00",
            "12",
            "PAYERCTRL0100",
            "11",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(
            ELEMENT_SEP,
            "SVC",
            f"HC{SUB_ELEMENT_SEP}27447{SUB_ELEMENT_SEP}50",
            "1000.00",
            "750.00",
            "",
            "1",
        ),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "250.00"),
        *envelope_tail(segment_count="9"),
    ]
    result = parse_835(assemble(segments))
    line = result.transactions[0].claims[0].service_lines[0]
    assert line.procedure_code == "27447"
    assert line.modifiers == ("50",)


def test_claim_level_cas_multiple_triplets_single_segment() -> None:
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0101",
            "1",
            "500.00",
            "400.00",
            "0.00",
            "12",
            "PAYERCTRL0101",
            "11",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "50.00", "CO", "96", "30.00", "CO", "97", "20.00"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "400.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        *envelope_tail(segment_count="8"),
    ]
    result = parse_835(assemble(segments))
    claim = result.transactions[0].claims[0]
    assert len(claim.adjustments) == 3
    assert sum((a.amount for a in claim.adjustments), Money.zero()) == Money("100.00")


def test_service_level_cas_repeating_triplets() -> None:
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0102",
            "1",
            "500.00",
            "400.00",
            "0.00",
            "12",
            "PAYERCTRL0102",
            "11",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "400.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "60.00", "CO", "96", "40.00"),
        *envelope_tail(segment_count="8"),
    ]
    result = parse_835(assemble(segments))
    line = result.transactions[0].claims[0].service_lines[0]
    assert len(line.adjustments) == 2
    assert line.allowed == Money("400.00")


def test_two_cas_segments_on_same_claim_concatenate() -> None:
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0103",
            "1",
            "500.00",
            "350.00",
            "0.00",
            "12",
            "PAYERCTRL0103",
            "11",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "50.00"),
        seg(ELEMENT_SEP, "CAS", "CO", "96", "100.00"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "350.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        *envelope_tail(segment_count="9"),
    ]
    result = parse_835(assemble(segments))
    claim = result.transactions[0].claims[0]
    assert len(claim.adjustments) == 2
    assert sum((a.amount for a in claim.adjustments), Money.zero()) == Money("150.00")


# --- allowed/paid arithmetic, reconciliation ---------------------------------


def test_allowed_and_paid_arithmetic() -> None:
    """charge 500, CO 50 total across two CAS -> allowed 450; PR 20 -> paid 430."""
    line = parse_835(minimal_valid_835()).transactions[0].claims[0].service_lines[0]
    assert line.allowed == Money("450.00")
    assert line.paid_computed == Money("430.00")


def test_reconciliation_matches_clp04() -> None:
    claim = parse_835(minimal_valid_835()).transactions[0].claims[0]
    assert claim.is_reconciled is True
    assert claim.reconciliation_diff == Money.zero()


def test_reconciliation_mismatch_detected_no_exception() -> None:
    claim = parse_835(mismatched_reconciliation_835()).transactions[0].claims[0]
    assert claim.is_reconciled is False
    assert claim.reconciliation_diff != Money.zero()


# --- Claim status: reversal, denial, secondary/tertiary payer ---------------


def test_reversal_claim_status_negative_paid_no_crash() -> None:
    claim = parse_835(reversal_835()).transactions[0].claims[0]
    assert claim.status == ClaimStatus.REVERSAL_OF_PREVIOUS_PAYMENT
    assert claim.total_paid_reported == Money("-430.00")


def test_denial_claim_status_zero_paid_full_co() -> None:
    claim = parse_835(denial_835()).transactions[0].claims[0]
    assert claim.status == ClaimStatus.DENIED
    assert claim.total_paid_reported == Money("0.00")
    line = claim.service_lines[0]
    assert line.paid_computed == Money("0.00")


def test_secondary_payer_oa_retained_not_subtracted() -> None:
    claim = parse_835(secondary_payer_835()).transactions[0].claims[0]
    assert claim.status == ClaimStatus.SECONDARY
    line = claim.service_lines[0]
    oa_adjustments = [a for a in claim.adjustments if a.group_code == AdjustmentGroup.OA]
    assert len(oa_adjustments) == 1
    # OA is retained on the claim but must not reduce the computed allowed/paid.
    assert line.allowed == Money("500.00")


def test_tertiary_payer_variant() -> None:
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0104",
            "3",
            "500.00",
            "100.00",
            "0.00",
            "12",
            "PAYERCTRL0104",
            "11",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "100.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        *envelope_tail(segment_count="7"),
    ]
    claim = parse_835(assemble(segments)).transactions[0].claims[0]
    assert claim.status == ClaimStatus.TERTIARY


# --- MIA / MOA ---------------------------------------------------------------


def test_mia_present() -> None:
    segments = [
        *envelope_head(),
        *claim_segments(),
        seg(ELEMENT_SEP, "MIA", "5", "1", "2000.00"),
        plb_segment(),
        *envelope_tail(segment_count="18"),
    ]
    claim = parse_835(assemble(segments)).transactions[0].claims[0]
    assert claim.mia is not None


def test_moa_present() -> None:
    segments = [
        *envelope_head(),
        *claim_segments(),
        seg(ELEMENT_SEP, "MOA", "", "", "", "", "", "", "", "A1"),
        plb_segment(),
        *envelope_tail(segment_count="18"),
    ]
    claim = parse_835(assemble(segments)).transactions[0].claims[0]
    assert claim.moa is not None


# --- GATE-REQUIRED: the six malformed-input cases ----------------------------


def test_malformed_1_mixed_line_endings_unix() -> None:
    result = parse_835(malformed_mixed_line_endings_unix())
    assert result.errors == ()
    assert len(result.transactions) == 1


def test_malformed_2_mixed_line_endings_windows() -> None:
    result = parse_835(malformed_mixed_line_endings_windows())
    assert result.errors == ()
    assert len(result.transactions) == 1


def test_malformed_3_bare_single_line_no_breaks() -> None:
    result = parse_835(malformed_bare_single_line())
    assert result.errors == ()
    assert len(result.transactions) == 1


def test_malformed_4_missing_isa_header_fatal_no_exception() -> None:
    result = parse_835(malformed_missing_isa())
    assert result.transactions == ()
    assert len(result.errors) >= 1
    assert result.errors[0].severity == "error"


def test_malformed_5_bad_clp_quarantined_no_raw_content_leak() -> None:
    result = parse_835(malformed_clp())
    assert len(result.errors) >= 1
    issue = next(e for e in result.errors if e.segment_tag == "CLP")
    assert "CLAIM0004" not in issue.reason


def test_malformed_6_truncated_file_returns_partial_claims() -> None:
    result = parse_835(malformed_truncated_file())
    assert len(result.transactions) == 1
    assert len(result.transactions[0].claims) == 1
    assert len(result.errors) >= 1


# --- Additional resilience (not gate-required, still valuable) --------------


def test_dangling_cas_triplet_quarantined_without_losing_claim() -> None:
    result = parse_835(dangling_cas_triplet_835())
    assert len(result.transactions[0].claims) == 1
    assert len(result.errors) >= 1


def test_non_numeric_charge_quarantined() -> None:
    result = parse_835(non_numeric_charge_835())
    assert result.transactions[0].claims == ()
    assert len(result.errors) >= 1


def test_partial_batch_one_bad_claim_others_still_parse() -> None:
    result = parse_835(partial_batch_one_bad_claim())
    claims = result.transactions[0].claims
    assert len(claims) == 2
    assert {c.patient_control_number for c in claims} == {"CLAIM0001", "CLAIM0006"}
    assert len(result.errors) >= 1
