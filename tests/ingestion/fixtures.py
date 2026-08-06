"""835 fixture composition for tests/ingestion/, built on top of the shared
segment builders in tests/domain/fixtures_x835.py rather than duplicating
them. The one thing those builders don't parameterize is the BPR total paid
(always hardcoded "500.00"), which ingestion's BPR-vs-claims+PLB
reconciliation needs to control directly -- so this module assembles the
envelope head itself, with the same segments in the same order, just with a
caller-supplied BPR amount.
"""

from __future__ import annotations

from datetime import date

from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
)
from domain.money import Money, Rate
from tests.domain.fixtures_x835 import (
    ELEMENT_SEP,
    SUB_ELEMENT_SEP,
    assemble,
    claim_segments,
    envelope_tail,
    isa_segment,
    plb_segment,
    seg,
)

TEST_PAYER = "TEST PAYER"


def make_contract_version(
    *,
    payer_id: str = TEST_PAYER,
    effective_from: date = date(2023, 1, 1),
    effective_to: date | None = None,
    fee_schedule: dict[str, Money] | None = None,
    implant_carveout_rule: ImplantCarveoutRule | None = None,
) -> ContractVersion:
    """A minimal fee-schedule contract with every optional rule disabled --
    ingestion tests only need pricing to run end to end deterministically,
    not to re-exercise MPPR/bilateral/implant logic (that's domain/'s job,
    already gated in Phase 1). `implant_carveout_rule` is the one
    exception: F-17 (docs/audit/REGISTER.md) needs a real, enabled rule to
    prove implant *detection* now works on the real ingestion path even
    though implant *pricing* deliberately still doesn't (no invoice-cost
    source exists yet -- see ingestion/plan.py's own docstring)."""
    return ContractVersion(
        payer_id=payer_id,
        effective_from=effective_from,
        effective_to=effective_to,
        default_pricing_method=PricingMethod.FEE_SCHEDULE,
        fee_schedule=fee_schedule if fee_schedule is not None else {"99213": Money("100.00")},
        percent_of_charge_rate=None,
        case_rate_groups=(),
        mppr_rule=MPPRRule(
            enabled=False,
            second_procedure_rate=Rate.percent(50),
            third_and_subsequent_rate=Rate.percent(25),
            exempt_codes=frozenset(),
        ),
        bilateral_rule=BilateralRule(
            enabled=False,
            total_rate=Rate.percent(150),
            convention=BilateralConvention.SINGLE_LINE_150_PCT,
        ),
        assistant_surgeon_rule=AssistantSurgeonRule(
            enabled=False,
            rate=Rate.percent(16),
            applicable_modifiers=frozenset(),
        ),
        implant_carveout_rule=implant_carveout_rule
        if implant_carveout_rule is not None
        else ImplantCarveoutRule(
            enabled=False,
            procedure_codes=frozenset(),
            revenue_codes=frozenset(),
        ),
    )


def envelope_head_with_bpr_total(bpr_total: str) -> list[str]:
    return [
        isa_segment(ELEMENT_SEP, SUB_ELEMENT_SEP),
        seg(
            ELEMENT_SEP,
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
        seg(ELEMENT_SEP, "ST", "835", "0001", "005010X221A1"),
        seg(
            ELEMENT_SEP,
            "BPR",
            "I",
            bpr_total,
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
        seg(ELEMENT_SEP, "TRN", "1", "TRACE0001", "1999999999"),
        seg(ELEMENT_SEP, "N1", "PR", "TEST PAYER"),
        seg(ELEMENT_SEP, "N1", "PE", "TEST ASC", "XX", "1999999999"),
    ]


def reconciling_835(bpr_total: str) -> str:
    """One claim (charge 500, CO 50+10, PR 20 -> paid 430) + one PLB of
    -10.00, with the BPR total set by the caller so tests can construct
    both a matching and a mismatching transaction from the same claim."""
    segments = [
        *envelope_head_with_bpr_total(bpr_total),
        *claim_segments(),
        plb_segment(),
        *envelope_tail(segment_count="17"),
    ]
    return assemble(segments)
