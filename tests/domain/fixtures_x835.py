"""Synthetic X12 835 fixture builders.

*.835/*.edi are gitignored (see .gitignore), so these fixtures live as Python
string literals rather than standalone files. All patient/provider names and
member IDs are deliberately synthetic and shaped to avoid SSN/MBI-like
patterns -- see scripts/hooks/block_phi.sh and CLAUDE.md rule 1: no real PHI.

Delimiters are never hardcoded at the call site: every builder takes the
element separator, sub-element separator, and segment terminator as
parameters, mirroring how domain.x835.parse_835 must read them positionally
from the ISA segment rather than assuming "*"/":"/"~".
"""

from __future__ import annotations

ELEMENT_SEP = "*"
SUB_ELEMENT_SEP = ":"
SEGMENT_TERM = "~"


def seg(element_sep: str, *fields: str) -> str:
    """Join fields into a segment body (no leading tag repetition, no terminator)."""
    return element_sep.join(fields)


def isa_segment(
    element_sep: str = ELEMENT_SEP,
    sub_element_sep: str = SUB_ELEMENT_SEP,
) -> str:
    """A fixed-width ISA segment (105 chars, no terminator).

    Real ISA elements are fixed-width regardless of the chosen delimiters,
    which is exactly why a parser can read the element separator at index 3,
    the sub-element separator at index 104, and the terminator at index 105
    without already knowing them.
    """
    fields = [
        "ISA",
        "00",
        "          ",
        "00",
        "          ",
        "ZZ",
        "SENDERID       ",
        "ZZ",
        "RECEIVERID     ",
        "230101",
        "1200",
        "^",
        "00501",
        "000000905",
        "0",
        "T",
        sub_element_sep,
    ]
    return element_sep.join(fields)


def assemble(segments: list[str], terminator: str = SEGMENT_TERM, line_break: str = "") -> str:
    """Join complete segment bodies with the terminator (and optional line break)."""
    return (terminator + line_break).join(segments) + terminator


def envelope_head(
    element_sep: str = ELEMENT_SEP, sub_element_sep: str = SUB_ELEMENT_SEP
) -> list[str]:
    return [
        isa_segment(element_sep, sub_element_sep),
        seg(
            element_sep,
            "GS",
            "HP",
            "SENDERID",
            "RECEIVERID",
            "20230101",
            "1200",
            "1",
            "X",
            "005010X221A1",
        ),
        seg(element_sep, "ST", "835", "0001", "005010X221A1"),
        seg(
            element_sep,
            "BPR",
            "I",
            "500.00",
            "C",
            "ACH",
            "CTX",
            "01",
            "999999999",
            "DA",
            "123456789",
            "1999999999",
            "",
            "",
            "01",
            "999999998",
            "DA",
            "987654321",
            "20230115",
        ),
        seg(element_sep, "TRN", "1", "TRACE0001", "1999999999"),
        seg(element_sep, "N1", "PR", "TEST PAYER"),
        seg(element_sep, "N1", "PE", "TEST ASC", "XX", "1999999999"),
    ]


def envelope_tail(element_sep: str = ELEMENT_SEP, segment_count: str = "17") -> list[str]:
    return [
        seg(element_sep, "SE", segment_count, "0001"),
        seg(element_sep, "GE", "1", "1"),
        seg(element_sep, "IEA", "1", "000000905"),
    ]


def claim_segments(
    element_sep: str = ELEMENT_SEP,
    sub_element_sep: str = SUB_ELEMENT_SEP,
    *,
    claim_status: str = "1",
    total_charge: str = "500.00",
    total_paid: str = "430.00",
    patient_resp: str = "20.00",
) -> list[str]:
    """One reasonably complete claim: CLP, NM1 x2, DTM x2, claim CAS, one SVC
    line with its own DTM/CAS/CAS/LQ, matching allowed=charge-CO, paid=allowed-PR."""
    return [
        seg(element_sep, "LX", "1"),
        seg(
            element_sep,
            "CLP",
            "CLAIM0001",
            claim_status,
            total_charge,
            total_paid,
            patient_resp,
            "12",
            "PAYERCTRL0001",
            "11",
        ),
        seg(
            element_sep,
            "NM1",
            "QC",
            "1",
            "PATIENT ONE",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000001",
        ),
        seg(
            element_sep,
            "NM1",
            "82",
            "2",
            "TEST RENDERING PROVIDER",
            "",
            "",
            "",
            "",
            "XX",
            "1999999999",
        ),
        seg(element_sep, "DTM", "232", "20230110"),
        seg(element_sep, "DTM", "233", "20230110"),
        seg(element_sep, "CAS", "CO", "45", "10.00"),
        seg(element_sep, "SVC", f"HC{sub_element_sep}99213", total_charge, total_paid, "", "1"),
        seg(element_sep, "DTM", "472", "20230110"),
        seg(element_sep, "CAS", "CO", "45", "50.00"),
        seg(element_sep, "CAS", "PR", "1", "20.00"),
        seg(element_sep, "LQ", "HE", "N362"),
    ]


def plb_segment(element_sep: str = ELEMENT_SEP) -> str:
    return seg(element_sep, "PLB", "1999999999", "20230101", "WO", "PLBREF0001", "-10.00")


