"""Tests for domain.variance.evaluate_claim.

Written before any implementation exists (variance.py currently only has
typed stub signatures/dataclasses, and evaluate_claim raises
NotImplementedError) -- these are expected to fail until Step 2 implements
the module, per the approved test-first plan.

Written to hit every branch of the classification chain, since 100% branch
coverage on this file is a hard Phase 1 gate item.

Note: contract.PricedServiceLine carries a `raw_input: ClaimLineInput` field
(added during this test-writing pass) precisely so evaluate_claim can
re-price a claim against a *different* contract version for the
stale-fee-schedule check -- see test_stale_fee_schedule_detected_when_versions_provided.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from domain.contract import (
    ClaimLineInput,
    ContractVersion,
    PricedClaim,
    PricedServiceLine,
    PricingMethodUsed,
)
from domain.money import Money
from domain.variance import ActualServiceLine, RootCause, evaluate_claim
from tests.domain.conftest import IMPLANT_CODE, ContractFactory

DEFAULT_DATE = date(2023, 1, 10)


def cli(
    procedure_code: str,
    charge: Money,
    *,
    invoice_cost: Money | None = None,
    modifiers: tuple[str, ...] = (),
) -> ClaimLineInput:
    return ClaimLineInput(procedure_code, modifiers, None, charge, invoice_cost, Decimal("1"))


def psl(
    procedure_code: str,
    allowed: Money | None,
    method: PricingMethodUsed,
    *,
    rank: int | None = None,
    raw_input: ClaimLineInput | None = None,
) -> PricedServiceLine:
    return PricedServiceLine(
        procedure_code=procedure_code,
        allowed=allowed,
        pricing_method_used=method,
        mppr_rank=rank,
        raw_input=raw_input
        if raw_input is not None
        else cli(procedure_code, allowed or Money.zero()),
    )


def actual_line(
    procedure_code: str,
    allowed: Money,
    *,
    charge: Money | None = None,
    service_date: date = DEFAULT_DATE,
) -> ActualServiceLine:
    return ActualServiceLine(
        procedure_code=procedure_code,
        service_date=service_date,
        charge=charge if charge is not None else allowed,
        allowed=allowed,
    )


def priced_claim(
    lines: tuple[PricedServiceLine, ...], contract_version: ContractVersion
) -> PricedClaim:
    total = sum((line.allowed for line in lines if line.allowed is not None), Money.zero())
    return PricedClaim(lines=lines, total_allowed=total, contract_version=contract_version)


# --- Exact match / tolerance boundary ----------------------------------------


def test_exact_match_is_correct_no_variance(make_contract_version: ContractFactory) -> None:
    expected = priced_claim(
        (psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),), make_contract_version()
    )
    actual = (actual_line("99213", Money("100.00")),)
    findings = evaluate_claim("CLAIM1", expected, actual)
    assert len(findings) == 1
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE
    assert findings[0].shortfall == Money.zero()
    assert findings[0].expected_allowed == Money("100.00")
    assert findings[0].actual_allowed == Money("100.00")


def test_boundary_exactly_at_tolerance_is_still_correct(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),), make_contract_version()
    )
    actual = (actual_line("99213", Money("99.99")),)  # diff == 0.01 == tolerance
    findings = evaluate_claim("CLAIM2", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE


def test_just_beyond_tolerance_is_a_real_underpayment(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),), make_contract_version()
    )
    actual = (actual_line("99213", Money("99.98")),)  # diff == 0.02 > tolerance
    findings = evaluate_claim("CLAIM3", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.UNDETERMINED_VARIANCE
    assert f.shortfall == Money("0.02")


def test_overpayment_beyond_tolerance_still_produces_a_finding(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),), make_contract_version()
    )
    actual = (actual_line("99213", Money("100.05")),)  # payer overpaid
    findings = evaluate_claim("CLAIM4", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.UNDETERMINED_VARIANCE
    assert f.shortfall == Money("-0.05")


# --- Unpriced ------------------------------------------------------------------


def test_unpriced_code_regardless_of_actual_value(make_contract_version: ContractFactory) -> None:
    expected = priced_claim(
        (psl("ZZZZZ", None, PricingMethodUsed.UNPRICED),), make_contract_version()
    )
    actual = (actual_line("ZZZZZ", Money("500.00")),)
    findings = evaluate_claim("CLAIM5", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.UNPRICED_CODE
    assert f.expected_allowed is None


# --- MPPR ------------------------------------------------------------------


def test_mppr_not_applied_when_actual_is_full_rate(make_contract_version: ContractFactory) -> None:
    expected = priced_claim(
        (
            psl(
                "B",
                Money("100.00"),
                PricingMethodUsed.MPPR_REDUCED,
                rank=2,
                raw_input=cli("B", Money("200.00")),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("B", Money("200.00")),)  # payer paid full, unreduced rate
    findings = evaluate_claim("CLAIM6", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.MPPR_NOT_APPLIED
    assert f.shortfall == Money("-100.00")


def test_mppr_correctly_applied_is_correct_no_variance(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                "B",
                Money("100.00"),
                PricingMethodUsed.MPPR_REDUCED,
                rank=2,
                raw_input=cli("B", Money("200.00")),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("B", Money("100.00")),)
    findings = evaluate_claim("CLAIM7", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE


# --- Bilateral ---------------------------------------------------------------


def test_bilateral_modifier_dropped_when_actual_is_single_rate(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                "27447",
                Money("1500.00"),
                PricingMethodUsed.BILATERAL,
                raw_input=cli("27447", Money("1000.00"), modifiers=("50",)),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("27447", Money("1000.00")),)
    findings = evaluate_claim("CLAIM8", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.BILATERAL_MODIFIER_DROPPED
    assert f.shortfall == Money("500.00")


def test_bilateral_correctly_applied_is_correct_no_variance(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                "27447",
                Money("1500.00"),
                PricingMethodUsed.BILATERAL,
                raw_input=cli("27447", Money("1000.00"), modifiers=("50",)),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("27447", Money("1500.00")),)
    findings = evaluate_claim("CLAIM9", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE


# --- Implant carve-out ---------------------------------------------------------


def test_implant_not_carved_out_when_actual_looks_fee_scheduled(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                IMPLANT_CODE,
                Money("1750.00"),
                PricingMethodUsed.INVOICE_COST_IMPLANT,
                raw_input=cli(IMPLANT_CODE, Money("2000.00"), invoice_cost=Money("1750.00")),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line(IMPLANT_CODE, Money("1200.00")),)
    findings = evaluate_claim("CLAIM10", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.IMPLANT_NOT_CARVED_OUT
    assert f.shortfall == Money("550.00")


def test_implant_correctly_carved_out_is_correct_no_variance(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                IMPLANT_CODE,
                Money("1750.00"),
                PricingMethodUsed.INVOICE_COST_IMPLANT,
                raw_input=cli(IMPLANT_CODE, Money("2000.00"), invoice_cost=Money("1750.00")),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line(IMPLANT_CODE, Money("1750.00")),)
    findings = evaluate_claim("CLAIM11", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE


# --- Stop-loss / outlier (Phase 8) ----------------------------------------------


def test_stop_loss_not_applied_when_actual_is_flat_fee_scheduled(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                "27447",
                Money("300.00"),
                PricingMethodUsed.STOP_LOSS_OUTLIER,
                raw_input=cli("27447", Money("1000.00")),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("27447", Money("1000.00")),)  # payer paid flat fee schedule, not outlier
    findings = evaluate_claim("CLAIM12", expected, actual)
    f = findings[0]
    assert f.root_cause == RootCause.STOP_LOSS_NOT_APPLIED
    assert f.shortfall == Money("-700.00")


def test_stop_loss_correctly_applied_is_correct_no_variance(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                "27447",
                Money("300.00"),
                PricingMethodUsed.STOP_LOSS_OUTLIER,
                raw_input=cli("27447", Money("1000.00")),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("27447", Money("300.00")),)
    findings = evaluate_claim("CLAIM13", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE


# --- Stale fee schedule (both sides of the optional-parameter branch) -------


def test_stale_fee_schedule_detected_when_versions_provided(
    make_contract_version: ContractFactory,
) -> None:
    prior_version = make_contract_version(
        effective_from=date(2022, 1, 1),
        effective_to=date(2022, 12, 31),
        fee_schedule={"99213": Money("100.00")},
    )
    current_version = make_contract_version(
        effective_from=date(2023, 1, 1),
        effective_to=None,
        fee_schedule={"99213": Money("120.00")},
    )
    expected = priced_claim(
        (
            psl(
                "99213",
                Money("120.00"),
                PricingMethodUsed.FEE_SCHEDULE,
                raw_input=cli("99213", Money("120.00")),
            ),
        ),
        current_version,
    )
    actual = (actual_line("99213", Money("100.00")),)  # matches the PRIOR version's rate
    findings = evaluate_claim(
        "CLAIM12", expected, actual, all_contract_versions=[prior_version, current_version]
    )
    assert findings[0].root_cause == RootCause.STALE_FEE_SCHEDULE


def test_stale_fee_schedule_degrades_to_undetermined_when_versions_omitted(
    make_contract_version: ContractFactory,
) -> None:
    current_version = make_contract_version(
        effective_from=date(2023, 1, 1),
        effective_to=None,
        fee_schedule={"99213": Money("120.00")},
    )
    expected = priced_claim(
        (
            psl(
                "99213",
                Money("120.00"),
                PricingMethodUsed.FEE_SCHEDULE,
                raw_input=cli("99213", Money("120.00")),
            ),
        ),
        current_version,
    )
    actual = (actual_line("99213", Money("100.00")),)
    findings = evaluate_claim("CLAIM12B", expected, actual, all_contract_versions=None)
    assert findings[0].root_cause == RootCause.UNDETERMINED_VARIANCE


def test_stale_fee_schedule_check_present_but_no_prior_version_stays_undetermined(
    make_contract_version: ContractFactory,
) -> None:
    """all_contract_versions IS provided, but no version precedes the current one --
    exercises the "checked, found nothing" path distinctly from "never checked"."""
    current_version = make_contract_version(
        effective_from=date(2023, 1, 1),
        effective_to=None,
        fee_schedule={"99213": Money("120.00")},
    )
    expected = priced_claim(
        (
            psl(
                "99213",
                Money("120.00"),
                PricingMethodUsed.FEE_SCHEDULE,
                raw_input=cli("99213", Money("120.00")),
            ),
        ),
        current_version,
    )
    actual = (actual_line("99213", Money("100.00")),)
    findings = evaluate_claim("CLAIM12C", expected, actual, all_contract_versions=[current_version])
    assert findings[0].root_cause == RootCause.UNDETERMINED_VARIANCE


# --- Duplicate line (claim-level) --------------------------------------------


def test_duplicate_line_flagged_on_second_identical_occurrence(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),
            psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),
        ),
        make_contract_version(),
    )
    actual = (
        actual_line("99213", Money("100.00"), charge=Money("100.00"), service_date=DEFAULT_DATE),
        actual_line("99213", Money("100.00"), charge=Money("100.00"), service_date=DEFAULT_DATE),
    )
    findings = evaluate_claim("CLAIM13", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE
    assert findings[1].root_cause == RootCause.DUPLICATE_LINE


def test_same_procedure_different_date_or_charge_is_not_a_duplicate(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),
            psl("99213", Money("120.00"), PricingMethodUsed.FEE_SCHEDULE),
        ),
        make_contract_version(),
    )
    actual = (
        actual_line(
            "99213", Money("100.00"), charge=Money("100.00"), service_date=date(2023, 1, 10)
        ),
        actual_line(
            "99213", Money("120.00"), charge=Money("120.00"), service_date=date(2023, 1, 17)
        ),
    )
    findings = evaluate_claim("CLAIM14", expected, actual)
    assert findings[0].root_cause == RootCause.CORRECT_NO_VARIANCE
    assert findings[1].root_cause == RootCause.CORRECT_NO_VARIANCE


# --- Precedence: unpriced check runs before duplicate check -----------------


def test_unpriced_check_takes_precedence_over_duplicate_check(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl("ZZZZZ", None, PricingMethodUsed.UNPRICED),
            psl("ZZZZZ", None, PricingMethodUsed.UNPRICED),
        ),
        make_contract_version(),
    )
    actual = (
        actual_line("ZZZZZ", Money("500.00"), charge=Money("500.00"), service_date=DEFAULT_DATE),
        actual_line("ZZZZZ", Money("500.00"), charge=Money("500.00"), service_date=DEFAULT_DATE),
    )
    findings = evaluate_claim("CLAIM15", expected, actual)
    assert findings[0].root_cause == RootCause.UNPRICED_CODE
    assert findings[1].root_cause == RootCause.UNPRICED_CODE


# --- Evidence strings ----------------------------------------------------------


def test_evidence_string_contains_exact_money_substrings_and_no_names(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl(
                "27447",
                Money("1500.00"),
                PricingMethodUsed.BILATERAL,
                raw_input=cli("27447", Money("1000.00"), modifiers=("50",)),
            ),
        ),
        make_contract_version(),
    )
    actual = (actual_line("27447", Money("1000.00")),)
    findings = evaluate_claim("CLAIM16", expected, actual)
    evidence = findings[0].evidence
    assert "1500.00" in evidence
    assert "1000.00" in evidence
    for name_token in ("PATIENT", "MRN", "SSN"):
        assert name_token not in evidence.upper()


# --- Volume: N-line claim produces exactly N findings ------------------------


def test_evaluate_claim_on_three_line_claim_returns_three_findings(
    make_contract_version: ContractFactory,
) -> None:
    expected = priced_claim(
        (
            psl("99213", Money("100.00"), PricingMethodUsed.FEE_SCHEDULE),
            psl("99214", Money("150.00"), PricingMethodUsed.FEE_SCHEDULE),
            psl("ZZZZZ", None, PricingMethodUsed.UNPRICED),
        ),
        make_contract_version(),
    )
    actual = (
        actual_line("99213", Money("100.00"), service_date=date(2023, 1, 10)),
        actual_line("99214", Money("100.00"), service_date=date(2023, 1, 11)),
        actual_line("ZZZZZ", Money("50.00"), service_date=date(2023, 1, 12)),
    )
    findings = evaluate_claim("CLAIM17", expected, actual)
    assert len(findings) == 3
    assert [f.line_index for f in findings] == [0, 1, 2]
