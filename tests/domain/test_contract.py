"""Tests for domain.contract (fee schedule and payment rule pricing).

Written before any implementation exists (contract.py currently only has typed
stub signatures/dataclasses, and price_claim/find_effective_contract raise
NotImplementedError) -- these are expected to fail until Step 2 implements the
module, per the approved test-first plan.

Uses the make_contract_version factory fixture from conftest.py so each test
only overrides the one rule it's exercising.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from domain.contract import (
    BilateralConvention,
    BilateralRule,
    CaseRateGroup,
    ClaimLineInput,
    MPPRRule,
    PricingMethod,
    PricingMethodUsed,
    find_effective_contract,
    price_claim,
)
from domain.money import Money, Rate
from tests.domain.conftest import IMPLANT_CODE, ContractFactory

# --- Base pricing methods -----------------------------------------------------


def test_fee_schedule_lookup_basic(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(fee_schedule={"99213": Money("100.00")})
    line = ClaimLineInput("99213", (), None, Money("100.00"), None, Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed == Money("100.00")
    assert priced.lines[0].pricing_method_used == PricingMethodUsed.FEE_SCHEDULE


def test_percent_of_charge_pricing(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(
        default_pricing_method=PricingMethod.PERCENT_OF_CHARGE,
        percent_of_charge_rate=Rate.percent(Decimal("60")),
        fee_schedule={},
    )
    line = ClaimLineInput("99213", (), None, Money("100.00"), None, Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed == Money("60.00")
    assert priced.lines[0].pricing_method_used == PricingMethodUsed.PERCENT_OF_CHARGE


# --- Case rate ---------------------------------------------------------------


def test_case_rate_flat_regardless_of_individual_charges(
    make_contract_version: ContractFactory,
) -> None:
    case_rate_groups = (
        CaseRateGroup(
            trigger_procedure_codes=frozenset({"CASERATE1"}),
            flat_rate=Money("100.00"),
            includes_implants=False,
        ),
    )
    contract = make_contract_version(
        fee_schedule={
            "CASERATE1": Money("500.00"),
            "99213": Money("300.00"),
            "99214": Money("200.00"),
        },
        case_rate_groups=case_rate_groups,
    )
    lines = [
        ClaimLineInput("CASERATE1", (), None, Money("500.00"), None, Decimal("1")),
        ClaimLineInput("99213", (), None, Money("300.00"), None, Decimal("1")),
        ClaimLineInput("99214", (), None, Money("200.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    assert sum((p.allowed for p in priced.lines if p.allowed is not None), Money.zero()) == Money(
        "100.00"
    )
    assert all(p.pricing_method_used == PricingMethodUsed.CASE_RATE_ALLOCATED for p in priced.lines)


# --- MPPR ranking --------------------------------------------------------------


def test_mppr_ranking_highest_value_first(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(
        fee_schedule={"A": Money("300.00"), "B": Money("200.00"), "C": Money("100.00")}
    )
    # Deliberately out of value order to prove ranking is by allowed value, not input order.
    lines = [
        ClaimLineInput("C", (), None, Money("100.00"), None, Decimal("1")),
        ClaimLineInput("A", (), None, Money("300.00"), None, Decimal("1")),
        ClaimLineInput("B", (), None, Money("200.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    by_code = {p.procedure_code: p for p in priced.lines}
    assert by_code["A"].allowed == Money("300.00") and by_code["A"].mppr_rank == 1
    assert by_code["B"].allowed == Money("100.00") and by_code["B"].mppr_rank == 2
    assert by_code["C"].allowed == Money("25.00") and by_code["C"].mppr_rank == 3


def test_mppr_exempt_codes_skip_ranking(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(
        fee_schedule={"A": Money("300.00"), "B": Money("200.00"), "C": Money("100.00")},
        mppr_rule=MPPRRule(
            enabled=True,
            second_procedure_rate=Rate.percent(Decimal("50")),
            third_and_subsequent_rate=Rate.percent(Decimal("25")),
            exempt_codes=frozenset({"C"}),
        ),
    )
    lines = [
        ClaimLineInput("C", (), None, Money("100.00"), None, Decimal("1")),
        ClaimLineInput("A", (), None, Money("300.00"), None, Decimal("1")),
        ClaimLineInput("B", (), None, Money("200.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    by_code = {p.procedure_code: p for p in priced.lines}
    assert by_code["C"].allowed == Money("100.00") and by_code["C"].mppr_rank is None
    assert by_code["A"].allowed == Money("300.00") and by_code["A"].mppr_rank == 1
    assert by_code["B"].allowed == Money("100.00") and by_code["B"].mppr_rank == 2


def test_payer_override_disables_mppr_all_lines_at_full_rate(
    make_contract_version: ContractFactory,
) -> None:
    contract = make_contract_version(
        fee_schedule={"A": Money("300.00"), "B": Money("200.00")},
        mppr_rule=MPPRRule(
            enabled=False,
            second_procedure_rate=Rate.percent(Decimal("50")),
            third_and_subsequent_rate=Rate.percent(Decimal("25")),
            exempt_codes=frozenset(),
        ),
    )
    lines = [
        ClaimLineInput("A", (), None, Money("300.00"), None, Decimal("1")),
        ClaimLineInput("B", (), None, Money("200.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    by_code = {p.procedure_code: p for p in priced.lines}
    assert by_code["A"].allowed == Money("300.00") and by_code["A"].mppr_rank is None
    assert by_code["B"].allowed == Money("200.00") and by_code["B"].mppr_rank is None


# --- Bilateral modifier 50 -----------------------------------------------------


def test_bilateral_modifier_150_percent_single_line(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(fee_schedule={"27447": Money("1000.00")})
    line = ClaimLineInput("27447", ("50",), None, Money("1000.00"), None, Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed == Money("1500.00")
    assert priced.lines[0].pricing_method_used == PricingMethodUsed.BILATERAL


def test_bilateral_disabled_via_payer_override(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(
        fee_schedule={"27447": Money("1000.00")},
        bilateral_rule=BilateralRule(
            enabled=False,
            total_rate=Rate.percent(Decimal("150")),
            convention=BilateralConvention.SINGLE_LINE_150_PCT,
        ),
    )
    line = ClaimLineInput("27447", ("50",), None, Money("1000.00"), None, Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed == Money("1000.00")


# --- Assistant surgeon ---------------------------------------------------------


def test_assistant_surgeon_modifier_80_reduced_rate(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(fee_schedule={"27447": Money("1000.00")})
    lines = [
        ClaimLineInput("27447", (), None, Money("1000.00"), None, Decimal("1")),
        ClaimLineInput("27447", ("80",), None, Money("1000.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    assert priced.lines[0].allowed == Money("1000.00")
    assert priced.lines[1].allowed == Money("160.00")
    assert priced.lines[1].pricing_method_used == PricingMethodUsed.ASSISTANT_SURGEON


def test_assistant_surgeon_modifier_as_recognized(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(fee_schedule={"27447": Money("1000.00")})
    lines = [
        ClaimLineInput("27447", (), None, Money("1000.00"), None, Decimal("1")),
        ClaimLineInput("27447", ("AS",), None, Money("1000.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    assert priced.lines[1].allowed == Money("160.00")
    assert priced.lines[1].pricing_method_used == PricingMethodUsed.ASSISTANT_SURGEON


# --- Implant carve-out ---------------------------------------------------------


def test_implant_priced_at_invoice_cost_regardless_of_fee_schedule(
    make_contract_version: ContractFactory,
) -> None:
    contract = make_contract_version(fee_schedule={})
    line = ClaimLineInput(IMPLANT_CODE, (), None, Money("2000.00"), Money("1750.00"), Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed == Money("1750.00")
    assert priced.lines[0].pricing_method_used == PricingMethodUsed.INVOICE_COST_IMPLANT


def test_implant_excluded_from_mppr_ranking(make_contract_version: ContractFactory) -> None:
    contract = make_contract_version(fee_schedule={"A": Money("300.00"), "B": Money("200.00")})
    lines = [
        ClaimLineInput(IMPLANT_CODE, (), None, Money("2000.00"), Money("1750.00"), Decimal("1")),
        ClaimLineInput("A", (), None, Money("300.00"), None, Decimal("1")),
        ClaimLineInput("B", (), None, Money("200.00"), None, Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    by_code = {p.procedure_code: p for p in priced.lines}
    assert by_code[IMPLANT_CODE].mppr_rank is None
    assert by_code["A"].mppr_rank == 1 and by_code["A"].allowed == Money("300.00")
    assert by_code["B"].mppr_rank == 2 and by_code["B"].allowed == Money("100.00")


def test_implant_missing_invoice_cost_is_unpriced_not_zero(
    make_contract_version: ContractFactory,
) -> None:
    contract = make_contract_version(fee_schedule={})
    line = ClaimLineInput(IMPLANT_CODE, (), None, Money("2000.00"), None, Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed is None
    assert priced.lines[0].pricing_method_used == PricingMethodUsed.UNPRICED


# --- Unpriced codes ------------------------------------------------------------


def test_unpriced_code_no_fee_schedule_no_percent_of_charge_fallback(
    make_contract_version: ContractFactory,
) -> None:
    contract = make_contract_version(fee_schedule={}, percent_of_charge_rate=None)
    line = ClaimLineInput("ZZZZZ", (), None, Money("500.00"), None, Decimal("1"))
    priced = price_claim([line], contract)
    assert priced.lines[0].allowed is None
    assert priced.lines[0].pricing_method_used == PricingMethodUsed.UNPRICED


# --- Precedence: case rate wins, implant still separately carved out --------


def test_case_rate_wins_but_implant_still_carved_out(
    make_contract_version: ContractFactory,
) -> None:
    case_rate_groups = (
        CaseRateGroup(
            trigger_procedure_codes=frozenset({"27447"}),
            flat_rate=Money("2000.00"),
            includes_implants=False,
        ),
    )
    contract = make_contract_version(
        fee_schedule={"27447": Money("1000.00")}, case_rate_groups=case_rate_groups
    )
    lines = [
        ClaimLineInput("27447", ("50",), None, Money("1000.00"), None, Decimal("1")),
        ClaimLineInput(IMPLANT_CODE, (), None, Money("500.00"), Money("450.00"), Decimal("1")),
    ]
    priced = price_claim(lines, contract)
    by_code = {p.procedure_code: p for p in priced.lines}
    assert by_code[IMPLANT_CODE].allowed == Money("450.00")
    assert by_code[IMPLANT_CODE].pricing_method_used == PricingMethodUsed.INVOICE_COST_IMPLANT
    assert by_code["27447"].allowed == Money("2000.00")
    assert by_code["27447"].pricing_method_used == PricingMethodUsed.CASE_RATE_ALLOCATED


# --- find_effective_contract ---------------------------------------------------


def test_find_effective_contract_selects_correct_version(
    make_contract_version: ContractFactory,
) -> None:
    v1 = make_contract_version(effective_from=date(2022, 1, 1), effective_to=date(2022, 12, 31))
    v2 = make_contract_version(effective_from=date(2023, 1, 1), effective_to=date(2023, 12, 31))
    v3 = make_contract_version(effective_from=date(2024, 1, 1), effective_to=None)
    result = find_effective_contract("PAYER1", date(2023, 6, 15), [v1, v2, v3])
    assert result is v2


def test_find_effective_contract_returns_none_when_no_version_covers_date(
    make_contract_version: ContractFactory,
) -> None:
    v1 = make_contract_version(effective_from=date(2022, 1, 1), effective_to=date(2022, 12, 31))
    result = find_effective_contract("PAYER1", date(2021, 1, 1), [v1])
    assert result is None


def test_find_effective_contract_open_ended_version_matches_all_after_start(
    make_contract_version: ContractFactory,
) -> None:
    v3 = make_contract_version(effective_from=date(2024, 1, 1), effective_to=None)
    result = find_effective_contract("PAYER1", date(2030, 1, 1), [v3])
    assert result is v3


def test_find_effective_contract_filters_by_payer_id(
    make_contract_version: ContractFactory,
) -> None:
    v1 = make_contract_version(payer_id="PAYER1", effective_from=date(2023, 1, 1))
    v2 = make_contract_version(payer_id="PAYER2", effective_from=date(2023, 1, 1))
    result = find_effective_contract("PAYER2", date(2023, 6, 1), [v1, v2])
    assert result is v2