def minimal_valid_835(
    element_sep: str = ELEMENT_SEP,
    sub_element_sep: str = SUB_ELEMENT_SEP,
    terminator: str = SEGMENT_TERM,
    line_break: str = "",
) -> str:
    """A single-claim, single-line, fully reconciling 835 (charge 500, CO 50 total,
    PR 20 -> allowed 450, paid 430; CLP04=430 so is_reconciled is True)."""
    segments = [
        *envelope_head(element_sep, sub_element_sep),
        *claim_segments(element_sep, sub_element_sep),
        plb_segment(element_sep),
        *envelope_tail(element_sep, segment_count="17"),
    ]
    return assemble(segments, terminator, line_break)


def custom_delimiters_835() -> str:
    """Same claim as minimal_valid_835 but with non-default ISA-declared delimiters
    (element sep '|', sub-element sep '^', terminator '\\')."""
    return minimal_valid_835(element_sep="|", sub_element_sep="^", terminator="\\")


def reversal_835() -> str:
    """CLP02 = 22 (reversal of previous payment); paid amount reported negative."""
    segments = [
        *envelope_head(),
        *claim_segments(
            claim_status="22", total_charge="500.00", total_paid="-430.00", patient_resp="0.00"
        ),
        plb_segment(),
        *envelope_tail(segment_count="17"),
    ]
    return assemble(segments)


def denial_835() -> str:
    """CLP02 = 4 (denied); paid 0.00, full charge adjusted off with CO."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0002",
            "4",
            "500.00",
            "0.00",
            "0.00",
            "12",
            "PAYERCTRL0002",
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT TWO",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000002",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "50", "500.00"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "0.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "50", "500.00"),
        *envelope_tail(segment_count="10"),
    ]
    return assemble(segments)


def secondary_payer_835() -> str:
    """CLP06 = secondary payer filing indicator with an OA-group claim-level CAS
    representing the primary payer's allowed reduction (retained, not subtracted)."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0003",
            "2",
            "500.00",
            "380.00",
            "20.00",
            "12",
            "PAYERCTRL0003",
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT THREE",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000003",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "CAS", "OA", "23", "100.00"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "0.00"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "380.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        seg(ELEMENT_SEP, "CAS", "PR", "1", "20.00"),
        *envelope_tail(segment_count="10"),
    ]
    return assemble(segments)


def mismatched_reconciliation_835() -> str:
    """CLP04 deliberately doesn't equal the sum of line-level paid amounts."""
    segments = [
        *envelope_head(),
        *claim_segments(total_charge="500.00", total_paid="999.99", patient_resp="20.00"),
        plb_segment(),
        *envelope_tail(segment_count="17"),
    ]
    return assemble(segments)


# --- Malformed-input fixtures (six required by the Phase 1 gate) ------------


def malformed_mixed_line_endings_unix() -> str:
    return minimal_valid_835(line_break="\n")


def malformed_mixed_line_endings_windows() -> str:
    return minimal_valid_835(line_break="\r\n")


def malformed_bare_single_line() -> str:
    return minimal_valid_835(line_break="")


def malformed_missing_isa() -> str:
    """ISA segment truncated to far fewer than 106 chars -- delimiters can't be read."""
    return "ISA*00*BADHEADER~GS*HP~"


def malformed_clp() -> str:
    """CLP segment with far too few elements (missing charge/paid/etc)."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(ELEMENT_SEP, "CLP", "CLAIM0004"),
        *envelope_tail(segment_count="5"),
    ]
    return assemble(segments)


def malformed_truncated_file() -> str:
    """Cuts off mid-claim, before SE/GE/IEA trailers."""
    segments = [
        *envelope_head(),
        *claim_segments(),
    ]
    return assemble(segments)


def partial_batch_one_bad_claim() -> str:
    """Three claims; the middle one is malformed. Claims 1 and 3 must still parse."""
    good_claim_1 = claim_segments()
    bad_claim = [
        seg(ELEMENT_SEP, "LX", "2"),
        seg(ELEMENT_SEP, "CLP", "CLAIM0005"),
    ]
    good_claim_3 = [
        seg(ELEMENT_SEP, "LX", "3"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0006",
            "1",
            "200.00",
            "180.00",
            "10.00",
            "12",
            "PAYERCTRL0006",
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT FOUR",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000004",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230112"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "10.00"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99214", "200.00", "180.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230112"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "10.00"),
        seg(ELEMENT_SEP, "CAS", "PR", "1", "10.00"),
    ]
    segments = [
        *envelope_head(),
        *good_claim_1,
        *bad_claim,
        *good_claim_3,
        *envelope_tail(segment_count="28"),
    ]
    return assemble(segments)


def dangling_cas_triplet_835() -> str:
    """A CAS segment with a group+reason but no amount for the final triplet slot."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0007",
            "1",
            "500.00",
            "430.00",
            "20.00",
            "12",
            "PAYERCTRL0007",
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT FIVE",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            "TESTMBR000005",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "430.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "50.00", "PR", "1"),
        *envelope_tail(segment_count="9"),
    ]
    return assemble(segments)


def non_numeric_charge_835() -> str:
    """CLP03 (total charge) is non-numeric."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0008",
            "1",
            "NOTANUMBER",
            "430.00",
            "20.00",
            "12",
            "PAYERCTRL0008",
            "11",
        ),
        *envelope_tail(segment_count="4"),
    ]
    return assemble(segments)
